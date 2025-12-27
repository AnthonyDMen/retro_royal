"""Helpers for multiplayer-enabled minigames.

Centralizes discovery and selection of minigames that opt into multiplayer so
the main multiplayer flow can stay smaller and reuse shared logic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Iterable, List, Optional, Any

# Safe fallback list so the duel wheel always has entries.
FALLBACK_MINIGAMES = ["rps_duel"]


def _load_module(module_path: Path, dotted_name: str) -> Optional[Any]:
    """Load a module from an explicit path."""
    try:
        spec = importlib.util.spec_from_file_location(dotted_name, module_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _enabled_flag_from_module(module: Any, default: bool = False) -> bool:
    flag_primary = getattr(module, "MULTIPLAYER_ENABLED", default)
    flag_alt = getattr(module, "multiplayer_enabled", default)
    return bool(flag_primary or flag_alt)


def discover_multiplayer_minigames(base_dir: Optional[Path] = None) -> List[str]:
    """Return a list of minigame folder names that opt into multiplayer."""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    if not base.exists():
        return list(FALLBACK_MINIGAMES)
    valid: List[str] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("__") or entry.name == "shared":
            continue
        mp_file = entry / "multiplayer.py"
        game_file = entry / "game.py"
        # Prefer dedicated multiplayer module.
        if mp_file.exists():
            module = _load_module(mp_file, f"minigames.{entry.name}.multiplayer")
            if module and _enabled_flag_from_module(module, default=False):
                valid.append(entry.name)
            # If a multiplayer.py exists, treat it as source of truth; skip game.py flag.
            continue
        # Fallback to flag inside game.py only when no multiplayer.py is present.
        if not mp_file.exists() and game_file.exists():
            module = _load_module(game_file, f"minigames.{entry.name}.game")
            if module and _enabled_flag_from_module(module, default=False):
                valid.append(entry.name)
    return sorted(valid) if valid else list(FALLBACK_MINIGAMES)


def minigame_has_hooks(minigame_id: str, base_dir: Optional[Path] = None) -> bool:
    """Check if a minigame has a multiplayer.py module that can be loaded."""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    mp_path = base / minigame_id / "multiplayer.py"
    if not mp_path.exists():
        return False
    mod = load_minigame_multiplayer(minigame_id, base_dir=base)
    if not mod:
        return False
    return _enabled_flag_from_module(mod, default=False)


def pick_minigame_wheel(
    rng,
    minigames: Optional[Iterable[str]] = None,
    slots: int = 5,
    base_dir: Optional[Path] = None,
) -> List[str]:
    """Select a wheel of minigames without duplicates, filtered to those with hooks."""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    candidates = list(minigames or FALLBACK_MINIGAMES)
    candidates = [g for g in candidates if minigame_has_hooks(g, base_dir=base)]
    if not candidates:
        return []
    size = max(1, int(slots))
    size = min(size, len(candidates))
    try:
        return list(rng.sample(candidates, size))
    except ValueError:
        # Fallback in case RNG lacks sample: simple shuffle + slice
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        return shuffled[:size]


def load_minigame_multiplayer(minigame_id: str, base_dir: Optional[Path] = None):
    """Load the multiplayer module for a minigame if present."""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    mp_path = base / minigame_id / "multiplayer.py"
    if not mp_path.exists():
        return None
    return _load_module(mp_path, f"minigames.{minigame_id}.multiplayer")


def get_minigame_hooks(minigame_id: str, base_dir: Optional[Path] = None):
    """Return the multiplayer hooks module or a simple fallback stub."""
    module = load_minigame_multiplayer(minigame_id, base_dir=base_dir)
    if module:
        return module

    class _Fallback:
        MINIGAME_ID = minigame_id
        MULTIPLAYER_ENABLED = True

        @staticmethod
        def get_minigame_id():
            return minigame_id

        @staticmethod
        def build_match_payload(host_state, participants):
            return {"minigame": minigame_id, "participants": list(participants or [])}

        @staticmethod
        def resolve_result(result_payload):
            return {
                "duel_id": result_payload.get("duel_id"),
                "winner": result_payload.get("winner"),
                "loser": result_payload.get("loser"),
                "outcome": result_payload.get("outcome"),
            }

    return _Fallback
