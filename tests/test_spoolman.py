# Unit tests for the spoolman per-tool spool tracking logic.
#
# These tests exercise the SpoolManager's tool-spool map management,
# extrusion attribution, extruder discovery, and API handlers without
# requiring a running Klipper or Spoolman instance.

from __future__ import annotations
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers to build a SpoolManager with mocked dependencies
# ---------------------------------------------------------------------------

def _make_mock_config() -> MagicMock:
    config = MagicMock()
    config.get_server.return_value = _make_mock_server()
    config.get.return_value = "http://localhost:7912"
    config.getint.return_value = 5
    config.error = Exception
    return config


def _make_mock_server() -> MagicMock:
    server = MagicMock()
    server.get_event_loop.return_value = _make_mock_eventloop()
    server.lookup_component.side_effect = _mock_lookup
    server.register_notification = MagicMock()
    server.register_event_handler = MagicMock()
    server.register_endpoint = MagicMock()
    server.register_remote_method = MagicMock()
    server.send_event = MagicMock()
    server.error = Exception
    server.is_verbose_enabled.return_value = False
    return server


def _make_mock_eventloop() -> MagicMock:
    el = MagicMock()
    el.register_timer.return_value = MagicMock()
    el.create_task = MagicMock()
    el.get_loop_time.return_value = 0.0
    el.create_future.return_value = MagicMock()
    return el


def _mock_lookup(name: str, default: Any = MagicMock()) -> Any:
    if name == "database":
        db = MagicMock()
        db.get_item = AsyncMock(return_value=None)
        db.insert_item = MagicMock()
        return db
    if name == "history":
        hist = MagicMock()
        hist.register_auxiliary_field = MagicMock()
        hist.tracking_enabled = MagicMock(return_value=True)
        return hist
    if name == "announcements":
        ann = MagicMock()
        ann.register_feed = MagicMock()
        return ann
    return MagicMock()


def _patch_trackers(sm: Any) -> None:
    """Patch the history trackers so tracker.update() doesn't crash."""
    for field in [sm.spool_history, sm.tool_spool_history]:
        field.tracker.history = MagicMock()
        field.tracker.history.tracking_enabled = MagicMock(return_value=True)


def _make_spoolman():
    """Create a SpoolManager instance with all dependencies mocked."""
    from moonraker.components.spoolman import SpoolManager
    config = _make_mock_config()
    sm = SpoolManager(config)
    _patch_trackers(sm)
    return sm


def _make_web_request(
    req_type: str = "GET",
    params: Optional[Dict[str, Any]] = None
) -> MagicMock:
    from moonraker.common import RequestType
    wr = MagicMock()
    rt = RequestType.GET if req_type == "GET" else RequestType.POST
    wr.get_request_type.return_value = rt

    def get_int(key, default=None):
        if params and key in params:
            return int(params[key]) if params[key] is not None else default
        return default

    def get_str(key, default=None):
        if params and key in params:
            return str(params[key])
        return default

    wr.get_int = get_int
    wr.get_str = get_str
    wr.get = lambda key, default=None: params.get(key, default) if params else default
    wr.get_boolean = lambda key, default=False: params.get(key, default) if params else default
    return wr


# ===========================================================================
# Tests
# ===========================================================================

