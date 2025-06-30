# SPDX-License-Identifier: MIT
# tests/test_avd_utils.py
"""
High-coverage tests for pyavd.py
==============================================

What is covered
---------------
✔ Target / Device parsing helpers
✔ Internal `_parse_avd_list`
✔ `AVD.device` property (setter → lookup by alias)
✔ Command-building logic of `AVD.create`
✔ `AVD.wait_boot_completed` happy-path
✔ Error branches
      • unknown device alias  → ValueError
      • boot timeout          → BootTimeoutError

All host interactions are stubbed, so the suite runs on any machine
(without an Android SDK).
"""
from __future__ import annotations

import stat
import sys
import textwrap
import types
from collections import namedtuple
from pathlib import Path
from typing import Final, List

import pytest

###############################################################################
# --- fixture: import module with dummy SDK tools ----------------------------
###############################################################################
@pytest.fixture  # function scope → compatible with monkeypatch
def au(tmp_path_factory, monkeypatch):
    """
    Prepare three tiny dummy SDK binaries, let `find_android_tool`
    resolve them via env-vars, then import *avd_utils_refactored*.
    """
    bin_dir = tmp_path_factory.mktemp("sdk_bin")
    for tool in ("avdmanager", "emulator", "adb"):
        p = bin_dir / tool
        p.write_text("#!/bin/sh\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv(f"ANDROID_{tool.upper()}", str(p))

    # ensure a clean import every time the fixture is invoked
    sys.modules.pop("pyavd", None)

    import pyavd as mod
    return mod


###############################################################################
# --- canned text outputs mimicking `avdmanager list …` ----------------------
###############################################################################
_TARGET_LIST: Final[str] = textwrap.dedent(
    """
    ----------
    id: 1 or "android-34"
        Name: Android 14
        Type: Platform
        API level: 34
        Revision: 1

    ----------
    id: 2 or "android-33"
        Name: Android 13
        Type: Platform
        API level: 33
        Revision: 2
"""
)

_DEVICE_LIST: Final[str] = textwrap.dedent(
    """
    ---------
    id: 0 or "pixel"
        Name: Pixel 4
        OEM : Google
        Tag : google

    ---------
    id: 1 or "Nexus_5X"
        Name: Nexus 5X
        OEM : Google
        Tag : default
"""
)

_AVD_LIST: Final[str] = textwrap.dedent(
    """
    --------
    Name: Pixel_4_API_34
    Device: pixel (Google Pixel 4)
    Path: /tmp/.android/Pixel_4_API_34.avd
    Target: Google APIs (Android 34)
    Skin: pixel_4
    Sdcard: 512M
    Based on: Android 34.0.0 Tag/ABI: google_apis/x86_64
"""
)

###############################################################################
# --- autouse fixture: share dummy device catalogue --------------------------
###############################################################################
@pytest.fixture(autouse=True)
def _patch_get_devices(monkeypatch, au):
    """
    Provide a reusable dummy device list for every test.

    Individual tests can still override `Device.get_devices` with their own
    monkey-patch if they need different behaviour.
    """
    dummy_devices = list(au.Device._parse(_DEVICE_LIST.splitlines()))
    monkeypatch.setattr(au.Device, "get_devices", lambda: dummy_devices)


###############################################################################
# --- parsing helpers ---------------------------------------------------------
###############################################################################
def test_target_parse(au):
    targets = list(au.Target._parse(_TARGET_LIST.splitlines()))
    assert [t.id for t in targets] == [1, 2]
    assert targets[0].name == "Android 14"
    assert targets[1].api_level == 33


def test_device_parse(au):
    devices = list(au.Device._parse(_DEVICE_LIST.splitlines()))
    assert [d.id_alias for d in devices] == ["pixel", "Nexus_5X"]
    assert devices[0].oem == "Google"
    assert devices[1].tag == "default"


def test_parse_avd_list(au):
    avds = list(au._parse_avd_list(_AVD_LIST.splitlines()))
    assert len(avds) == 1
    avd = avds[0]
    assert avd.name == "Pixel_4_API_34"
    assert avd.device.id_alias == "pixel"
    assert avd.abi == "google_apis/x86_64"
    assert avd.based_on.startswith("Android 34")

###############################################################################
# --- AVD.device property -----------------------------------------------------
###############################################################################
def test_device_lookup_by_alias(au):
    avd = au.AVD()
    avd.device = "pixel (Google Pixel 4)"
    assert avd.device and avd.device.id_alias == "pixel"

###############################################################################
# --- AVD.create command-builder ---------------------------------------------
###############################################################################
def _dummy_run_collector() -> types.SimpleNamespace:
    """
    Return a namespace that captures the last command given to the fake _run.
    """
    ns = types.SimpleNamespace(cmd=None)

    def fake_run(cmd: List[str], **_):
        ns.cmd = cmd
        # mimic subprocess.CompletedProcess
        return types.SimpleNamespace(stdout=b"", stderr=b"")

    ns.fake = fake_run
    return ns


def test_avd_create_happy(monkeypatch, au):
    # capture generated command
    recorder = _dummy_run_collector()
    monkeypatch.setattr(au, "_run", recorder.fake)

    # Locate the real implementation module without importing anything new
    import sys
    core = sys.modules[au.AVD.__module__]  # the module where AVD (and _run) live
    monkeypatch.setattr(core, "_run", recorder.fake)

    # fake success of get_by_name so .create returns normally
    monkeypatch.setattr(
        au.AVD, "get_by_name", classmethod(lambda cls, n: au.AVD(name=n))
    )

    au.AVD.create(
        name="demo",
        package="system-images;android-34;google_apis;x86_64",
        device="pixel",
    )

    expected_tail = [
        "create",
        "avd",
        "-n",
        "demo",
        "--package",
        "system-images;android-34;google_apis;x86_64",
        "--device",
        "0",  # id for "pixel"
    ]
    assert recorder.cmd[-len(expected_tail) :] == expected_tail


def test_avd_create_unknown_device(monkeypatch, au):
    with pytest.raises(ValueError):
        au.AVD.create(name="bad", package="pkg", device="does-not-exist")

###############################################################################
# --- boot-completed helper ---------------------------------------------------
###############################################################################
def test_wait_boot_completed_success(monkeypatch, au):
    avd = au.AVD(name="Pixel_4_API_34")

    class DummyDev:
        def shell(self, _):
            return "1"  # booted

    Info = namedtuple("Info", "serial tags")

    class DummyClient:
        def list(self, *, extended=False):
            return [Info(serial="emulator-5554", tags={"product": "Pixel_4_API_34"})]

        def device(self, _serial):
            return DummyDev()

    monkeypatch.setattr(au, "_adb_client", lambda: DummyClient())
    monkeypatch.setattr(au.time, "sleep", lambda _x: None)

    # should not raise
    avd.wait_boot_completed(timeout=5)


def test_wait_boot_completed_timeout(monkeypatch, au):
    avd = au.AVD(name="Pixel_4_API_34")

    class DummyDev:
        def shell(self, _):
            return "0"  # never booted

    Info = namedtuple("Info", "serial tags")

    class DummyClient:
        def list(self, *, extended=False):
            return [Info(serial="emulator-5554", tags={"product": "Pixel_4_API_34"})]

        def device(self, _serial):
            return DummyDev()

    monkeypatch.setattr(au, "_adb_client", lambda: DummyClient())
    monkeypatch.setattr(au.time, "sleep", lambda _x: None)

    with pytest.raises(au.BootTimeoutError):
        avd.wait_boot_completed(timeout=0)