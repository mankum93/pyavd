"""
High-coverage tests for android_tools.find_android_tool
------------------------------------------------------

Scenarios covered
1. Unsupported tool         → ValueError
2. Explicit env-var hit
3. PATH hit (shutil.which)
4. ANDROID_SDK_ROOT hit
5. Default-root hit
6. Extra-dirs hit
7. Nothing found            → FileNotFoundError
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

import android_sdk_utils._android_sdk_utils as at              # <-- adjust if your module name differs

find_android_tool = at.find_android_tool
_windows_name     = at._windows_name


# ---------------------------------------------------------------- helpers
def _make_dummy_exe(dir_: Path, stem: str) -> Path:
    """
    Create a tiny executable file (POSIX + Windows) and mark it +x.

    We really create a file so that Path.is_file() returns True — no mocks.
    """
    name = _windows_name(stem)
    path = dir_ / name
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# ---------------------------------------------------------------- tests
def test_invalid_tool_raises():
    with pytest.raises(ValueError):
        find_android_tool("zipalign")          # not in {'adb','emulator','avdmanager'}


def test_env_var_hit(tmp_path, monkeypatch):
    dummy = _make_dummy_exe(tmp_path, "adb")
    monkeypatch.setenv("ANDROID_ADB", str(dummy))
    assert find_android_tool("adb") == dummy


def test_path_hit(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dummy = _make_dummy_exe(bin_dir, "adb")

    # prepend dummy dir to PATH
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.getenv('PATH', '')}")
    monkeypatch.delenv("ANDROID_ADB", raising=False)      # ensure env-var doesn't interfere
    assert find_android_tool("adb") == dummy


def test_sdk_root_hit(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    (sdk / "platform-tools").mkdir(parents=True)
    dummy = _make_dummy_exe(sdk / "platform-tools", "adb")

    monkeypatch.setenv("ANDROID_SDK_ROOT", str(sdk))
    monkeypatch.delenv("ANDROID_ADB", raising=False)
    monkeypatch.delenv("PATH", raising=False)

    assert find_android_tool("adb") == dummy


def test_default_root_hit(tmp_path, monkeypatch):
    default_sdk = tmp_path / "default"
    (default_sdk / "platform-tools").mkdir(parents=True)
    dummy = _make_dummy_exe(default_sdk / "platform-tools", "adb")

    # wipe higher-priority mechanisms
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_ADB", "PATH"):
        monkeypatch.delenv(var, raising=False)

    # point the helper’s internal function to our temp root
    monkeypatch.setattr(at, "_default_sdk_roots", lambda: [default_sdk])
    assert find_android_tool("adb") == dummy


def test_extra_dirs_hit(tmp_path, monkeypatch):
    extra_root = tmp_path / "extrasdk"
    deep_dir   = extra_root / "deep" / "nest" / "platform-tools"
    deep_dir.mkdir(parents=True)
    dummy = _make_dummy_exe(deep_dir, "adb")

    monkeypatch.setenv("FIND_ANDROID_EXTRA_DIRS", str(extra_root))
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_ADB", "PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(at, "_default_sdk_roots", lambda: [])

    assert find_android_tool("adb") == dummy


def test_not_found_raises(monkeypatch):
    # scrub every discovery path
    for var in (
        "ANDROID_SDK_ROOT",
        "ANDROID_HOME",
        "ANDROID_ADB",
        "PATH",
        "FIND_ANDROID_EXTRA_DIRS",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(at, "_default_sdk_roots", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda *_: None)

    with pytest.raises(FileNotFoundError):
        find_android_tool("adb")
