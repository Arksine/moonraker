# Wrapper for msgspec with stdlib fallback
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import contextlib
from typing import Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    def dumps(obj: Any) -> bytes: ...  # type: ignore # noqa: E704
    def loads(data: Union[str, bytes, bytearray]) -> Any: ...  # noqa: E704

MSGSPEC_ENABLED = False
_msgspc_var = os.getenv("MOONRAKER_ENABLE_MSGSPEC", "y").lower()
if _msgspc_var in ["y", "yes", "true"]:
    with contextlib.suppress(ImportError):
        import msgspec
        from msgspec import DecodeError as JSONDecodeError
        encoder = msgspec.json.Encoder()
        decoder = msgspec.json.Decoder()
        dumps = encoder.encode  # noqa: F811
        loads = decoder.decode  # noqa: F811
        MSGSPEC_ENABLED = True
if not MSGSPEC_ENABLED:
    import json
    from json import JSONDecodeError  # type: ignore # noqa: F401,F811
    loads = json.loads  # type: ignore

    def dumps(obj) -> bytes:  # type: ignore # noqa: F811
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")
