"""Multiplayer hooks for E-Kart duel (ghost race)."""

from __future__ import annotations

import random
from typing import Iterable, Dict, Any, Optional

MINIGAME_ID = "e_kart_duel"
MULTIPLAYER_ENABLED = True


def get_minigame_id() -> str:
    return MINIGAME_ID


def build_match_payload(host_state: Optional[Dict[str, Any]], participants: Iterable[str]) -> Dict[str, Any]:
    """Provide launch payload for the ghost race.

    We keep it simple: 1-lap race, shared seed so both sides see the same track layout.
    """
    seed = random.randrange(0, 1_000_000)
    return {
        "minigame": MINIGAME_ID,
        "participants": list(participants or []),
        "seed": seed,
    }


def resolve_result(result_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize result payload from the client."""
    return {
        "duel_id": result_payload.get("duel_id"),
        "winner": result_payload.get("winner"),
        "loser": result_payload.get("loser"),
        "outcome": result_payload.get("outcome"),
    }


def ai_choice(seed: str, round_no: int, participants: Iterable[str]):
    """E-Kart duel has no NPC participation path (ghost-only)."""
    return None
