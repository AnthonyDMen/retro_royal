"""Shared helpers for Retro Royale minigames."""

from .multiplayer_registry import (
    discover_multiplayer_minigames,
    pick_minigame_wheel,
    load_minigame_multiplayer,
    get_minigame_hooks,
    minigame_has_hooks,
)

__all__ = [
    "discover_multiplayer_minigames",
    "pick_minigame_wheel",
    "load_minigame_multiplayer",
    "get_minigame_hooks",
    "minigame_has_hooks",
]
