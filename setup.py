from __future__ import annotations

import pathlib
import subprocess
import sys

from setuptools import setup

try:
    import packaging  # noqa: F401
except ImportError:
    install_cmd = [sys.executable, "-m", "pip", "install", "packaging>=23.0"]
    if getattr(sys, "base_prefix", sys.prefix) == sys.prefix:
        install_cmd.insert(4, "--user")
    subprocess.check_call(install_cmd)
    import packaging  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent
APP = [str(ROOT / "src" / "main.py")]
DATA_FILES: list[tuple[str, list[str]]] = []
ICNS_PATH = ROOT / "resources" / "icons" / "PhotoLab.icns"
ICON_OPTION = str(ICNS_PATH) if ICNS_PATH.exists() else None

OPTIONS: dict[str, object] = {
    "argv_emulation": False,
    "iconfile": ICON_OPTION,
    "includes": [
        "jaraco.text",
        "jaraco.context",
        "jaraco.functools",
        "jaraco.collections",
    ],
    "plist": {
        "CFBundleName": "PhotoLab",
        "CFBundleDisplayName": "PhotoLab",
        "CFBundleIdentifier": "com.example.photolab",
        "CFBundleVersion": "1.0.0",
        "NSHumanReadableCopyright": "© 2025 PhotoLab Team",
    },
    "optimize": 2,
}

if ICON_OPTION is None:
    OPTIONS.pop("iconfile", None)

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app", "packaging"],
)
