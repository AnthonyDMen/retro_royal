from __future__ import annotations

from pathlib import Path
import sys


def resource_path(*parts: str | Path) -> str:
    """Resolve asset paths for dev runs and PyInstaller bundles."""
    # PyInstaller sets sys._MEIPASS to the temp extraction dir.
    base = getattr(sys, "_MEIPASS", None)
    base_path = Path(base) if base else Path(__file__).resolve().parent
    path = base_path
    for part in parts:
        path = path / Path(part)
    return str(path)
