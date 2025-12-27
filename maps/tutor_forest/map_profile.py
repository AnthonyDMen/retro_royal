import pygame
from sound_engine import play_music, stop_music
from scoreboard import save_score
from game_context import GameContext

def _normalize_name(name):
    return (name or "").strip().lower()


def _build_zone_levels():
    """Return ordered config for each tournament gate."""
    base_levels = [
        {
            "name": "large level",
            "display": "Large Level",
            "wins_to_clear": 2,
            "total_players": 8,  # player + 7 challengers
            "random_culls": [3, 1],  # defeated NPC + these extra random removals
        },
        {
            "name": "medium level",
            "display": "Medium Level",
            "wins_to_clear": 2,
            "total_players": 4,  # player + 3 challengers
            "random_culls": [1, 0],
        },
        {
            "name": "small level",
            "display": "Small Level",
            "wins_to_clear": 1,
            "total_players": 3,  # player + 2 challengers
            "random_culls": [1],  # defeat one, eliminate the other
        },
        {
            "name": "final level",
            "display": "Final Stage",
            "wins_to_clear": 1,
            "total_players": 2,  # single final challenger
            "random_culls": [0],
        },
    ]

    normalized = []
    cumulative = 0
    for entry in base_levels:
        zone = dict(entry)
        zone["key"] = _normalize_name(zone["name"])
        random_steps = list(zone.get("random_culls", []))
        if len(random_steps) < zone["wins_to_clear"]:
            pad_value = random_steps[-1] if random_steps else 0
            random_steps.extend([pad_value] * (zone["wins_to_clear"] - len(random_steps)))
        zone["random_culls"] = random_steps
        zone["wins_required"] = cumulative
        total_players = max(1, int(zone.get("total_players", 1)))
        zone["npc_slots"] = max(0, total_players - 1)
        cumulative += zone["wins_to_clear"]
        normalized.append(zone)
    return normalized


TOURNAMENT_LEVELS = _build_zone_levels()
ZONE_LOOKUP = {zone["key"]: zone for zone in TOURNAMENT_LEVELS}
TOURNAMENT_STATE_FLAG = "tutor_forest_tournament"

TILE_OFFSET = 0

MAP_PROFILE = {
    "name": "Tutor Forest",
    "music": "arena_theme.ogg",
}

# list the layer names in the order you want them drawn (bottom → top)
DRAW_ORDER = [
    "dirt",
    "Large level",
    "medium level",
    "small level",
    "final level",
    "ground details",
    "trees",
    "buildings",
]

OVERLAY_LAYERS = [
    "trees",
    "buildings",
]

COLLIDER_MARGIN = (0, 4, 0, 0)

def get_draw_order():
    return DRAW_ORDER

def on_load(context: GameContext, manager):
    """Ensure Tutor Forest loads in tournament mode and spin up music."""
    print("[TutorForest] Preparing Tournament Mode")
    if context is None:
        context = GameContext()

    context.flags.setdefault("mode", "tournament")
    context.flags.setdefault("round", 1)
    context.flags.setdefault("max_rounds", len(get_zone_data()))
    context.flags.setdefault("char_name", context.flags.get("char_name", "classic"))
    ensure_tournament_state(context)

    if not hasattr(context, "score") or context.score is None:
        context.score = {}
    context.score.setdefault("wins", 0)

    play_music(MAP_PROFILE["music"])
    print("[TutorForest] Tournament mode locked and active.")


def on_exit(context=None, manager=None):
    print(f"[MapProfile] Exiting {MAP_PROFILE['name']}")
    stop_music()


def on_minigame_end(context, manager):
    result = context.last_result or {}
    outcome = result.get("outcome")

    context.apply_result()

    current_round = context.flags.get("round", 1)
    max_rounds = context.flags.get("max_rounds", len(get_zone_data()))

    if outcome == "win":
        next_round = current_round + 1
        context.flags["round"] = next_round
        print(f"[TutorForest] Advanced to round {next_round}")
        if next_round > max_rounds:
            from end_screens import WinGameScene

            print("[TutorForest] Champion crowned!")
            try:
                save_score(context, "tutor_forest", "Champion")
            except Exception as e:
                print(f"[TutorForest] Failed to save score: {e}")

            manager.switch(WinGameScene(manager))
        else:
            print("[TutorForest] Prepare for the next opponent.")

    elif outcome == "lose":
        from end_screens import LoseScene

        print("[TutorForest] Eliminated from the tournament.")
        try:
            save_score(context, "tutor_forest", "Eliminated")
        except Exception as e:
            print(f"[TutorForest] Failed to save score: {e}")

        manager.switch(LoseScene(manager))