class TestSpoolIdProperty:
    """The spool_id property should return tool 0's spool for backward compat."""

    def test_returns_none_when_empty(self):
        sm = _make_spoolman()
        assert sm.spool_id is None

    def test_returns_tool_0_spool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42, 1: 99}
        assert sm.spool_id == 42

    def test_returns_none_when_only_tool_1(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {1: 99}
        assert sm.spool_id is None


class TestSetActiveSpool:
    """set_active_spool manages the tool-spool map."""

    def test_set_single_tool(self):
        sm = _make_spoolman()
        sm.set_active_spool(5, tool=0)
        assert sm._tool_spool_map == {0: 5}

    def test_set_multi_tool(self):
        sm = _make_spoolman()
        sm.set_active_spool(10, tool=0)
        sm.set_active_spool(20, tool=1)
        assert sm._tool_spool_map == {0: 10, 1: 20}

    def test_clear_spool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm.set_active_spool(None, tool=1)
        assert 1 not in sm._tool_spool_map
        assert sm._tool_spool_map == {0: 5}

    def test_clear_only_tool_0(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        sm.set_active_spool(None, tool=0)
        assert sm._tool_spool_map == {}

    def test_noop_when_already_set(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        sm.server.send_event.reset_mock()
        sm.set_active_spool(5, tool=0)
        # Should not fire event when value unchanged
        sm.server.send_event.assert_not_called()

    def test_fires_event(self):
        sm = _make_spoolman()
        sm.set_active_spool(7, tool=1)
        sm.server.send_event.assert_called_with(
            "spoolman:active_spool_set",
            {"spool_id": 7, "tool": 1}
        )

    def test_saves_to_database(self):
        sm = _make_spoolman()
        sm.set_active_spool(15, tool=0)
        sm.database.insert_item.assert_called()

    def test_no_tool_defaults_to_tool_0_when_empty(self):
        sm = _make_spoolman()
        sm.set_active_spool(3)
        assert sm._tool_spool_map == {0: 3}

    def test_no_tool_sets_all_tracked_tools(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm.set_active_spool(99)
        assert sm._tool_spool_map == {0: 99, 1: 99}

    def test_no_tool_clears_all_tracked_tools(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm.set_active_spool(None)
        assert sm._tool_spool_map == {}

    def test_resets_epos_watermark(self):
        sm = _make_spoolman()
        sm._last_epos = 100.0
        sm.set_active_spool(5, tool=1)
        assert sm._highest_epos[1] == 100.0


class TestHasAnySpool:
    def test_false_when_empty(self):
        sm = _make_spoolman()
        assert sm._has_any_spool() is False

    def test_true_single_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        assert sm._has_any_spool() is True

    def test_true_multi_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        assert sm._has_any_spool() is True

    def test_false_when_all_none(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: None, 1: None}
        assert sm._has_any_spool() is False


class TestSaveToolSpoolMap:
    """_save_tool_spool_map persists the map and legacy key."""

    def test_saves_both_keys(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm._save_tool_spool_map()
        calls = sm.database.insert_item.call_args_list
        # Should have at least 2 calls: tool_spool_map and legacy spool_id
        keys_saved = [c[0][1] for c in calls]
        assert "spoolman.tool_spool_map" in keys_saved
        assert "spoolman.spool_id" in keys_saved

    def test_legacy_key_is_tool_0(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42, 1: 99}
        sm._save_tool_spool_map()
        # Find the legacy key call
        for call in sm.database.insert_item.call_args_list:
            if call[0][1] == "spoolman.spool_id":
                assert call[0][2] == 42
                return
        pytest.fail("Legacy spool_id key not saved")

    def test_legacy_key_none_when_no_tool_0(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {1: 99}
        sm._save_tool_spool_map()
        for call in sm.database.insert_item.call_args_list:
            if call[0][1] == "spoolman.spool_id":
                assert call[0][2] is None
                return
        pytest.fail("Legacy spool_id key not saved")

    def test_map_keys_are_strings(self):
        """DB keys must be strings for JSON serialization."""
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm._save_tool_spool_map()
        for call in sm.database.insert_item.call_args_list:
            if call[0][1] == "spoolman.tool_spool_map":
                db_map = call[0][2]
                assert all(isinstance(k, str) for k in db_map.keys())
                assert db_map == {"0": 5, "1": 10}
                return
        pytest.fail("tool_spool_map not saved")


class TestDiscoverExtruderTools:
    """_discover_extruder_tools parses extruder names from Klipper objects."""

    def test_single_extruder(self):
        sm = _make_spoolman()
        sm._discover_extruder_tools(["extruder", "heater_bed"])
        assert sm._extruder_to_tool == {"extruder": 0}

    def test_dual_extruder(self):
        sm = _make_spoolman()
        sm._discover_extruder_tools(["extruder", "extruder1", "heater_bed"])
        assert sm._extruder_to_tool == {"extruder": 0, "extruder1": 1}

    def test_triple_extruder(self):
        sm = _make_spoolman()
        sm._discover_extruder_tools(
            ["extruder", "extruder1", "extruder2"]
        )
        assert sm._extruder_to_tool == {
            "extruder": 0, "extruder1": 1, "extruder2": 2
        }

    def test_no_extruders(self):
        sm = _make_spoolman()
        sm._discover_extruder_tools(["heater_bed", "fan"])
        assert sm._extruder_to_tool == {}

    def test_ignores_non_numeric_suffix(self):
        sm = _make_spoolman()
        sm._discover_extruder_tools(["extruder", "extruder_stepper"])
        assert sm._extruder_to_tool == {"extruder": 0}


class TestHandleStatusUpdate:
    """_handle_status_update tracks extrusion per-tool."""

    def test_extrusion_attributed_to_current_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        sm._extruder_to_tool = {"extruder": 0}
        sm._current_tool = 0
        sm._current_extruder = "extruder"
        sm._highest_epos = {0: 0.0}
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 10.0], "extruder": "extruder"}},
            0.0
        )
        assert sm.pending_reports.get(5, 0) == pytest.approx(10.0)
        assert sm._highest_epos[0] == 10.0

    def test_no_report_without_spool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {}
        sm._extruder_to_tool = {"extruder": 0}
        sm._current_tool = 0
        sm._current_extruder = "extruder"
        sm._highest_epos = {0: 0.0}
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 10.0], "extruder": "extruder"}},
            0.0
        )
        assert sm.pending_reports == {}

    def test_tool_change_updates_current_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm._extruder_to_tool = {"extruder": 0, "extruder1": 1}
        sm._current_tool = 0
        sm._current_extruder = "extruder"
        sm._highest_epos = {0: 50.0}
        # Simulate tool change to extruder1
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 50.0], "extruder": "extruder1"}},
            0.0
        )
        assert sm._current_tool == 1
        assert sm._current_extruder == "extruder1"
        assert sm._highest_epos[1] == 50.0

    def test_extrusion_after_tool_change(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        sm._extruder_to_tool = {"extruder": 0, "extruder1": 1}
        sm._current_tool = 0
        sm._current_extruder = "extruder"
        sm._highest_epos = {0: 50.0}
        # Tool change
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 50.0], "extruder": "extruder1"}},
            0.0
        )
        # Extrude 5mm on tool 1
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 55.0], "extruder": "extruder1"}},
            0.0
        )
        assert sm.pending_reports.get(10, 0) == pytest.approx(5.0)
        assert sm.pending_reports.get(5, 0) == 0  # Tool 0 unchanged

    def test_no_extrusion_on_retract(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        sm._extruder_to_tool = {"extruder": 0}
        sm._current_tool = 0
        sm._current_extruder = "extruder"
        sm._highest_epos = {0: 50.0}
        # E position goes down (retraction)
        sm._handle_status_update(
            {"toolhead": {"position": [0, 0, 0, 48.0], "extruder": "extruder"}},
            0.0
        )
        assert sm.pending_reports == {}
        assert sm._highest_epos[0] == 50.0  # Watermark unchanged

    def test_ignores_update_without_toolhead(self):
        sm = _make_spoolman()
        sm._handle_status_update({"other": "data"}, 0.0)
        assert sm.pending_reports == {}


class TestDecodeMessage:
    """_decode_message handles spool deletion from Spoolman websocket."""

    def test_clears_deleted_spool_single_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42}
        sm._decode_message(
            '{"resource": "spool", "type": "deleted", "payload": {"id": 42}}'
        )
        assert 0 not in sm._tool_spool_map

    def test_clears_deleted_spool_multi_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42, 1: 42}
        sm._decode_message(
            '{"resource": "spool", "type": "deleted", "payload": {"id": 42}}'
        )
        assert sm._tool_spool_map == {}

    def test_ignores_non_spool_resource(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42}
        sm._decode_message(
            '{"resource": "filament", "type": "deleted", "payload": {"id": 42}}'
        )
        assert sm._tool_spool_map == {0: 42}

    def test_ignores_non_delete_event(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42}
        sm._decode_message(
            '{"resource": "spool", "type": "updated", "payload": {"id": 42}}'
        )
        assert sm._tool_spool_map == {0: 42}

    def test_does_not_clear_other_spools(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 42, 1: 99}
        sm._decode_message(
            '{"resource": "spool", "type": "deleted", "payload": {"id": 42}}'
        )
        assert sm._tool_spool_map == {1: 99}


