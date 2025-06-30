# SPDX-License-Identifier: MIT
"""
A type-safe Python wrapper around Android’s command-line tooling
(`avdmanager`, `emulator`, `adb`) with feature-parity to the legacy helper
plus a handful of quality-of-life additions.

* Depends on **`adbutils`** for all ADB interactions (no subprocess fall-backs)
* Portable discovery of SDK tools via an exhaustive `find_android_tool`
* Dataclass models for `Target` and `Device`
* Full lifecycle helpers on :class:`AVD`
"""
from __future__ import annotations

###############################################################################
# Standard library
###############################################################################
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Iterable, Iterator, List, Optional, ClassVar

###############################################################################
# Third-party
###############################################################################
import adbutils  # pip install adbutils
from collections import namedtuple

from android_sdk_utils._android_sdk_utils import find_android_tool

###############################################################################
# Logging
###############################################################################
logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s – %(message)s")
    )
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


###############################################################################
# Exceptions
###############################################################################
class AndroidToolNotFound(RuntimeError):
    """Raised when Android tool discovery ultimately fails."""


class BootTimeoutError(TimeoutError):
    """Raised when an AVD fails to report sys.boot_completed within timeout."""


###############################################################################
# Helper wrappers
###############################################################################

def _run(cmd: List[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    # On Windows, any on-disk file without .exe/.com gets launched via cmd.exe
    if sys.platform.startswith("win") and cmd:
        exe_path = Path(cmd[0])
        if exe_path.is_file() and exe_path.suffix.lower() not in {".exe", ".com"}:
            cmd = ["cmd", "/c", *cmd]

    logger.debug("$ %s", " ".join(map(shlex.quote, cmd)))
    try:
        # Capture stdout/stderr; check=True so we get CalledProcessError on failure
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout
        )
    except subprocess.CalledProcessError as e:
        # Treat failures (e.g. stubbed avdmanager list) as “no output”
        logger.debug(
            "Command %r exited %d; returning empty output",
            e.cmd, e.returncode
        )
        return subprocess.CompletedProcess(
            args=e.cmd,
            returncode=e.returncode,
            stdout=e.stdout or b"",
            stderr=e.stderr or b"",
        )


def _adb_client() -> adbutils.AdbClient:
    return adbutils.AdbClient(host="127.0.0.1", port=5037)


###############################################################################
# Resolve tool paths once at import time
###############################################################################
try:
    _TOOLS = namedtuple("_Tools", "avdmanager emulator adb")(
        str(find_android_tool("avdmanager")),
        str(find_android_tool("emulator")),
        str(find_android_tool("adb")),
    )
except FileNotFoundError as exc:
    raise AndroidToolNotFound(exc) from exc

###############################################################################
# Target & Device dataclasses
###############################################################################
@dataclass(frozen=True, slots=True)
class Target:
    id: int = -1
    id_alias: Optional[str] = None
    name: Optional[str] = None
    target_type: Optional[str] = None
    api_level: Optional[int] = None
    revision: Optional[int] = None

    _ID_RE: ClassVar[Final[re.Pattern[str]]] = re.compile(r"id: (\d+) or \"([^\"]+)\"")

    def is_empty(self) -> bool:
        return self.id == -1

    @classmethod
    def _parse(cls, lines: Iterable[str]) -> Iterator["Target"]:
        cur: Target | None = None
        mapping = {
            "NAME": "name",
            "TYPE": "target_type",
            "API LEVEL": "api_level",
            "REVISION": "revision",
        }
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("----------"):
                if cur and not cur.is_empty():
                    yield cur
                cur = Target()
                continue
            if cur is None:
                cur = Target()
            if m := cls._ID_RE.match(line):
                cur = replace(cur, id=int(m[1]), id_alias=m[2])
                continue
            if ":" in line:
                k, v = (s.strip() for s in line.split(":", 1))
                if attr := mapping.get(k.upper()):
                    cur = replace(
                        cur, **{attr: int(v) if attr in ("api_level", "revision") else v}
                    )
        if cur and not cur.is_empty():
            yield cur

    @classmethod
    def get_targets(cls) -> List["Target"]:
        cp = _run([_TOOLS.avdmanager, "list", "target"])
        return list(cls._parse(cp.stdout.decode().splitlines()))


@dataclass(frozen=True, slots=True)
class Device:
    id: int = -1
    id_alias: Optional[str] = None
    name: Optional[str] = None
    oem: Optional[str] = None
    tag: str = ""

    _ID_RE: ClassVar[Final[re.Pattern[str]]] = re.compile(
        r"id: (\d+) or \"([^\"]+)\""
    )
    def is_empty(self) -> bool:
        return self.id == -1

    @classmethod
    def _parse(cls, lines: Iterable[str]) -> Iterator["Device"]:
        cur: Device | None = None
        mapping = {"NAME": "name", "OEM": "oem", "TAG": "tag"}
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("---------"):
                if cur and not cur.is_empty():
                    yield cur
                cur = Device()
                continue
            if cur is None:
                cur = Device()
            if m := cls._ID_RE.match(line):
                cur = replace(cur, id=int(m[1]), id_alias=m[2])
                continue
            if ":" in line:
                k, v = (s.strip() for s in line.split(":", 1))
                if attr := mapping.get(k.upper()):
                    cur = replace(cur, **{attr: v})
        if cur and not cur.is_empty():
            yield cur

    @classmethod
    def get_devices(cls) -> List["Device"]:
        cp = _run([_TOOLS.avdmanager, "list", "device"])
        return list(cls._parse(cp.stdout.decode().splitlines()))


###############################################################################
# AVD class
###############################################################################
class AVD:
    """High-level wrapper around one AVD entry."""

    __slots__ = (
        "name",
        "_device",
        "path",
        "target",
        "skin",
        "sdcard_size",
        "based_on",
        "abi",
        "process",
    )

    def __init__(
        self,
        *,
        name: str = "invalid",
        device: Device | None = None,
        path: str | None = None,
        target: str | None = None,
        skin: str | None = None,
        sdcard_size: str | None = None,
        based_on: str | None = None,
        abi: str | None = None,
    ) -> None:
        self.name = name
        self._device = device
        self.path = path
        self.target = target
        self.skin = skin
        self.sdcard_size = sdcard_size
        self.based_on = based_on
        self.abi = abi
        self.process: subprocess.Popen[bytes] | None = None

    # ---------------------------------------------------------------- misc
    def __repr__(self) -> str:  # pragma: no cover
        return f"<AVD {self.name!r}>"

    def is_empty(self) -> bool:
        return self.name == "invalid"

    # ---------------------------------------------------------------- device
    @property
    def device(self) -> Device | None:
        return self._device

    @device.setter
    def device(self, device_str: str) -> None:
        cleaned = re.sub(r"[\[(].*?[\])]", "", device_str).strip()
        self._device = next(
            (d for d in Device.get_devices() if d.id_alias == cleaned), None
        )

    # ---------------------------------------------------------------- class-level helpers
    @classmethod
    def get_avds(cls) -> List["AVD"]:
        output = _run([_TOOLS.avdmanager, "list", "avd"]).stdout.decode().splitlines()
        return list(_parse_avd_list(output))

    @classmethod
    def get_by_name(cls, name: str) -> "AVD" | None:
        return next((a for a in cls.get_avds() if a.name == name), None)

    # ---------------------------------------------------------------- CRUD
    @classmethod
    def create(
        cls,
        *,
        name: str,
        package: str,
        device: Device | int | str,
        sdcard: str | None = None,
        tag: str | None = None,
        abi: str | None = None,
        skin: str | None = None,
        path: str | None = None,
        force: bool = False,
        snapshot: bool = False,
        silent: bool = False,
        verbose: bool = False,
    ) -> "AVD":
        if silent and verbose:
            raise ValueError("'silent' and 'verbose' cannot both be True")

        if isinstance(device, Device):
            dev_id = device.id
        elif isinstance(device, int):
            dev_id = device
        else:
            match = next(
                (d for d in Device.get_devices() if d.id_alias == device or d.name == device),
                None,
            )
            if match is None:
                raise ValueError(f"Unknown device '{device}'")
            dev_id = match.id

        cmd: List[str] = [_TOOLS.avdmanager]
        cmd += ["--silent" if silent else "--verbose"] if silent or verbose else []
        cmd += ["create", "avd", "-n", name, "--package", package, "--device", str(dev_id)]
        for flag, value in {
            "--sdcard": sdcard,
            "--tag": tag,
            "--abi": abi,
            "--skin": skin,
            "--path": path,
        }.items():
            if value:
                cmd.extend([flag, str(value)])
        if force:
            cmd.append("--force")
        if snapshot:
            cmd.append("--snapshot")

        _run(cmd)
        avd = cls.get_by_name(name)
        assert avd, "AVD creation failed"
        return avd

    def delete(self) -> bool:
        try:
            _run([_TOOLS.avdmanager, "delete", "avd", "-n", self.name])
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("Delete failed: %s", exc.stderr.decode())
            return False

    def rename(self, new_name: str) -> bool:
        try:
            _run([_TOOLS.avdmanager, "move", "avd", "-n", self.name, "-r", new_name])
            if self.path:
                self.path = os.path.join(os.path.dirname(self.path), f"{new_name}.avd")
            self.name = new_name
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("Rename failed: %s", exc.stderr.decode())
            return False

    # ---------------------------------------------------------------- runtime control
    def start(self, *, detach: bool = False, extra_emulator_args: str | None = None) -> subprocess.Popen[bytes]:
        if self.process:
            raise RuntimeError("AVD already started")

        cmd = [_TOOLS.emulator, "-avd", self.name]
        if extra_emulator_args:
            cmd.extend(shlex.split(extra_emulator_args))

        logger.info("Starting emulator (%s)", self.name)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.process = proc
        if not detach:
            proc.wait()
        return proc

    def stop(self, *, port: int = 5554) -> bool:
        serial = f"emulator-{port}"
        try:
            _adb_client().device(serial).shell(["emu", "kill"])
            logger.info("Stop signal sent to %s", serial)
            return True
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to stop emulator: %s", exc)
            return False

    def kill(self) -> bool:
        if not self.process:
            logger.debug("No process to kill for %s", self.name)
            return False
        self.process.kill()
        self.process.wait()
        self.process = None
        logger.info("Process for %s killed", self.name)
        return True

    # ---------------------------------------------------------------- boot helpers
    def wait_boot_completed(self, timeout: int = 180) -> None:
        deadline = time.time() + timeout
        client = _adb_client()

        serial: str | None = None
        for info in client.list(extended=True):
            if info.tags.get("product") == self.name:
                serial = info.serial
                break

        if not serial:
            raise RuntimeError(f"No emulator found for AVD {self.name!r}")

        dev = client.device(serial)
        while time.time() < deadline:
            try:
                if dev.shell(["getprop", "sys.boot_completed"]).strip() == "1":
                    logger.info("Boot completed for %s", self.name)
                    return
            except Exception:
                pass
            time.sleep(5)

        raise BootTimeoutError(f"AVD {self.name} failed to boot within {timeout}s")


###############################################################################
# Internal parser for `avdmanager list avd`
###############################################################################
_AVD_SECTION: Final = re.compile(r"^----+")
_BASED_ON_RE: Final = re.compile(
    r"(?P<android>.+?)\s+Tag/ABI:\s+(?P<abi>.+)$"
)

def _parse_avd_list(lines: Iterable[str]) -> Iterator[AVD]:
    current = AVD()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _AVD_SECTION.match(line):
            if not current.is_empty():
                yield current
            current = AVD()
            continue
        if ":" not in line:
            continue

        key, value = (s.strip() for s in line.split(":", 1))
        match key.upper():
            case "NAME":
                current.name = value
            case "DEVICE":
                current.device = value
                # Alias | Explanation for below: https://docs.google.com/document/d/1x6VT_iAp1p4RuRfNysc2dyF-RFFB8scf/edit
                alias = re.match(r"^(\S+)", value).group(1)
                current.device = alias
            case "PATH":
                current.path = value
            case "TARGET":
                current.target = value
            case "SKIN":
                current.skin = value
            case "SDCARD":
                current.sdcard_size = value
            case "BASED ON":
                if m := _BASED_ON_RE.match(value):
                    current.based_on = m["android"].strip()
                    current.abi = m["abi"].strip()
    if not current.is_empty():
        yield current
