import random
from sound_engine import play_music, stop_music

DRAW_ORDER = ["ground", "buildings", "tree_border", "overlay"]
TILE_OFFSET = 0

MAP_PROFILE = {
    "name": "Test Arena",
    "music": "arena_theme.ogg",
    "npc_count": 8,
    "spawn_point": (320, 180),
}

_LAST_TOURNEY_POINTS = []


def on_load(context=None, manager=None):
    print("[Profile] Test Arena loaded")
    if context:
        context.flags.setdefault("mode", "sandbox")
    play_music(MAP_PROFILE["music"])


def on_exit(context=None, manager=None):
    print(f"[MapProfile] Exiting {MAP_PROFILE['name']}")
    stop_music()


def on_minigame_end(context, manager=None):
    result = context.last_result or {}
    print(f"[TestArena] Played {result.get('minigame')} → {result.get('outcome')}")
    context.apply_result()
    print(context)


def get_draw_order():
    return DRAW_ORDER


def get_available_modes():
    """Allow both sandbox practice and tournament for Test Arena."""
    return ["sandbox", "tournament"]


# ---------- Tournament helpers ----------
def pick_tournament_spawns(map_data=None, tile=16):
    """Player spawn: pick a random perimeter point, inset from walls."""
    global _LAST_TOURNEY_POINTS
    points = _build_perimeter_points(map_data, tile, count=16, margin=96)
    if not points:
        return (tile * 2, tile * 2)
    random.shuffle(points)
    player_pt = points.pop()
    _LAST_TOURNEY_POINTS = points
    return player_pt


def get_tournament_spawn_points(count=16, zone_name=None, map_data=None, tile=16):
    """Return evenly spaced perimeter points for gatekeepers."""
    global _LAST_TOURNEY_POINTS
    if count <= 0:
        return []
    # Reuse leftover list from player spawn if available/large enough.
    if _LAST_TOURNEY_POINTS and len(_LAST_TOURNEY_POINTS) >= count:
        pts = _LAST_TOURNEY_POINTS[:count]
        return pts
    pts = _build_perimeter_points(map_data, tile, count=count, margin=96)
    _LAST_TOURNEY_POINTS = pts[1:] if pts else []
    return pts[:count]


def _build_perimeter_points(map_data, tile, count=16, margin=96):
    """Build evenly spaced points inset from map edges."""
    if map_data:
        w = map_data.get("mapWidth", 0) * tile
        h = map_data.get("mapHeight", 0) * tile
    else:
        w = h = 1856  # fallback to known size
    if w <= 0 or h <= 0:
        return []
    minx = margin
    maxx = max(margin + tile, w - margin)
    miny = margin
    maxy = max(margin + tile, h - margin)
    edges = [
        ((minx, miny), (maxx, miny)),  # top
        ((maxx, miny), (maxx, maxy)),  # right
        ((maxx, maxy), (minx, maxy)),  # bottom
        ((minx, maxy), (minx, miny)),  # left
    ]
    per_edge = [count // 4] * 4
    for i in range(count % 4):
        per_edge[i] += 1

    points = []
    for edge_idx, ((x1, y1), (x2, y2)) in enumerate(edges):
        slots = per_edge[edge_idx]
        if slots <= 0:
            continue
        for s in range(slots):
            t = (s + 0.5) / slots
            px = int(round(x1 + (x2 - x1) * t))
            py = int(round(y1 + (y2 - y1) * t))
            points.append((px, py))
    if len(points) < count:
        points.extend(points[: max(0, count - len(points))])
    random.shuffle(points)
    return points[:count]


def get_zone_data():
    """Single-zone perimeter tournament for Test Arena."""
    return [
        {
            "name": "perimeter",
            "display": "Perimeter",
            "wins_to_clear": 15,
            "wins_required": 0,
            "npc_slots": 15,  # 15 NPC + player = 16 total
        }
    ]


def ensure_tournament_state(context):
    if context is None:
        return {}
    flags = context.flags
    state = flags.setdefault("test_arena_tournament", {"zones": {}})
    z = state["zones"].setdefault("perimeter", {"wins": 0, "remaining": 15})
    z["remaining"] = min(15, max(0, int(z.get("remaining", 15))))
    return state


def get_zone_spawn_target(context, zone_name):
    ensure_tournament_state(context)
    state = context.flags["test_arena_tournament"]["zones"]["perimeter"]
    if state.get("wins", 0) >= 15:
        return 0
    return max(0, state.get("remaining", 15))


def record_victory_and_culls(context, zone_name):
    ensure_tournament_state(context)
    state = context.flags["test_arena_tournament"]["zones"]["perimeter"]
    if state.get("wins", 0) >= 15:
        return {"zone_complete": True, "remaining": 0}
    state["wins"] = min(15, state.get("wins", 0) + 1)
    before = max(0, state.get("remaining", 15))
    after = max(0, before - 1)
    state["remaining"] = after
    zone_complete = state["wins"] >= 15
    if zone_complete:
        context.flags["test_arena_tournament"]["last_cleared"] = "perimeter"
    return {
        "extra_random": 0,
        "random_removed": 0,
        "zone_complete": zone_complete,
        "remaining": after,
        "wins_in_zone": state["wins"],
        "wins_needed": 15,
        "zone_key": "perimeter",
    }
