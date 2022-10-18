from __future__ import annotations
import pathlib
import pytest
import hashlib
import confighelper
import shutil
import time
from moonraker.confighelper import ConfigError
from moonraker.server import Server
from moonraker.utils import ServerError
from moonraker.components import gpio
from mocks import MockGpiod
from typing import TYPE_CHECKING, Dict
if TYPE_CHECKING:
    from confighelper import ConfigHelper

@pytest.fixture(scope="class")
def config(base_server: Server) -> ConfigHelper:
    base_server.load_component(base_server.config, "secrets")
    return base_server.config

@pytest.fixture(scope="class")
def test_config(config: ConfigHelper,
                path_args: Dict[str, pathlib.Path]
                ) -> ConfigHelper:
    assets = path_args['asset_path']
    sup_cfg_path = assets.joinpath("moonraker/supplemental.conf")
    if not sup_cfg_path.exists():
        pytest.fail("Supplemental config not found")
    cfg = config.read_supplemental_config(str(sup_cfg_path))
    return cfg["test_options"]

@pytest.fixture(scope="function")
def gpio_config(test_config: ConfigHelper,
                monkeypatch: pytest.MonkeyPatch
                ) -> ConfigHelper:
    def load_gpio_mock(name: str) -> MockGpiod:
        return MockGpiod()
    monkeypatch.setattr(gpio, "load_system_module", load_gpio_mock)
    yield test_config
    server = test_config.get_server()
    gpio_comp = server.lookup_component("gpio", None)
    if gpio_comp is not None:
        gpio_comp.close()
        gpio_comp.reserved_gpios = {}

class TestConfigGeneric:
    def test_get_server(self, config: ConfigHelper):
        server = config.get_server()
        assert isinstance(server, Server)

    def test_get_item(self, config: ConfigHelper):
        sec = config["file_manager"]
        assert sec.section == "file_manager"

    def test_no_section_fail(self, config: ConfigHelper):
        with pytest.raises(ConfigError):
            config["not_available"].get("no_section")

    def test_contains(self, config: ConfigHelper):
        assert "file_manager" in config

    def test_not_contains(self, config: ConfigHelper):
        assert "not_available" not in config

    def test_has_option(self, config: ConfigHelper):
        assert config.has_option("host")

    def test_get_name(self, config: ConfigHelper):
        assert config.get_name() == "server"

    def test_get_options(self,
                         config: ConfigHelper,
                         path_args: Dict[str, pathlib.Path]):
        expected = {
            "host": "0.0.0.0",
            "port": "7010",
            "ssl_port": "7011",
            "klippy_uds_address": str(path_args["klippy_uds_path"])
        }
        assert expected == config.get_options()

    def test_get_hash(self, config: ConfigHelper):
        opts = config.get_options()
        expected_hash = hashlib.sha256()
        for opt, val in opts.items():
            expected_hash.update(opt.encode())
            expected_hash.update(val.encode())
        cfg_hash = config.get_hash().hexdigest()
        assert cfg_hash == expected_hash.hexdigest()

def test_missing_supplemental_config(config: ConfigHelper):
    no_file = pathlib.Path("nofile")
    with pytest.raises(ConfigError):
        config.read_supplemental_config(no_file)

def test_error_supplemental_config(config: ConfigHelper,
                                   path_args: Dict[str, pathlib.Path]):
    assets = path_args["asset_path"]
    invalid_cfg = assets.joinpath("moonraker/invalid_config.conf")
    if not invalid_cfg.exists():
        pytest.fail("Invalid Config File does not exist")
    with pytest.raises(ConfigError):
        config.read_supplemental_config(invalid_cfg)

def test_prefix_sections(test_config: ConfigHelper):
    prefix = test_config.get_prefix_sections("prefix_sec")
    expected = ["prefix_sec one", "prefix_sec two", "prefix_sec three"]
    assert prefix == expected

