# Semantic Version Parsing and Comparison
#
# Copyright (C) 2023  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import re
from enum import Flag, auto
from typing import Tuple, Optional, Dict, List

# Python regex for parsing version strings from PEP 440
# https://peps.python.org/pep-0440/#appendix-b-parsing-version-strings-with-regular-expressions
VERSION_PATTERN = r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_\.]?
                (?P<post_l>post|rev|r)
                [-_\.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [-_\.]?
            (?P<dev_l>dev)
            [-_\.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?       # local version
"""

GIT_VERSION_PATTERN = r"""
    (?P<tag>
        v?
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
    )
    (?:
        (?:-(?P<dev_n>[0-9]+))                            # dev count
        (?:-g(?P<hash>[a-fA-F0-9]+))?                     # abbrev hash
    )?
    (?P<dirty>-dirty)?
    (?P<inferred>-(?:inferred|shallow))?

"""

_py_version_regex = re.compile(
    r"^\s*" + VERSION_PATTERN + r"\s*$",
    re.VERBOSE | re.IGNORECASE,
)

_git_version_regex = re.compile(
    r"^\s*" + GIT_VERSION_PATTERN + r"\s*$",
    re.VERBOSE | re.IGNORECASE,
)

class ReleaseType(Flag):
    FINAL = auto()
    ALPHA = auto()
    BETA = auto()
    RELEASE_CANDIDATE = auto()
    POST = auto()
    DEV = auto()

class BaseVersion:
    def __init__(self, version: str) -> None:
        self._release: str = "?"
        self._release_type = ReleaseType(0)
        self._tag: str = "?"
        self._orig: str = version.strip()
        self._release_tup: Tuple[int, ...] = tuple()
        self._extra_tup: Tuple[int, ...] = tuple()
        self._has_dev_part: bool = False
        self._dev_count: int = 0
        self._valid_version: bool = False

    @property
    def full_version(self) -> str:
        return self._orig

    @property
    def release(self) -> str:
        return self._release

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def release_type(self) -> ReleaseType:
        return self._release_type

    @property
    def dev_count(self) -> int:
        return self._dev_count

    def is_pre_release(self) -> bool:
        for pr_idx in (1, 2, 3):
            if ReleaseType(1 << pr_idx) in self._release_type:
                return True
        return False

    def is_post_release(self) -> bool:
        return ReleaseType.POST in self._release_type

    def is_dev_release(self) -> bool:
        return ReleaseType.DEV in self._release_type

    def is_alpha_release(self) -> bool:
        return ReleaseType.ALPHA in self._release_type

    def is_beta_release(self) -> bool:
        return ReleaseType.BETA in self._release_type

    def is_release_candidate(self) -> bool:
        return ReleaseType.RELEASE_CANDIDATE in self._release_type

    def is_final_release(self) -> bool:
        return ReleaseType.FINAL in self._release_type

    def is_valid_version(self) -> bool:
        return self._valid_version

    def __str__(self) -> str:
        return self._orig

    def _validate(self, other: BaseVersion) -> None:
        if not self._valid_version:
            raise ValueError(
                f"Version {self._orig} is not a valid version string "
                f"for type {type(self).__name__}"
            )
        if not other._valid_version:
            raise ValueError(
                f"Version {other._orig} is not a valid version string "
                f"for type {type(self).__name__}"
            )

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup != __value._release_tup:
            return False
        if self._extra_tup != __value._extra_tup:
            return False
        if self._has_dev_part != __value._has_dev_part:
            return False
        if self._dev_count != __value._dev_count:
            return False
        return True

    def __lt__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup != __value._release_tup:
            return self._release_tup < __value._release_tup
        if self._extra_tup != __value._extra_tup:
            return self._extra_tup < __value._extra_tup
        if self._has_dev_part != __value._has_dev_part:
            return self._has_dev_part
        return self._dev_count < __value._dev_count

    def __le__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup > __value._release_tup:
            return False
        if self._extra_tup > __value._extra_tup:
            return False
        if self._has_dev_part != __value._has_dev_part:
            return self._has_dev_part
        return self._dev_count <= __value._dev_count

    def __ne__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup != __value._release_tup:
            return True
        if self._extra_tup != __value._extra_tup:
            return True
        if self._has_dev_part != __value._has_dev_part:
            return True
        if self._dev_count != __value._dev_count:
            return True
        return False

    def __gt__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup != __value._release_tup:
            return self._release_tup > __value._release_tup
        if self._extra_tup != __value._extra_tup:
            return self._extra_tup > __value._extra_tup
        if self._has_dev_part != __value._has_dev_part:
            return __value._has_dev_part
        return self._dev_count > __value._dev_count

    def __ge__(self, __value: object) -> bool:
        if not isinstance(__value, type(self)):
            raise ValueError("Invalid type for comparison")
        self._validate(__value)
        if self._release_tup < __value._release_tup:
            return False
        if self._extra_tup < __value._extra_tup:
            return False
        if self._has_dev_part != __value._has_dev_part:
            return __value._has_dev_part
        return self._dev_count >= __value._dev_count


class PyVersion(BaseVersion):
    def __init__(self, version: str) -> None:
        super().__init__(version)
        ver_match = _py_version_regex.match(version)
        if ver_match is None:
            return
        version_info = ver_match.groupdict()
        release: Optional[str] = version_info["release"]
        if release is None:
            return
        self._valid_version = True
        self._release = release
        self._tag = f"v{release}" if self._orig[0].lower() == "v" else release
        self._release_tup = tuple(int(part) for part in release.split("."))
        self._extra_tup = (1, 0, 0)
        if version_info["pre"] is not None:
            pre_conv = dict([("a", 1), ("b", 2), ("c", 3), ("r", 3), ("p", 3)])
            lbl = version_info["pre_l"][0].lower()
            self._extra_tup = (0, pre_conv.get(lbl, 0), int(version_info["pre_n"] or 0))
            self._tag += version_info["pre"]
            self._release_type |= ReleaseType(1 << pre_conv.get(lbl, 1))
            if version_info["post"] is not None:
                # strange combination of a "post" pre-release.
                num = version_info["post_n1"] or version_info["post_n2"]
                self._extra_tup += (int(num or 0),)
                self._tag += version_info["post"]
                self._release_type |= ReleaseType.POST
        elif version_info["post"] is not None:
            num = version_info["post_n1"] or version_info["post_n2"]
            self._extra_tup = (2, int(num or 0), 0)
            self._tag += version_info["post"]
            self._release_type |= ReleaseType.POST
        self._has_dev_part = version_info["dev"] is not None
        if self._has_dev_part:
            self._release_type |= ReleaseType.DEV
        elif self._release_type.value == 0:
            self._release_type = ReleaseType.FINAL
        elif self._release_type.value == ReleaseType.POST.value:
            self._release_type |= ReleaseType.FINAL
        self._dev_count = int(version_info["dev_n"] or 0)
        self.local: Optional[str] = version_info["local"]

    def convert_to_git(self, version_info: Dict[str, Optional[str]]) -> GitVersion:
        git_version: Optional[str] = version_info["release"]
        if git_version is None:
            raise ValueError("Invalid version string")
        if self._orig[0].lower() == "v":
            git_version == f"v{git_version}"
        local: str = version_info["local"] or ""
        # Assume semantic versioning, convert the version string.
        if version_info["dev_n"] is not None:
            major, _, minor = git_version.rpartition(".")
            if major:
                git_version = f"v{major}.{max(int(minor) - 1, 0)}"
        if version_info["pre"] is not None:
            git_version = f"{git_version}{version_info['pre']}"
        dev_num = version_info["dev_n"] or 0
        git_version = f"{git_version}-{dev_num}"
        local_parts = local.split(".", 1)[0]
        if local_parts[0]:
            git_version = f"{git_version}-{local_parts[0]}"
        if len(local_parts) > 1:
            git_version = f"{git_version}-dirty"
        return GitVersion(git_version)


class GitVersion(BaseVersion):
    def __init__(self, version: str) -> None:
        super().__init__(version)
        self._is_dirty: bool = False
        self._is_inferred: bool = False
        ver_match = _git_version_regex.match(version)
        if ver_match is None:
            # Check Fallback
            fb_match = re.match(r"(?P<hash>[a-fA-F0-9]+)(?P<dirty>-dirty)?", self._orig)
            if fb_match is None:
                return
            self._tag = ""
            self._release = fb_match["hash"]
            self._is_dirty = fb_match["dirty"] is not None
            self._is_inferred = True
            return
        version_info = ver_match.groupdict()
        release: Optional[str] = version_info["release"]
        if release is None:
            return
        self._valid_version = True
        self._release = release
        self._tag = version_info["tag"] or "?"
        self._release_tup = tuple(int(part) for part in release.split("."))
        self._extra_tup = (1, 0, 0)
        if version_info["pre"] is not None:
            pre_conv = dict([("a", 1), ("b", 2), ("c", 3), ("r", 3), ("p", 3)])
            lbl = version_info["pre_l"][0].lower()
            self._extra_tup = (0, pre_conv.get(lbl, 0), int(version_info["pre_n"] or 0))
            self._release_type = ReleaseType(1 << pre_conv.get(lbl, 1))
        # All git versions are considered to have a dev part.  Contrary to python
        # versioning, a version with a dev number is greater than the same version
        # without one.
        self._has_dev_part = True
        self._dev_count = int(version_info["dev_n"] or 0)
        if self._dev_count > 0:
            self._release_type |= ReleaseType.DEV
        if self._release_type.value == 0:
            self._release_type = ReleaseType.FINAL
        self._is_inferred = version_info["inferred"] is not None
        self._is_dirty = version_info["dirty"] is not None

    @property
    def short_version(self) -> str:
        if not self._valid_version:
            return "?"
        return f"{self._tag}-{self._dev_count}"

    @property
    def dirty(self) -> bool:
        return self._is_dirty

    @property
    def inferred(self) -> bool:
        return self._is_inferred

    def is_fallback(self) -> bool:
        return self._is_inferred and not self._valid_version

    def infer_last_tag(self) -> str:
        if self._valid_version:
            if self._is_inferred:
                # We can't infer a previous release from another inferred release
                return self._tag
            type_choices = dict([(1, "a"), (2, "b"), (3, "rc")])
            if self.is_pre_release() and self._extra_tup > (0, 1, 0):
                type_idx = self._extra_tup[1]
                type_count = self._extra_tup[2]
                if type_count == 0:
                    type_idx -= 1
                else:
                    type_count -= 1
                pretype = type_choices.get(type_idx, "rc")
                return f"{self._release}.{pretype}{type_count}"
            else:
                parts = [int(ver) for ver in self._release.split(".")]
                new_ver: List[str] = []
                need_decrement = True
                for part in reversed(parts):
                    if part > 0 and need_decrement:
                        need_decrement = False
                        part -= 1
                    new_ver.insert(0, str(part))
                return "v" + ".".join(new_ver)
        return "v0.0.0"
