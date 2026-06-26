"""
odrive_can.py  -  Moonraker Component for ODrive S1 CAN Bus Control
Mission-critical hardened version.

Place at: ~/moonraker/moonraker/components/odrive_can.py

Enable in moonraker.conf:
  [odrive_can]
  can_interface: can0
  can_bitrate: 1000000
  node_ids: 0,1,2,3
  node_directions: 1,-1,1,-1   # per-node sign; must match len(node_ids)
  step_per_unit: 0.024176
  homing_timeout: 30
  traj_vel_limit: 2.0           # turns/sec
  traj_accel: 3.0               # turns/sec²
  traj_decel: 3.0               # turns/sec²
  traj_inertia: 0.001           # kg·m² feedforward
  move_timeout: 120.0           # max seconds for any motion
  lock_timeout: 60.0            # seconds to wait for motion lock

All POST requests MUST include: -H "Content-Type: application/json"
"""

from __future__ import annotations
import asyncio
import struct
import time
import threading
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

try:
    import can
    CAN_AVAILABLE = True
except ImportError:
    CAN_AVAILABLE = False

# ODrive CAN Protocol Command IDs
CMD_HEARTBEAT = 0x001
CMD_ESTOP = 0x002
CMD_GET_ENCODER = 0x009
CMD_SET_CONTROLLER_MODE = 0x00B
CMD_SET_INPUT_POS = 0x00C
CMD_SET_INPUT_VEL = 0x00D
CMD_VBUS_VOLTAGE = 0x017
CMD_CLEAR_ERRORS = 0x018
CMD_SET_ABSOLUTE_POS = 0x019
CMD_SET_AXIS_STATE = 0x007
CMD_TRAJ_VEL_LIMIT = 0x011
CMD_TRAJ_ACCEL_DECEL = 0x012
CMD_TRAJ_INERTIA = 0x013

# BCM GPIO pin mapping for CM4 40-pin header.
PIN_MAPPING: Dict[str, int] = {
    "homing_stop": 2,
    "front_stop": 3,
    "reserve": 4,
    "F4": 17, "F3": 27, "F2": 22, "F1": 10,
    "B4": 9, "B3": 11, "B2": 5, "B1": 6,
}


