# SPDX-License-Identifier: MIT
"""
android_sdk_utils
=================

A thin, import-ready façade that exposes the high-level Android SDK helpers
(`Target`, `Device`, `AVD`), error types, and the cross-platform
``find_android_tool`` locator at package level.

Usage
-----
>>> from pyavd import AVD, Device, Target
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export public API
# ---------------------------------------------------------------------------
from .pyavd import (                                    # ← rename if needed
    AVD,
    BootTimeoutError,
    Device,
    Target,
    AndroidToolNotFound,
)

# Re-export the helpers you want tests to patch/use
_run = pyavd._run
_parse_avd_list = pyavd._parse_avd_list       # (add any others as needed)

__all__: list[str] = [
    # core helpers
    # high-level models
    "Target",
    "Device",
    "AVD",
    # exceptions
    "AndroidToolNotFound",
    "BootTimeoutError",
]

# ---------------------------------------------------------------------------
# Optional: version & logging niceties
# ---------------------------------------------------------------------------
try:
    from importlib.metadata import version, PackageNotFoundError

    try:
        __version__: str = version(__name__)
    except PackageNotFoundError:  # running from a checkout
        __version__ = "0.0.0.dev0"
except Exception:  # pragma: no cover
    __version__ = "0.0.0.dev0"

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
