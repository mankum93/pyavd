from __future__ import annotations
import os, shutil, sys
from pathlib import Path
from typing import Optional, Iterable

# ---------- Core helper ------------------------------------------------------
def find_android_tool(tool: str) -> Path:
    """
    Locate an Android command-line tool (avdmanager, emulator, adb) on any OS.

    Search order (first hit wins):
      1. Explicit env vars: ANDROID_{TOOL.upper()} (e.g. ANDROID_EMULATOR)
      2. Anything already on the PATH
      3. $ANDROID_HOME / $ANDROID_SDK_ROOT
      4. Typical default SDK locations for the current platform
      5. User-supplied fallback directories via FIND_ANDROID_EXTRA_DIRS env var
    Raises:
        FileNotFoundError if nothing is found.
    """
    if tool not in {"adb", "emulator", "avdmanager"}:
        raise ValueError(f"Unsupported tool: {tool}")

    # 1. Dedicated env var?
    explicit = os.getenv(f"ANDROID_{tool.upper()}")
    if explicit and Path(explicit).expanduser().is_file():
        return Path(explicit).expanduser()

    # 2. Already on PATH?
    path_hit = shutil.which(tool) or shutil.which(_windows_name(tool))
    if path_hit:
        return Path(path_hit)

    # 3. Look under ANDROID_SDK_ROOT / ANDROID_HOME
    sdk_root = os.getenv("ANDROID_SDK_ROOT") or os.getenv("ANDROID_HOME")
    if sdk_root:
        p = _scan_sdk(Path(sdk_root), tool)
        if p:
            return p

    # 4. Well-known defaults
    for candidate_root in _default_sdk_roots():
        p = _scan_sdk(candidate_root, tool)
        if p:
            return p

    # 5. Extra dirs (comma-separated)
    extra = os.getenv("FIND_ANDROID_EXTRA_DIRS", "")
    for root in map(str.strip, extra.split(",")):
        if root:
            p = _scan_sdk(Path(root).expanduser(), tool, deep=True)
            if p:
                return p

    raise FileNotFoundError(
        f"Could not locate {tool}. "
        "Install Android SDK Platform-Tools / Emulator or set ANDROID_SDK_ROOT."
    )

# ---------- Helpers ----------------------------------------------------------
def _windows_name(tool: str) -> str:
    """Return the executable name for Windows builds."""
    if sys.platform.startswith("win"):
        # avdmanager is a .bat; emulator & adb are .exe
        return f"{tool}.bat" if tool == "avdmanager" else f"{tool}.exe"
    return tool

def _scan_sdk(root: Path, tool: str, deep: bool = False) -> Optional[Path]:
    """Search the SDK tree for the requested tool."""
    root = root.expanduser()
    exe = _windows_name(tool)
    subdirs: dict[str, Iterable[Path]] = {
        "adb":        [root / "platform-tools"],
        "emulator":   [root / "emulator"],
        "avdmanager": (
            (root / "cmdline-tools").glob("*/bin")   # new cmdline-tools path
            if deep or (root / "cmdline-tools").exists()
            else []
        ),
    }
    # Legacy path for avdmanager (pre-cmdline-tools)
    if tool == "avdmanager":
        subdirs["avdmanager"] = list(subdirs["avdmanager"]) + [root / "tools" / "bin"]

    for d in subdirs[tool]:
        cand = d / exe
        if cand.is_file():
            return cand

    if deep:  # exhaustive scan when requested
        for cand in root.rglob(exe):
            return cand
    return None

def _default_sdk_roots() -> list[Path]:
    """Return typical SDK install roots for each OS."""
    home = Path.home()
    if sys.platform.startswith("darwin"):   # macOS
        return [
            Path("~/Library/Android/sdk").expanduser(),
            home / "Android" / "Sdk",
        ]
    elif sys.platform.startswith("win"):    # Windows
        return [
            Path(os.environ.get("LOCALAPPDATA", "")) /
            "Android" / "Sdk",
            home / "AppData" / "Local" / "Android" / "Sdk",
        ]
    else:                                    # Linux / WSL
        return [
            home / "Android" / "Sdk",
            Path("/opt/android-sdk"),
        ]