def get_available_modes():
    """Tutor Forest is tournament-only."""
    return ["tournament"]


def _layer_rect_px(map_data, tile, name_ci):
    name_ci = (name_ci or "").strip().lower()
    for layer in map_data.get("layers", []):
        lname = (layer.get("name") or "").strip().lower()
        if lname != name_ci:
            continue
        if layer.get("tiles"):
            xs = [int(t.get("x", 0)) for t in layer["tiles"]]
            ys = [int(t.get("y", 0)) for t in layer["tiles"]]
            if xs and ys:
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
                return (minx * tile, miny * tile, (maxx - minx + 1) * tile, (maxy - miny + 1) * tile)
        if layer.get("data"):
            width = int(layer.get("width", map_data.get("mapWidth", 0)) or 0)
            data = layer["data"]
            filled = [i for i, tid in enumerate(data) if tid and tid > 0]
            if width > 0 and filled:
                mi, ma = min(filled), max(filled)
                minx, miny = (mi % width), (mi // width)
                maxx, maxy = (ma % width), (ma // width)
                return (minx * tile, miny * tile, (maxx - minx + 1) * tile, (maxy - miny + 1) * tile)
    return (0, 0, map_data.get("mapWidth", 0) * tile, map_data.get("mapHeight", 0) * tile)


def pick_arcade_spawn(map_data, tile):
    zx, zy, zw, zh = _layer_rect_px(map_data, tile, "large level")
    margin = 32
    px = zx + margin + (zw // 6)
    py = zy + margin + (zh // 6)
    nx = px + 64
    ny = py
    return (int(px), int(py)), (int(nx), int(ny))


def pick_tournament_spawns(map_data, tile):
    zx, zy, zw, zh = _layer_rect_px(map_data, tile, "large level")
    left = zx + 32
    right = zx + max(zw - 32, 33)
    px = (left + right) // 2
    py = zy + zh - 6
    return (int(px), int(py))


def get_barrier_rects(map_data=None, tile=16):
    """Build barrier rectangles keyed by zone name."""
    rects = {}
    if not map_data:
        print("[TutorForest] No map data provided for barriers.")
        return rects

    map_width_px = (map_data.get("mapWidth", 0) or 0) * tile or 512
    margin_x = 24
    gate_height = max(12, tile)

    manual = {
        "large level": pygame.Rect(-24, 1300, map_width_px + 48, 18),
    }

    level_offsets = {
        "medium level": tile * 3,
        "small level": tile * 4,
    }

    for zone in get_zone_data():
        name = (zone.get("name") or "").strip().lower()
        if not name:
            continue
        if name == "final level":
            print("[TutorForest] Skipping extra top barrier for final level.")
            continue
        if name in manual:
            rects[name] = manual[name].copy()
            print(f"[TutorForest] Manual barrier for '{name}' at y={manual[name].y}")
            continue

        has_layer = any(
            (layer.get("name") or "").strip().lower() == name
            for layer in map_data.get("layers", [])
        )
        if not has_layer:
            print(f"[TutorForest] Layer '{name}' not found for barriers.")
            continue

        x, y, w, h = _layer_rect_px(map_data, tile, name)
        if not w or not h:
            print(f"[TutorForest] Missing bounds for '{name}' barrier.")
            continue

        gate_y = y - gate_height // 2
        if name in level_offsets:
            target = y + level_offsets[name]
            max_y = y + h - gate_height
            gate_y = min(max(0, target), max_y)

        if name == "final level":
            gate_y = max(0, y - gate_height)

        rect = pygame.Rect(
            -margin_x,
            max(0, gate_y),
            map_width_px + margin_x * 2,
            gate_height,
        )
        rects[name] = rect
        print(f"[TutorForest] Auto barrier for '{name}' at y={rect.y}")

    print(f"[TutorForest] Created {len(rects)} barriers (manual + auto).")
    return rects


def get_zone_data():
    """Metadata for each gate with tournament sizing info."""
    return [dict(zone) for zone in TOURNAMENT_LEVELS]


def get_unlock_state(context):
    """Return which barriers should open based on wins."""
    score = getattr(context, "score", None) or {}
    wins = score.get("wins", 0)
    state = {}
    for zone in TOURNAMENT_LEVELS:
        threshold = zone["wins_required"] + zone.get("wins_to_clear", 0)
        state[zone["key"]] = wins >= threshold
    return state


def on_barrier_open(barrier_name):
    """React visually or with sound when gates open."""
    print(f"[TutorForest] Gate '{barrier_name}' opened!")


def get_spawn_points(mode_name):
    """Provide spawn hints for both arcade and tournament modes."""
    if mode_name == "tournament":
        return {"player": (320, 1850)}
    if mode_name == "arcade":
        return {"player": (220, 160), "npc": (280, 160)}
    return {}


# ---------- Tournament helpers ----------
def ensure_tournament_state(context):
    """Guarantee per-zone roster state exists."""
    if context is None:
        context = GameContext()
    flags = getattr(context, "flags", None)
    if flags is None:
        context.flags = {}
        flags = context.flags
    state = flags.setdefault(TOURNAMENT_STATE_FLAG, {"zones": {}})
    zones_bucket = state.setdefault("zones", {})
    for zone in TOURNAMENT_LEVELS:
        key = zone["key"]
        zone_state = zones_bucket.setdefault(key, {})
        zone_state.setdefault("wins", 0)
        if "remaining" not in zone_state:
            zone_state["remaining"] = zone["npc_slots"]
        else:
            zone_state["remaining"] = min(zone["npc_slots"], max(0, int(zone_state["remaining"])))
    return state


def get_zone_spawn_target(context, zone_name):
    """How many NPC should be present for this zone right now?"""
    ensure_tournament_state(context)
    key = _normalize_name(zone_name)
    zone = ZONE_LOOKUP.get(key)
    if not zone:
        return 0
    state = context.flags[TOURNAMENT_STATE_FLAG]["zones"][key]
    if state.get("wins", 0) >= zone["wins_to_clear"]:
        return 0
    remaining = state.get("remaining", zone["npc_slots"])
    if remaining <= 0:
        return 0
    return min(zone["npc_slots"], remaining)


def record_victory_and_culls(context, zone_name):
    """Update roster after a win and report how many random removals are needed."""
    ensure_tournament_state(context)
    key = _normalize_name(zone_name)
    zone = ZONE_LOOKUP.get(key)
    if not zone:
        return {"extra_random": 0, "zone_complete": False}

    state = context.flags[TOURNAMENT_STATE_FLAG]["zones"][key]
    if state.get("wins", 0) >= zone["wins_to_clear"]:
        return {
            "extra_random": 0,
            "zone_complete": True,
            "remaining": 0,
            "wins_in_zone": state.get("wins", zone["wins_to_clear"]),
            "wins_needed": zone["wins_to_clear"],
        }

    state["wins"] = min(zone["wins_to_clear"], state.get("wins", 0) + 1)
    stage_idx = state["wins"] - 1
    extra_random = zone["random_culls"][stage_idx] if stage_idx >= 0 else 0

    before = max(0, state.get("remaining", zone["npc_slots"]))
    eliminated = min(before, 1 + max(0, extra_random))
    after = before - eliminated
    state["remaining"] = after

    zone_complete = state["wins"] >= zone["wins_to_clear"]
    if zone_complete:
        state["remaining"] = 0
        context.flags[TOURNAMENT_STATE_FLAG]["last_cleared"] = key

    random_removed = max(0, eliminated - 1)

    return {
        "extra_random": extra_random,
        "random_removed": random_removed,
        "zone_complete": zone_complete,
        "remaining": state["remaining"],
        "wins_in_zone": state["wins"],
        "wins_needed": zone["wins_to_clear"],
        "zone_key": key,
    }
