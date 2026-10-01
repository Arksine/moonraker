from __future__ import annotations

import importlib.util
import math
import pathlib
from types import ModuleType


def load_metadata_module() -> ModuleType:
    module_path = (
        pathlib.Path(__file__).parents[1]
        / "moonraker"
        / "components"
        / "file_manager"
        / "metadata.py"
    )
    spec = importlib.util.spec_from_file_location(
        "moonraker_metadata_under_test",
        module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_qidistudio_metadata(tmp_path: pathlib.Path) -> None:
    metadata = load_metadata_module()
    gcode_path = tmp_path / "qidistudio.gcode"
    gcode_path.write_text(
        "\n".join(
            [
                "; HEADER_BLOCK_START",
                "; QIDIStudio 01.10.01.51",
                "; total layer number: 163",
                "; total filament length [mm] : 3944.21",
                "; total filament volume [cm^3] : 9486.94",
                "; total filament weight [g] : 11.76",
                "; filament_density: 1.24",
                "; filament_diameter: 1.75",
                "; max_z_height: 32.60",
                "; HEADER_BLOCK_END",
                "G1 Z0.2 F1200",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\r\n"
    )

    result = metadata.extract_metadata(str(gcode_path), [])

    assert result["slicer"] == "QIDIStudio"
    assert result["slicer_version"] == "01.10.01.51"
    assert result["layer_count"] == 163
    assert math.isclose(result["filament_total"], 3944.21)
    assert math.isclose(result["filament_weight_total"], 11.76)
    assert math.isclose(result["object_height"], 32.6)
