from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Optional

from resource_path import resource_path


def _ensure_minigame_root(base_dir: Path) -> None:
    if not base_dir.exists():
        return
    root = str(base_dir.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def load_minigame_module(
    minigame_id: str,
    module: str = "game",
    base_dir: Optional[Path] = None,
):
    if not minigame_id:
        return None
    module_name = f"minigames.{minigame_id}.{module}"
    base = Path(base_dir) if base_dir else Path(resource_path("minigames"))
    _ensure_minigame_root(base)
    try:
        return importlib.import_module(module_name)
    except Exception:
        module_path = base / minigame_id / f"{module}.py"
        if not module_path.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if not spec or not spec.loader:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            return None