class TestHandleSpoolIdRequest:
    """API endpoint: GET/POST /server/spoolman/spool_id"""

    @pytest.mark.asyncio
    async def test_get_single_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        wr = _make_web_request("GET", {"tool": 0})
        result = await sm._handle_spool_id_request(wr)
        assert result == {"spool_id": 5}

    @pytest.mark.asyncio
    async def test_get_multi_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        wr = _make_web_request("GET", {"tool": 1})
        result = await sm._handle_spool_id_request(wr)
        assert result == {"spool_id": 10}

    @pytest.mark.asyncio
    async def test_get_legacy_no_tool_param(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        wr = _make_web_request("GET", {})
        result = await sm._handle_spool_id_request(wr)
        assert result == {"spool_id": 5}

    @pytest.mark.asyncio
    async def test_get_unassigned_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        wr = _make_web_request("GET", {"tool": 1})
        result = await sm._handle_spool_id_request(wr)
        assert result == {"spool_id": None}

    @pytest.mark.asyncio
    async def test_post_sets_spool(self):
        sm = _make_spoolman()
        wr = _make_web_request("POST", {"spool_id": 42, "tool": 1})
        result = await sm._handle_spool_id_request(wr)
        assert sm._tool_spool_map[1] == 42
        assert result == {"spool_id": 42}

    @pytest.mark.asyncio
    async def test_post_no_tool_defaults_to_tool_0_when_empty(self):
        sm = _make_spoolman()
        wr = _make_web_request("POST", {"spool_id": 42})
        await sm._handle_spool_id_request(wr)
        assert sm._tool_spool_map[0] == 42

    @pytest.mark.asyncio
    async def test_post_no_tool_sets_all_tracked_tools(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        wr = _make_web_request("POST", {"spool_id": 42})
        await sm._handle_spool_id_request(wr)
        assert sm._tool_spool_map == {0: 42, 1: 42}

    @pytest.mark.asyncio
    async def test_post_clear_spool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        wr = _make_web_request("POST", {"spool_id": None, "tool": 0})
        await sm._handle_spool_id_request(wr)
        assert 0 not in sm._tool_spool_map


class TestHandleStatusRequest:
    """API endpoint: GET /server/spoolman/status"""

    @pytest.mark.asyncio
    async def test_empty_state(self):
        sm = _make_spoolman()
        wr = _make_web_request("GET")
        result = await sm._handle_status_request(wr)
        assert result["spoolman_connected"] is False
        assert result["spool_id"] is None
        assert result["tool_spool_map"] == {}
        assert result["pending_reports"] == []

    @pytest.mark.asyncio
    async def test_single_tool_status(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        wr = _make_web_request("GET")
        result = await sm._handle_status_request(wr)
        assert result["spool_id"] == 5
        assert result["tool_spool_map"] == {0: 5}

    @pytest.mark.asyncio
    async def test_multi_tool_status(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        wr = _make_web_request("GET")
        result = await sm._handle_status_request(wr)
        assert result["spool_id"] == 5
        assert result["tool_spool_map"] == {0: 5, 1: 10}

    @pytest.mark.asyncio
    async def test_pending_reports_included(self):
        sm = _make_spoolman()
        sm.pending_reports = {5: 12.5, 10: 3.2}
        wr = _make_web_request("GET")
        result = await sm._handle_status_request(wr)
        reports = result["pending_reports"]
        assert len(reports) == 2
        ids = {r["spool_id"] for r in reports}
        assert ids == {5, 10}


class TestHistoryCallbacks:
    """History auxiliary field reset callbacks."""

    def test_history_reset_returns_tool_0_spool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5}
        result = sm._on_history_reset()
        assert result == [5]

    def test_history_reset_empty_when_no_spool(self):
        sm = _make_spoolman()
        result = sm._on_history_reset()
        assert result == []

    def test_tool_spool_history_reset_multi_tool(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: 5, 1: 10}
        result = sm._on_tool_spool_history_reset()
        assert len(result) == 2
        tools = {e["tool"] for e in result}
        assert tools == {0, 1}

    def test_tool_spool_history_reset_skips_none(self):
        sm = _make_spoolman()
        sm._tool_spool_map = {0: None, 1: 10}
        result = sm._on_tool_spool_history_reset()
        assert len(result) == 1
        assert result[0] == {"tool": 1, "spool_id": 10}


class TestComponentInit:
    """component_init loads from DB and handles migration."""

    @pytest.mark.asyncio
    async def test_loads_existing_tool_map(self):
        sm = _make_spoolman()
        sm.database.get_item = AsyncMock(
            side_effect=lambda ns, key, default=None: (
                {"0": 5, "1": 10} if key == "spoolman.tool_spool_map" else default
            )
        )
        await sm.component_init()
        assert sm._tool_spool_map == {0: 5, 1: 10}

    @pytest.mark.asyncio
    async def test_migrates_legacy_spool_id(self):
        sm = _make_spoolman()
        sm.database.get_item = AsyncMock(
            side_effect=lambda ns, key, default=None: (
                42 if key == "spoolman.spool_id"
                else default
            )
        )
        await sm.component_init()
        assert sm._tool_spool_map == {0: 42}
        # Should have saved the new format
        sm.database.insert_item.assert_called()

    @pytest.mark.asyncio
    async def test_empty_when_no_data(self):
        sm = _make_spoolman()
        sm.database.get_item = AsyncMock(return_value=None)
        await sm.component_init()
        assert sm._tool_spool_map == {}
