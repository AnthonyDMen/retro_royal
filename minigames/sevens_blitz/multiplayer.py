"""Multiplayer hooks for Sevens Blitz."""

from __future__ import annotations

import random
from typing import Iterable, Dict, Any, Optional

MINIGAME_ID = "sevens_blitz"
MULTIPLAYER_ENABLED = True


def get_minigame_id() -> str:
    return MINIGAME_ID


def build_match_payload(host_state: Optional[Dict[str, Any]], participants: Iterable[str]) -> Dict[str, Any]:
    seed = random.randrange(0, 1_000_000)
    return {"minigame": MINIGAME_ID, "participants": list(participants or []), "seed": seed}


def resolve_result(result_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "duel_id": result_payload.get("duel_id"),
        "winner": result_payload.get("winner"),
        "loser": result_payload.get("loser"),
        "outcome": result_payload.get("outcome"),
    }


def ai_choice(seed: str, round_no: int, participants: Iterable[str]):
    return None