class TestGetString:
    def test_get_str_exists(self, test_config: ConfigHelper):
        val = test_config.get("test_string")
        assert val == "Hello World"

    def test_get_str_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.get("invalid_option")

    def test_get_str_default(self, test_config: ConfigHelper):
        assert test_config.get("invalid_option", None) is None

    def test_get_str_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.get("test_string", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_string' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetInt:
    def test_get_int_exists(self, test_config: ConfigHelper):
        val = test_config.getint("test_int")
        assert val == 1

    def test_get_int_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getint("invalid_option")

    def test_get_int_default(self, test_config: ConfigHelper):
        assert test_config.getint("invalid_option", None) is None

    def test_get_int_fail_above(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getint("test_int", above=1)

    def test_get_int_fail_below(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getint("test_int", below=1)

    def test_get_int_fail_minval(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getint("test_int", minval=2)

    def test_get_int_fail_maxval(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getint("test_int", maxval=0)

    def test_get_int_pass_all(self, test_config: ConfigHelper):
        val = test_config.getint("test_int", above=0, below=2,
                                 minval=1, maxval=1)
        assert val == 1

    def test_get_int_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.getint("test_int", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_int' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetFloat:
    def test_get_float_exists(self, test_config: ConfigHelper):
        val = test_config.getfloat("test_float")
        assert 3.5 == pytest.approx(val)

    def test_get_float_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getfloat("invalid_option")

    def test_get_float_default(self, test_config: ConfigHelper):
        assert test_config.getfloat("invalid_option", None) is None

    def test_get_float_fail_above(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getfloat("test_float", above=3.55)

    def test_get_float_fail_below(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getfloat("test_float", below=3.45)

    def test_get_float_fail_minval(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getfloat("test_float", minval=3.6)

    def test_get_float_fail_maxval(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getfloat("test_float", maxval=3.45)

    def test_get_float_pass_all(self, test_config: ConfigHelper):
        val = test_config.getfloat("test_float", above=3.45, below=3.55,
                                   minval=3, maxval=4)
        assert 3.5 == pytest.approx(val)

    def test_get_float_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.getfloat("test_float", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_float' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetBoolean:
    def test_get_boolean_exists(self, test_config: ConfigHelper):
        val = test_config.getboolean("test_bool")
        assert val is True

    def test_get_float_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getboolean("invalid_option")

    def test_get_float_default(self, test_config: ConfigHelper):
        assert test_config.getboolean("invalid_option", None) is None

    def test_get_int_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.getboolean("test_bool", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_bool' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetList:
    def test_get_list_exists(self, test_config: ConfigHelper):
        val = test_config.getlist("test_list")
        assert val == ["one", "two", "three"]

    def test_get_list_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getlist("invalid_option")

    def test_get_list_default(self, test_config: ConfigHelper):
        assert test_config.getlist("invalid_option", None) is None

    def test_get_int_list(self, test_config: ConfigHelper):
        val = test_config.getintlist("test_int_list", separator=",")
        assert val == [1, 2, 3]

    def test_get_float_list(self, test_config: ConfigHelper):
        val = test_config.getfloatlist("test_float_list", separator=",")
        assert val == pytest.approx([1.5, 2.8, 3.2])

    def test_get_multi_list(self, test_config: ConfigHelper):
        val = test_config.getlists("test_multi_list", list_type=int,
                                   separators=("\n", ","))
        assert val == [[1, 2, 3], [4, 5, 6]]

    def test_get_list_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.getlist("test_list", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_list' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetDict:
    def test_get_dict_exists(self, test_config: ConfigHelper):
        val = test_config.getdict("test_dict", dict_type=int)
        assert val == {"one": 1, "two": 2, "three": 3}

    def test_get_dict_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getdict("invalid_option")

    def test_get_dict_default(self, test_config: ConfigHelper):
        assert test_config.getdict("invalid_option", None) is None

    def test_get_dict_empty_fields(self, test_config: ConfigHelper):
        val = test_config.getdict("test_dict_empty_field",
                                  allow_empty_fields=True)
        assert val == {"one": "test", "two": None, "three": None}

    def test_get_dict_empty_fields_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.getdict("test_dict_empty_field")

    def test_get_dict_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.getdict("test_dict", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_dict' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetTemplate:
    def test_get_template_exists(self, test_config: ConfigHelper):
        val = test_config.gettemplate("test_template").render()
        assert val == "mqttuser"

    @pytest.mark.asyncio
    async def test_get_template_async(self, test_config: ConfigHelper):
        templ = test_config.gettemplate("test_template", is_async=True)
        val = await templ.render_async()
        assert val == "mqttuser"

    def test_get_template_plain(self, test_config: ConfigHelper):
        val = test_config.gettemplate("test_string").render()
        assert val == "Hello World"

    def test_get_template_fail(self, test_config: ConfigHelper):
        with pytest.raises(ConfigError):
            test_config.gettemplate("invalid_option")

    def test_get_template_render_fail(self, test_config: ConfigHelper):
        with pytest.raises(ServerError):
            test_config.gettemplate("test_template", is_async=True).render()

    def test_get_template_default(self, test_config: ConfigHelper):
        assert test_config.gettemplate("invalid_option", None) is None

    def test_load_template(self, test_config: ConfigHelper):
        val = test_config.load_template("test_template").render()
        assert val == "mqttuser"

    def test_load_template_default(self, test_config: ConfigHelper):
        templ = test_config.load_template(
            "invalid_option", "{secrets.mqtt_credentials.password}")
        val = templ.render()
        assert val == "mqttpass"

    def test_get_template_deprecate(self, test_config: ConfigHelper):
        server = test_config.get_server()
        test_config.gettemplate("test_template", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_template' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetGpioOut:
    def test_get_gpio_exists(self, gpio_config: ConfigHelper):
        val: gpio.GpioOutputPin = gpio_config.getgpioout("test_gpio")
        assert (
            val.orig == "gpiochip0/gpio26" and
            val.name == "gpiochip0:gpio26" and
            val.inverted is False and
            val.value == 0
        )

    def test_get_gpio_no_chip(self, gpio_config: ConfigHelper):
        val: gpio.GpioOutputPin = gpio_config.getgpioout("test_gpio_no_chip")
        assert (
            val.orig == "gpio26" and
            val.name == "gpiochip0:gpio26" and
            val.inverted is False and
            val.value == 0
        )

    def test_get_gpio_invert(self, gpio_config: ConfigHelper):
        val: gpio.GpioOutputPin = gpio_config.getgpioout("test_gpio_invert")
        assert (
            val.orig == "!gpiochip0/gpio26" and
            val.name == "gpiochip0:gpio26" and
            val.inverted is True and
            val.value == 0
        )

    def test_get_gpio_no_chip_invert(self, gpio_config: ConfigHelper):
        val: gpio.GpioOutputPin = gpio_config.getgpioout(
            "test_gpio_no_chip_invert")
        assert (
            val.orig == "!gpio26" and
            val.name == "gpiochip0:gpio26" and
            val.inverted is True and
            val.value == 0
        )

    def test_get_gpio_initial_value(self, gpio_config: ConfigHelper):
        val: gpio.GpioOutputPin = gpio_config.getgpioout(
            "test_gpio", initial_value=1)
        assert (
            val.orig == "gpiochip0/gpio26" and
            val.name == "gpiochip0:gpio26" and
            val.inverted is False and
            val.value == 1
        )

    def test_get_gpio_fail(self, gpio_config: ConfigHelper):
        with pytest.raises(ConfigError):
            gpio_config.getgpioout("invalid_option")

    def test_get_gpio_default(self, gpio_config: ConfigHelper):
        assert gpio_config.getgpioout("invalid_option", None) is None

    @pytest.mark.parametrize("opt", ["pullup", "pullup_no_chip",
                                     "pulldown", "pulldown_no_chip"])
    def test_get_gpio_invalid(self, gpio_config: ConfigHelper, opt: str):
        option = f"test_gpio_{opt}"
        if not gpio_config.has_option(option):
            pytest.fail(f"No option {option}")
        with pytest.raises(ConfigError):
            gpio_config.getgpioout(option)

    def test_get_gpio_deprecated(self, gpio_config: ConfigHelper):
        server = gpio_config.get_server()
        gpio_config.getgpioout("test_gpio", deprecate=True)
        expected = (
            f"[test_options]: Option 'test_gpio' is "
            "deprecated, see the configuration documention "
            "at https://moonraker.readthedocs.io/en/latest/configuration"
        )
        assert expected in server.warnings

class TestGetConfiguration:
    def test_get_config_no_exist(self, base_server: Server):
        fake_path = pathlib.Path("no_exist")
        if fake_path.exists():
            pytest.fail("Path exists")
        args = dict(base_server.app_args)
        args["config_file"] = str(fake_path)
        with pytest.raises(ConfigError):
            confighelper.get_configuration(base_server, args)

    def test_get_config_no_access(self,
                                  base_server: Server,
                                  path_args: Dict[str, pathlib.Path]
                                  ):
        cfg_path = path_args["config_path"]
        test_cfg = cfg_path.joinpath("test.conf")
        shutil.copy(path_args["moonraker.conf"], test_cfg)
        test_cfg.chmod(mode=222)
        args = dict(base_server.app_args)
        args["config_file"] = str(test_cfg)
        with pytest.raises(ConfigError):
            confighelper.get_configuration(base_server, args)

    def test_get_config_no_server(self,
                                  base_server: Server,
                                  path_args: Dict[str, pathlib.Path]
                                  ):
        assets = path_args['asset_path']
        sup_cfg_path = assets.joinpath("moonraker/supplemental.conf")
        if not sup_cfg_path.exists():
            pytest.fail("Supplemental config not found")
        args = dict(base_server.app_args)
        args["config_file"] = str(sup_cfg_path)
        with pytest.raises(ConfigError):
            confighelper.get_configuration(base_server, args)

class TestBackupConfig:
    def test_find_backup_fail(self):
        fake_path = pathlib.Path("no_exist")
        if fake_path.exists():
            fake_path.unlink()
        result = confighelper.find_config_backup(fake_path)
        assert result is None

    def test_backup_config_success(
        self, path_args: Dict[str, pathlib.Path], config: ConfigHelper
    ):
        cfg_path = path_args["moonraker.conf"]
        bkp_dest = cfg_path.parent.joinpath(f".{cfg_path.name}.bkp")
        if bkp_dest.exists():
            pytest.fail("Backup Already Exists")
        config.create_backup()
        assert bkp_dest.is_file()

    def test_backup_skip(
        self, path_args: Dict[str, pathlib.Path], config: ConfigHelper
    ):
        cfg_path = path_args["moonraker.conf"]
        bkp_dest = cfg_path.parent.joinpath(f".{cfg_path.name}.bkp")
        if not bkp_dest.exists():
            pytest.fail("Backup Not Present")
        stat = bkp_dest.stat()
        config.create_backup()
        assert stat == bkp_dest.stat()

    def test_find_backup(self, path_args: Dict[str, pathlib.Path]):
        cfg_path = path_args["moonraker.conf"]
        bkp_dest = cfg_path.parent.joinpath(f".{cfg_path.name}.bkp")
        bkp = confighelper.find_config_backup(str(cfg_path))
        assert bkp == str(bkp_dest)
