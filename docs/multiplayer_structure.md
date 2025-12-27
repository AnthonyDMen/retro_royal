# Multiplayer structure (work-in-progress)

What lives in `multiplayer.py` today:
- `LobbyServer`: asyncio host loop, lobby state, match tick/duel orchestration.
- `LobbyClient`: asyncio client wrapper, snapshot queues, duel/minigame messages.
- `MultiplayerArenaScene`: game-side render/input/prediction, duel triggers, minigame launch.
- Globals: `ACTIVE_LOBBY_SERVER`, `ACTIVE_LOBBY_CLIENTS`, `stop_active_lobby`.
- Minigame helpers: duel wheel, RPS resolve, etc. (moving into `minigames/shared`).

Planned split to keep files smaller:
- `multiplayer_state.py`: ACTIVE globals and `stop_active_lobby`.
- `multiplayer_server.py`: lobby host + match/duel loop (imports `multiplayer_state`).
- `multiplayer_client.py`: lobby client (imports `multiplayer_state`).
- `multiplayer_arena.py`: scene/render/input + duel UI (imports client interface only).
- `multiplayer.py`: thin shim re-exporting the above for compatibility.

Per-minigame multiplayer modules:
- File: `minigames/<id>/multiplayer.py` (see `minigames/template/multiplayer.py` for a stub).
- Required: `MULTIPLAYER_ENABLED = True`, `MINIGAME_ID` or `get_minigame_id()`.
- Hooks: `build_match_payload(host_state, participants)`, `resolve_result(result_payload)`.
- Optional: `ai_choice(seed, round_no, participants)` for NPC/autoplay.
- Registry now prefers these modules and falls back to `game.py` flags.

Next coding steps:
- Move globals to `multiplayer_state.py`.
- Extract `LobbyServer` into `multiplayer_server.py` and `LobbyClient` into `multiplayer_client.py`; update shim.
- Extract `MultiplayerArenaScene` into `multiplayer_arena.py`.
