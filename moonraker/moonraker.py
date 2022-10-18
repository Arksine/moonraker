#!/usr/bin/env python3
# Legacy entry point for Moonraker
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license


if __name__ == "__main__":
    import sys
    import importlib
    import pathlib
    pkg_parent = pathlib.Path(__file__).parent.parent
    sys.path.pop(0)
    sys.path.insert(0, str(pkg_parent))
    svr = importlib.import_module(".server", "moonraker")
    svr.main(False)  # type: ignore