def load_component(config: "ConfigHelper") -> "ODriveCAN":
    return ODriveCAN(config)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ODriveCAN:
    def __init__(self, config: "ConfigHelper") -> None:
        self.server = config.get_server()
        self.logger = logging.getLogger("moonraker.odrive_can")

        # ── Node IDs ─────────────────────────────────────────────────
        raw_ids = config.get("node_ids", "0,1,2,3")
        try:
            self.node_ids: List[int] = [int(x.strip())
                                        for x in raw_ids.split(",")]
        except ValueError:
            self.logger.error("Invalid node_ids — defaulting to [0,1,2,3]")
            self.node_ids = [0, 1, 2, 3]

        # Per-node direction: +1 or -1. Default: alternate even=+1, odd=-1.
        default_dirs = ",".join(
            str(1 if i % 2 == 0 else -1) for i in range(len(self.node_ids))
        )
        raw_dirs = config.get("node_directions", default_dirs)
        try:
            dirs = [int(x.strip()) for x in raw_dirs.split(",")]
            if len(dirs) != len(self.node_ids):
                raise ValueError("length mismatch")
            self.node_dir: Dict[int, int] = {
                nid: d for nid, d in zip(self.node_ids, dirs)
            }
        except ValueError as e:
            self.logger.error(
                f"Invalid node_directions ({e}) — using alternating ±1"
            )
            self.node_dir = {
                nid: (1 if nid % 2 == 0 else -1) for nid in self.node_ids
            }

        # ── CAN / interface config ────────────────────────────────────
        self.can_iface: str = config.get("can_interface", "can0")
        self.can_bitrate: int = config.getint("can_bitrate", 1000000)

        # ── Motion config ─────────────────────────────────────────────
        self.step_per_unit: float = config.getfloat("step_per_unit", 0.024176)
        self.homing_timeout: float = config.getfloat("homing_timeout", 30.0)
        self.move_timeout: float = config.getfloat("move_timeout", 120.0)
        self.lock_timeout: float = config.getfloat("lock_timeout", 60.0)

        # ── Trajectory config ─────────────────────────────────────────
        self.traj_vel_limit: float = config.getfloat("traj_vel_limit", 2.0)
        self.traj_accel: float = config.getfloat("traj_accel", 3.0)
        self.traj_decel: float = config.getfloat("traj_decel", 3.0)
        self.traj_inertia: float = config.getfloat("traj_inertia", 0.001)

        # ── Internal state locks ──────────────────────────────────────
        self._state_lock = threading.Lock()   # protects robot/sensors/overrides
        self._node_lock = threading.Lock()   # protects node_data
        self.motion_lock = threading.Lock()   # serializes homing / move threads
        self._can_lock = threading.Lock()   # serializes CAN TX

        # ── CAN bus ───────────────────────────────────────────────────
        self.bus: Optional[Any] = None
        self._bus_error_count = 0
        self._can_reconnect_at = 0.0

        # ── Telemetry ─────────────────────────────────────────────────
        self.node_data: Dict[int, Dict[str, Any]] = {
            i: {
                "axis_error": 0,
                "axis_state": 0,
                "traj_done": 0,
                "pos_estimate": 0.0,
                "vel_estimate": 0.0,
                "vbus_voltage": 0.0,
                "last_update": 0.0,
            } for i in self.node_ids
        }

        # ── Sensors & overrides ───────────────────────────────────────
        self.sensors: Dict[str, bool] = {
            "front_stop": False, "homing_stop": False, "reserve": False,
            "F4": False, "F3": False, "F2": False, "F1": False,
            "B4": False, "B3": False, "B2": False, "B1": False,
        }
        self.overrides: Dict[str, bool] = {
            k: False for k in ["F4", "F3", "F2", "F1", "B4", "B3", "B2", "B1"]
        }

        # ── High-level robot state ────────────────────────────────────
        self.robot: Dict[str, Any] = {
            "is_homed": False,
            "homing_is_real": False,
            "em_stop": False,
            "motor_position_mm": 0.0,
            "command_status": "IDLE",
            "last_error": "",
        }

        # ── GPIO ──────────────────────────────────────────────────────
        self._pin_to_name = {v: k for k, v in PIN_MAPPING.items()}
        self._init_gpio()

        # ── CAN ───────────────────────────────────────────────────────
        self._connect_can()

        # ── Register endpoints ────────────────────────────────────────
        self.server.register_endpoint(
            "/machine/odrive/status", ["GET"], self._ep_status)
        self.server.register_endpoint(
            "/machine/odrive/sensors", ["GET", "POST"], self._ep_sensors)
        self.server.register_endpoint(
            "/machine/odrive/command", ["POST"], self._ep_command)

        # ── Background threads ────────────────────────────────────────
        self._rx_thread = threading.Thread(
            target=self._can_rx_supervisor, daemon=True, name="odrive_rx")
        self._rx_thread.start()

        self.logger.info(
            f"ODriveCAN loaded  iface={self.can_iface}  nodes={self.node_ids}  "
            f"dirs={self.node_dir}  vel={self.traj_vel_limit}  "
            f"accel={self.traj_accel}/{self.traj_decel}"
        )

    # ================================================================
    # CAN BUS
    # ================================================================

    def _connect_can(self) -> bool:
        if not CAN_AVAILABLE:
            self._set_error("python-can not installed")
            return False
        with self._can_lock:
            try:
                if self.bus is not None:
                    try:
                        self.bus.shutdown()
                    except Exception:
                        pass
                    self.bus = None
                self.bus = can.interface.Bus(
                    channel=self.can_iface,
                    interface="socketcan",
                    bitrate=self.can_bitrate,
                )
                self._bus_error_count = 0
                self.logger.info(f"CAN connected: {self.can_iface}")
                return True
            except Exception as e:
                self.bus = None
                self._bus_error_count += 1
                self._can_reconnect_at = time.time() + 5.0
                self._set_error(f"CAN connect failed: {e}")
                return False

    def _can_tx(self, node_id: int, cmd_id: int,
                data: bytes, rtr: bool = False) -> bool:
        """Thread-safe CAN transmit. Returns False silently on any error."""
        with self._can_lock:
            if self.bus is None:
                return False
            try:
                msg = can.Message(
                    arbitration_id=(node_id << 5) | cmd_id,
                    data=data,
                    is_extended_id=False,
                    is_remote_frame=rtr,
                )
                self.bus.send(msg, timeout=0.05)
                return True
            except can.CanError as e:
                self.logger.warning(
                    f"CAN TX error (node {node_id} cmd 0x{cmd_id:03X}): {e}")
                return False
            except Exception as e:
                self.logger.error(f"CAN TX unexpected error: {e}")
                return False

    def _can_tx_burst(
        self, frames: List[Tuple[int, int, bytes]], rtr: bool = False
    ) -> None:
        """Send multiple (node_id, cmd_id, data) frames under ONE lock acquisition.

        All frames are sent back-to-back with only CAN bus serialisation latency
        (~130 µs/frame at 1 Mbit) between them — the closest thing to simultaneous
        that a serial CAN bus allows. Per-frame errors are logged but never abort
        the remaining frames.
        """
        with self._can_lock:
            if self.bus is None:
                return
            for node_id, cmd_id, data in frames:
                try:
                    msg = can.Message(
                        arbitration_id=(node_id << 5) | cmd_id,
                        data=data,
                        is_extended_id=False,
                        is_remote_frame=rtr,
                    )
                    self.bus.send(msg, timeout=0.05)
                except can.CanError as e:
                    self.logger.warning(
                        f"CAN burst TX error (node {node_id} "
                        f"cmd 0x{cmd_id:03X}): {e}"
                    )
                except Exception as e:
                    self.logger.error(
                        f"CAN burst TX unexpected (node {node_id}): {e}"
                    )

    def _can_rx_supervisor(self) -> None:
        """Supervisor loop: restart the RX thread if it crashes."""
        while True:
            try:
                self._can_rx_loop()
            except Exception as e:
                self.logger.error(
                    f"CAN RX thread crashed: {e}. Restarting in 2s.")
                time.sleep(2.0)

    def _can_rx_loop(self) -> None:
        """Blocking CAN receive loop. Updates node_data on every received frame."""
        while True:
            if self.bus is None:
                if time.time() >= self._can_reconnect_at:
                    self.logger.info(
                        "CAN disconnected — attempting reconnect...")
                    self._connect_can()
                time.sleep(1.0)
                continue

            try:
                with self._can_lock:
                    bus_ref = self.bus
                if bus_ref is None:
                    time.sleep(0.5)
                    continue

                msg = bus_ref.recv(timeout=1.0)
                if msg is None:
                    continue

                nid = msg.arbitration_id >> 5
                cid = msg.arbitration_id & 0x1F

                if nid not in self.node_data:
                    continue

                with self._node_lock:
                    d = self.node_data[nid]
                    now = time.time()

                    if cid == CMD_HEARTBEAT and len(msg.data) >= 8:
                        err, state, _proc, traj = struct.unpack(
                            "<IBBBx", msg.data[:8])
                        d["axis_error"] = int(err)
                        d["axis_state"] = int(state)
                        d["traj_done"] = int(traj)
                        d["last_update"] = now

                    elif cid == CMD_GET_ENCODER and len(msg.data) >= 8:
                        pos, vel = struct.unpack("<ff", msg.data[:8])
                        d["pos_estimate"] = round(float(pos), 6)
                        d["vel_estimate"] = round(float(vel), 6)
                        d["last_update"] = now

                    elif cid == CMD_VBUS_VOLTAGE and len(msg.data) >= 8:
                        volt, _ = struct.unpack("<ff", msg.data[:8])
                        d["vbus_voltage"] = round(float(volt), 3)
                        d["last_update"] = now

            except struct.error as e:
                self.logger.warning(f"CAN RX unpack error: {e}")
            except can.CanError as e:
                self.logger.warning(f"CAN RX error: {e}")
                with self._can_lock:
                    self.bus = None
                self._can_reconnect_at = time.time() + 3.0
            except Exception as e:
                self.logger.error(f"CAN RX unexpected: {e}")
                time.sleep(0.1)

    # ================================================================
    # GPIO
    # ================================================================

    def _init_gpio(self) -> None:
        if not GPIO_AVAILABLE:
            self.logger.warning("RPi.GPIO not found — sensors in mock mode.")
            return
        try:
            GPIO.setmode(GPIO.BCM)
            for name, pin in PIN_MAPPING.items():
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                with self._state_lock:
                    self.sensors[name] = (GPIO.input(pin) == GPIO.LOW)
                GPIO.add_event_detect(
                    pin, GPIO.BOTH,
                    callback=self._gpio_cb,
                    bouncetime=50,
                )
            self.logger.info("GPIO interrupts registered.")
        except Exception as e:
            self.logger.error(f"GPIO init failed: {e}")

    def _gpio_cb(self, channel: int) -> None:
        """Hardware interrupt callback. Must never raise."""
        try:
            name = self._pin_to_name.get(channel)
            if name is None:
                return
            pressed = bool(GPIO.input(channel) == GPIO.LOW)
            with self._state_lock:
                self.sensors[name] = pressed
                status = self.robot["command_status"]
            if pressed and status in ("MOVING", "HOMING"):
                if name in ("front_stop", "homing_stop"):
                    self._estop(reason=f"Limit switch: {name}")
                elif not self.overrides.get(name, False):
                    self._estop(reason=f"Limit switch: {name}")
        except Exception as e:
            self.logger.error(f"GPIO callback error on channel {channel}: {e}")

    # ================================================================
    # Helpers
    # ================================================================

    def _set_error(self, msg: str) -> None:
        self.logger.error(msg)
        with self._state_lock:
            self.robot["last_error"] = msg

    def _estop(self, reason: str = "Manual ESTOP") -> None:
        self.logger.warning(f"ESTOP: {reason}")
        with self._state_lock:
            self.robot["em_stop"] = True
            self.robot["command_status"] = "ESTOPPED"
            self.robot["last_error"] = reason
        # Burst ESTOP to all nodes simultaneously
        self._can_tx_burst([(i, CMD_ESTOP, b"") for i in self.node_ids])

    def _clear_errors(self) -> None:
        self._can_tx_burst([(i, CMD_CLEAR_ERRORS, b"") for i in self.node_ids])
        time.sleep(0.05)
        with self._state_lock:
            self.robot["em_stop"] = False
            self.robot["last_error"] = ""
            self.robot["command_status"] = "IDLE"

    def _closed_loop(self) -> None:
        """Put all nodes into closed-loop control (state 8) simultaneously."""
        data = struct.pack("<I", 8)
        self._can_tx_burst([(i, CMD_SET_AXIS_STATE, data)
                           for i in self.node_ids])
        time.sleep(0.3)  # Give ODrives time to enter closed-loop state

    def _configure_trajectory(self) -> None:
        """Configure trajectory mode on all nodes.

        Sends each command TYPE to ALL nodes before moving to the next command,
        so every ODrive transitions states in lock-step.

        Order (each step is a burst across all nodes):
          1. SET_CONTROLLER_MODE → mode=3 (TRAJECTORY_CONTROL), input=5 (TRAP_TRAJ)
          2. TRAJ_VEL_LIMIT      → self.traj_vel_limit
          3. TRAJ_ACCEL_DECEL    → self.traj_accel / self.traj_decel
          4. TRAJ_INERTIA        → self.traj_inertia
        """
        mode_data = struct.pack("<II", 3, 5)
        vel_data = struct.pack("<f", self.traj_vel_limit)
        accel_data = struct.pack("<ff", self.traj_accel, self.traj_decel)
        inertia_data = struct.pack("<f", self.traj_inertia)

        self._can_tx_burst([(i, CMD_SET_CONTROLLER_MODE, mode_data)
                            for i in self.node_ids])
        self._can_tx_burst([(i, CMD_TRAJ_VEL_LIMIT, vel_data)
                            for i in self.node_ids])
        self._can_tx_burst([(i, CMD_TRAJ_ACCEL_DECEL, accel_data)
                            for i in self.node_ids])
        self._can_tx_burst([(i, CMD_TRAJ_INERTIA, inertia_data)
                            for i in self.node_ids])

    def _burst_set_input_pos(self, turns: float) -> None:
        """Send SET_INPUT_POS to all nodes simultaneously.

        Each node gets turns * node_dir[node_id] so that physically opposing
        wheels run in the correct direction for forward travel.
        """
        try:
            frames = [
                (i, CMD_SET_INPUT_POS,
                 struct.pack("<fhh", turns * self.node_dir[i], 0, 0))
                for i in self.node_ids
            ]
            self._can_tx_burst(frames)
        except struct.error as e:
            self.logger.error(f"burst_set_input_pos pack error: {e}")

    def _burst_set_abs_pos(self, turns: float) -> None:
        """Send SET_ABSOLUTE_POS to all nodes simultaneously."""
        try:
            frames = [
                (i, CMD_SET_ABSOLUTE_POS,
                 struct.pack("<f", turns * self.node_dir[i]))
                for i in self.node_ids
            ]
            self._can_tx_burst(frames)
        except struct.error as e:
            self.logger.error(f"burst_set_abs_pos pack error: {e}")

    # Keep single-node helpers for the per-node HTTP API
    def _set_input_pos(self, node_id: int, pos: float,
                       vel_ff: float = 0.0, torq_ff: float = 0.0) -> None:
        try:
            data = struct.pack("<fhh", pos,
                               int(vel_ff * 1000), int(torq_ff * 1000))
            self._can_tx(node_id, CMD_SET_INPUT_POS, data)
        except struct.error as e:
            self.logger.error(f"set_input_pos pack error node {node_id}: {e}")

    def _set_abs_pos(self, node_id: int, pos: float) -> None:
        try:
            self._can_tx(node_id, CMD_SET_ABSOLUTE_POS, struct.pack("<f", pos))
        except struct.error as e:
            self.logger.error(f"set_abs_pos pack error node {node_id}: {e}")

    def _reset_traj_done(self) -> None:
        with self._node_lock:
            for i in self.node_ids:
                self.node_data[i]["traj_done"] = 0

    def _wait_traj(self, timeout: float = 10.0) -> bool:
        """Wait for traj_done on all nodes that have actually reported in."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._node_lock:
                # Skip nodes that have never sent a heartbeat (offline / wrong
                # ID)
                active = [i for i in self.node_ids
                          if self.node_data[i]["last_update"] > 0]
                if not active:
                    time.sleep(0.05)
                    continue
                done = all(self.node_data[i]["traj_done"] == 1 for i in active)
            if done:
                return True
            time.sleep(0.05)
        return False

    def _sensors_block(self, forward: bool) -> bool:
        with self._state_lock:
            if self.robot["em_stop"]:
                return True
            key_stop = "front_stop" if forward else "homing_stop"
            if self.sensors[key_stop]:
                return True
            cols = (
                "F1",
                "F2",
                "F3",
                "F4") if forward else (
                "B1",
                "B2",
                "B3",
                "B4")
            for s in cols:
                if self.sensors[s] and not self.overrides.get(s, False):
                    return True
        return False

    def _acquire_motion_lock(self) -> bool:
        acquired = self.motion_lock.acquire(timeout=self.lock_timeout)
        if not acquired:
            self._set_error("Motion lock timeout — another motion is stuck.")
        return acquired

    # ================================================================
    # Motion Threads
    # ================================================================

    def _run_home(self) -> None:
        if not self._acquire_motion_lock():
            return
        try:
            with self._state_lock:
                self.robot["command_status"] = "HOMING"
                self.robot["last_error"] = ""

            if self.bus is None:
                self._set_error("Cannot home: CAN bus not connected")
                return

            self._clear_errors()
            self._closed_loop()
            self._configure_trajectory()

            deadline = time.time() + self.move_timeout

            # Step 1: drive to homing switch
            if not self.sensors.get("homing_stop", False):
                self._burst_set_input_pos(-1000.0)   # large negative target

                while not self.sensors.get("homing_stop", False):
                    if time.time() > deadline:
                        self._estop("Homing timeout — switch not found")
                        with self._state_lock:
                            self.robot["command_status"] = "ERROR_TIMEOUT"
                        return
                    with self._state_lock:
                        if self.robot["em_stop"]:
                            return
                    if self._sensors_block(forward=False):
                        self._estop("Sensor blocked during homing")
                        return
                    time.sleep(0.05)

            # Step 2: stop and zero
            self._estop("Homing switch reached — stopping")
            time.sleep(0.1)
            self._clear_errors()
            self._closed_loop()
            self._burst_set_abs_pos(0.0)

            # Step 3: nudge forward off the switch
            offset = 0.0
            t0 = time.time()
            while self.sensors.get("homing_stop", False):
                if time.time() - t0 > self.homing_timeout:
                    self._estop("Homing timeout — stuck on switch")
                    with self._state_lock:
                        self.robot["command_status"] = "ERROR_TIMEOUT"
                    return
                offset += 0.02
                self._burst_set_input_pos(offset)
                self._wait_traj(1.0)

            # Step 4: nudge back onto switch edge
            t0 = time.time()
            while not self.sensors.get("homing_stop", False):
                if time.time() - t0 > self.homing_timeout:
                    self._estop("Homing timeout — lost switch edge")
                    with self._state_lock:
                        self.robot["command_status"] = "ERROR_TIMEOUT"
                    return
                offset -= 0.01
                self._burst_set_input_pos(offset)
                self._wait_traj(1.0)

            # Step 5: declare home
            self._burst_set_abs_pos(0.0)
            with self._state_lock:
                self.robot.update({
                    "is_homed": True,
                    "homing_is_real": True,
                    "motor_position_mm": 0.0,
                    "command_status": "IDLE",
                    "last_error": "",
                })
            self.logger.info("Homing complete.")

        except Exception as e:
            self._set_error(f"Homing crashed: {e}")
            try:
                self._estop(f"Homing exception: {e}")
            except Exception:
                pass
            with self._state_lock:
                self.robot["command_status"] = "ERROR"
        finally:
            self.motion_lock.release()

    def _run_move(self, distance_mm: float) -> None:
        if not self._acquire_motion_lock():
            return
        try:
            with self._state_lock:
                is_homed = self.robot["is_homed"]

            if not is_homed:
                with self._state_lock:
                    self.robot["command_status"] = "ERROR_NOT_HOMED"
                    self.robot["last_error"] = "Not homed — send H0 or G28 first"
                return

            if self.bus is None:
                self._set_error("Cannot move: CAN bus not connected")
                return

            with self._state_lock:
                self.robot["command_status"] = "MOVING"
                self.robot["last_error"] = ""
                cur_pos = self.robot["motor_position_mm"]

            # Clear errors first — also resets em_stop so G0 after RESET
            # auto-recovers without a manual CLEAR_ERRORS call.
            self._clear_errors()
            self._closed_loop()
            self._configure_trajectory()

            fwd = distance_mm > cur_pos
            if self._sensors_block(fwd):
                with self._state_lock:
                    self.robot["command_status"] = "ERROR_BLOCKED"
                    self.robot["last_error"] = "Sensor blocked at move start"
                return

            turns = distance_mm * self.step_per_unit
            self._reset_traj_done()
            # Send position to all nodes simultaneously under one lock
            # acquisition
            self._burst_set_input_pos(turns)

            deadline = time.time() + self.move_timeout
            while True:
                if time.time() > deadline:
                    self._estop("Move timeout")
                    with self._state_lock:
                        self.robot["command_status"] = "ERROR_TIMEOUT"
                    return
                with self._state_lock:
                    if self.robot["em_stop"]:
                        return
                if self._wait_traj(timeout=0.1):
                    with self._state_lock:
                        self.robot["motor_position_mm"] = distance_mm
                        self.robot["command_status"] = "IDLE"
                    return

        except Exception as e:
            self._set_error(f"Move crashed: {e}")
            try:
                self._estop(f"Move exception: {e}")
            except Exception:
                pass
            with self._state_lock:
                self.robot["command_status"] = "ERROR"
        finally:
            self.motion_lock.release()

    # ================================================================
    # HTTP Endpoint Handlers
    # ================================================================

    def _safe_snapshot(self) -> Dict:
        try:
            with self._node_lock:
                od = {str(k): dict(v) for k, v in self.node_data.items()}
            with self._state_lock:
                rob = dict(self.robot)
                sens = dict(self.sensors)
                over = dict(self.overrides)
            return {
                "can_connected": self.bus is not None,
                "odrives": od,
                "robot": rob,
                "sensors": sens,
                "overrides": over,
                "config": {
                    "node_ids": self.node_ids,
                    "node_dir": self.node_dir,
                    "traj_vel_limit": self.traj_vel_limit,
                    "traj_accel": self.traj_accel,
                    "traj_decel": self.traj_decel,
                    "traj_inertia": self.traj_inertia,
                    "step_per_unit": self.step_per_unit,
                },
            }
        except Exception as e:
            self.logger.error(f"State snapshot error: {e}")
            return {"error": f"State read error: {e}"}

    def _poll_telemetry(self) -> None:
        try:
            frames = []
            for i in self.node_ids:
                frames.append((i, CMD_GET_ENCODER, b""))
                frames.append((i, CMD_VBUS_VOLTAGE, b""))
            self._can_tx_burst(frames, rtr=True)
        except Exception as e:
            self.logger.warning(f"Telemetry poll error: {e}")

    async def _ep_status(self, web_request) -> Dict:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._poll_telemetry)
            await asyncio.sleep(0.07)
            return self._safe_snapshot()
        except Exception as e:
            self.logger.error(f"_ep_status error: {e}")
            return {"error": str(e)}

    async def _ep_sensors(self, web_request) -> Dict:
        try:
            action = web_request.get_action()
            if action == "POST":
                args = web_request.get_args()
                with self._state_lock:
                    for k, v in args.items():
                        if k in self.sensors:
                            self.sensors[k] = bool(v)
            with self._state_lock:
                return dict(self.sensors)
        except Exception as e:
            self.logger.error(f"_ep_sensors error: {e}")
            return {"error": str(e)}

    async def _ep_command(self, web_request) -> Dict:
        try:
            return await self._dispatch_command(web_request)
        except Exception as e:
            self.logger.error(f"_ep_command unhandled error: {e}")
            return {"error": f"Internal error: {e}"}

    async def _dispatch_command(self, web_request) -> Dict:
        action = web_request.get_str("action", "")
        loop = asyncio.get_event_loop()

        # ── High-level actions ────────────────────────────────────────
        if action == "G28":
            with self._state_lock:
                status = self.robot["command_status"]
            if status in ("HOMING", "MOVING"):
                return {"error": f"Cannot home: robot is currently {status}"}
            loop.run_in_executor(None, self._run_home)
            return {"status": "Homing started"}

        if action == "G0":
            with self._state_lock:
                status = self.robot["command_status"]
            if status in ("HOMING", "MOVING"):
                return {"error": f"Cannot move: robot is currently {status}"}
            try:
                dist = web_request.get_float("distance", 0.0)
            except Exception:
                return {
                    "error": "Invalid 'distance' parameter (must be a number)"}
            loop.run_in_executor(None, self._run_move, dist)
            return {"status": "Move started", "distance": dist}

        if action == "H0":
            try:
                dist = web_request.get_float("distance", 0.0)
            except Exception:
                return {
                    "error": "Invalid 'distance' parameter (must be a number)"}
            turns = dist * self.step_per_unit
            self._burst_set_abs_pos(turns)
            with self._state_lock:
                self.robot.update({
                    "motor_position_mm": dist,
                    "is_homed": True,
                    "homing_is_real": False,
                    "command_status": "IDLE",
                    "last_error": "",
                })
            return {"status": "Position overridden", "distance": dist}

        if action == "RESET":
            self._estop(reason="API RESET command")
            return {"status": "Estop triggered"}

        if action == "CLEAR_ERRORS":
            with self._state_lock:
                status = self.robot["command_status"]
            if status in ("HOMING", "MOVING"):
                return {"error": "Cannot clear errors while robot is in motion"}
            self._clear_errors()
            return {"status": "Errors cleared"}

        if action == "ABLE":
            sensor = web_request.get_str("sensor", "")
            if sensor not in self.overrides:
                return {"error": f"Unknown sensor: '{sensor}'. "
                        f"Valid: {list(self.overrides.keys())}"}
            with self._state_lock:
                self.overrides[sensor] = True
            return {"sensor": sensor, "override": True}

        if action == "DISS":
            sensor = web_request.get_str("sensor", "")
            if sensor not in self.overrides:
                return {"error": f"Unknown sensor: '{sensor}'. "
                        f"Valid: {list(self.overrides.keys())}"}
            with self._state_lock:
                self.overrides[sensor] = False
            return {"sensor": sensor, "override": False}

        if action == "DISS_ALL":
            with self._state_lock:
                for k in self.overrides:
                    self.overrides[k] = False
            return {"status": "All overrides disabled"}

        # ── Per-node raw commands ─────────────────────────────────────
        # Example: {"0": {"state": 8, "position": 10.0}, "1": {"velocity": 2.5}}
        args = web_request.get_args()
        results: Dict = {}
        found_node = False

        for nid_str, cmds in args.items():
            if nid_str == "action":
                continue
            if not isinstance(cmds, dict):
                continue
            try:
                nid = int(nid_str)
            except ValueError:
                results[nid_str] = "error: not a valid node ID integer"
                continue
            if nid not in self.node_ids:
                results[nid_str] = (
                    f"error: node {nid} not in configured "
                    f"node_ids {self.node_ids}"
                )
                continue

            found_node = True
            results[nid_str] = {}

            try:
                if cmds.get("clear_errors"):
                    ok = self._can_tx(nid, CMD_CLEAR_ERRORS, b"")
                    results[nid_str]["clear_errors"] = "sent" if ok else "failed"

                if "state" in cmds:
                    state_val = _safe_int(cmds["state"])
                    ok = self._can_tx(nid, CMD_SET_AXIS_STATE,
                                      struct.pack("<I", state_val))
                    results[nid_str]["state"] = "sent" if ok else "failed"

                if "position" in cmds:
                    pos = _safe_float(cmds["position"])
                    vff = _safe_float(cmds.get("velocity_ff", 0.0))
                    tff = _safe_float(cmds.get("torque_ff", 0.0))
                    self._can_tx(nid, CMD_SET_CONTROLLER_MODE,
                                 struct.pack("<II", 3, 1))
                    ok = self._can_tx(
                        nid, CMD_SET_INPUT_POS,
                        struct.pack("<fhh", pos,
                                    int(vff * 1000), int(tff * 1000)))
                    results[nid_str]["position"] = "sent" if ok else "failed"

                elif "velocity" in cmds:
                    vel = _safe_float(cmds["velocity"])
                    tff = _safe_float(cmds.get("torque_ff", 0.0))
                    self._can_tx(nid, CMD_SET_CONTROLLER_MODE,
                                 struct.pack("<II", 2, 1))
                    ok = self._can_tx(nid, CMD_SET_INPUT_VEL,
                                      struct.pack("<ff", vel, tff))
                    results[nid_str]["velocity"] = "sent" if ok else "failed"

            except struct.error as e:
                results[nid_str] = f"error: struct pack failed: {e}"
            except Exception as e:
                results[nid_str] = f"error: {e}"

        if found_node:
            return {"success": True, "results": results}

        return {
            "error": "Unknown action or missing node dictionary",
            "valid_actions": [
                "G28", "G0 (+ distance)", "H0 (+ distance)",
                "RESET", "CLEAR_ERRORS",
                "ABLE (+ sensor)", "DISS (+ sensor)", "DISS_ALL",
            ],
            "multi_node_example": {
                "0": {"state": 8},
                "1": {"position": 10.0, "velocity_ff": 0.0},
            },
        }
