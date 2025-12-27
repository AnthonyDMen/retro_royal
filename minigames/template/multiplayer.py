"""Template multiplayer hooks for a minigame.

Copy into a minigame folder as `multiplayer.py` and tweak as needed.
"""

from __future__ import annotations

from typing import Iterable, Dict, Any, Optional

MINIGAME_ID = "template"
MULTIPLAYER_ENABLED = False


def get_minigame_id() -> str:
    return MINIGAME_ID


def build_match_payload(host_state: Optional[Dict[str, Any]], participants: Iterable[str]) -> Dict[str, Any]:
    """Return payload used to launch the minigame."""
    return {"minigame": MINIGAME_ID, "participants": list(participants or [])}


def resolve_result(result_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize result payload reported by the minigame."""
    return {
        "duel_id": result_payload.get("duel_id"),
        "winner": result_payload.get("winner"),
        "loser": result_payload.get("loser"),
        "outcome": result_payload.get("outcome"),
    }


def ai_choice(seed: str, round_no: int, participants: Iterable[str]):
    """Optional: return an automated choice for NPCs."""
    return None
