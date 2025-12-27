# minigames/domino_duel/game.py
import os, random
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pygame

from scene_manager import Scene
from content_registry import load_game_fonts  # shared font loader
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Domino Duel — Drag Demo"
MULTIPLAYER_ENABLED = True

# Spritesheet grid
CELL_W, CELL_H = 64, 128  # unrotated domino size (w x h)
GRID_ROWS = 8
GRID_COLS = 14
GRID_CELL = CELL_W  # size of a half (grid cell)

# Spritesheet layout for the 28 tiles (+2 extras)
SHEET_COLS = 7
SHEET_ROWS = 5

SPRITESHEET_NAME = "spritesheet.png"  # tiles only
BACKGROUND_NAME = "background.png"  # tropical planked table

UI_WHITE = (240, 240, 250)
OK_GREEN = (0, 160, 0)
BAD_RED = (160, 0, 0)


# ---------- spritesheet helpers ----------
def _sheet_cell_rect(index):
    c = index % SHEET_COLS
    r = index // SHEET_COLS
    return pygame.Rect(c * CELL_W, r * CELL_H, CELL_W, CELL_H)


def _tile_index_for(a, b):
    if a > b:
        a, b = b, a
    idx = 0
    for i in range(7):
        for j in range(i, 7):
            if i == a and j == b:
                return idx
            idx += 1
    return None


def _generate_tileset_surfaces(sheet_surface):
    tiles = [(i, j) for i in range(7) for j in range(i, 7)]
    tiles += [("blank", "blank"), ("back", "back")]
    surf_map = {}
    for idx, pair in enumerate(tiles):
        rect = _sheet_cell_rect(idx)
        tile_surf = pygame.Surface((CELL_W, CELL_H), pygame.SRCALPHA)
        tile_surf.blit(sheet_surface, (0, 0), rect)
        if pair == ("blank", "blank"):
            surf_map["blank"] = tile_surf
        elif pair == ("back", "back"):
            surf_map["back"] = tile_surf
        else:
            surf_map[pair] = tile_surf
    return surf_map


# ---------- simple domino struct ----------
class Tile:
    __slots__ = ("a", "b")

    def __init__(self, a, b):
        if a <= b:
            self.a, self.b = a, b
        else:
            self.a, self.b = b, a

    def key(self):
        return (self.a, self.b)


# ---------- scene ----------
class DominoDuelScene(Scene):

    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.big, self.font, self.small = load_game_fonts()
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or flags.get("duel_id")
        self.participants: List[str] = kwargs.get("participants") or flags.get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or flags.get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or flags.get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.local_side = "p1" if self.local_idx == 0 else "p2"
        self.remote_side = "p2" if self.local_side == "p1" else "p1"

        self.screen = manager.screen
        self.w, self.h = manager.size
        self.clock = pygame.time.Clock()
        self.minigame_id = "domino_duel"
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", 3.5)),
            titles={
                "win": "Domino Duel Win!",
                "lose": "Domino Duel Lost",
                "tie": "Domino Duel Tie",
                "forfeit": "Domino Duel Forfeit",
            },
        )
        self.pending_outcome = None
        self.pending_payload = {}
        self._completed = False

        root = Path(os.path.dirname(__file__))
        self.background = pygame.image.load(str(root / BACKGROUND_NAME)).convert_alpha()
        self.sheet = pygame.image.load(str(root / SPRITESHEET_NAME)).convert_alpha()
        self.tile_surfs = _generate_tileset_surfaces(self.sheet)

        # Table & play area
        self.table_rect = pygame.Rect(0, 0, int(self.h * 0.92), int(self.h * 0.92))
        self.table_rect.center = (self.w // 2, self.h // 2)

        play_w = GRID_COLS * GRID_CELL
        play_h = GRID_ROWS * GRID_CELL
        self.play_rect = pygame.Rect(0, 0, play_w, play_h)
        self.play_rect.center = (self.w // 2, self.h // 2)

        # Precompute top/bottom centered rows (NPC/Player)
        self._place_center_rows()

        # Grid overlay (for snapping)
        self.GRID_SIZE = GRID_CELL
        self.GRID_ALPHA = 90
        self._grid_surface = None
        self._rebuild_grid_surface()

        # Deal hands (deterministic when multiplayer)
        rng = random.Random(self.duel_id or kwargs.get("seed"))
        deck = [(idx, Tile(i, j)) for idx, (i, j) in enumerate([(a, b) for a in range(7) for b in range(a, 7)])]
        rng.shuffle(deck)
        p1_hand = deck[:6]
        p2_hand = deck[6:12]
        self.back_surf = self.tile_surfs["back"]

        # Player draggables
        self.drags: List[Dict[str, Any]] = []
        self.opponent_hand: List[Dict[str, Any]] = []
        if self.net_enabled:
            if self.local_side == "p1":
                self._layout_initial(p1_hand)
                self.opponent_hand = [{"id": tid, "tile": t} for tid, t in p2_hand]
            else:
                self._layout_initial(p2_hand)
                self.opponent_hand = [{"id": tid, "tile": t} for tid, t in p1_hand]
            self.npc_hand = []
        else:
            # Singleplayer vs simple NPC.
            self._layout_initial(p1_hand)
            self.npc_hand = [t for _, t in p2_hand]
        self.drag_index = None

        # Board state
        self.occ = {}  # (gx,gy) -> {tile_id, end: 'a'|'b', num}
        self.placed = []  # [{'id','tile','gx','gy','angle'}]
        self._next_id = 1

        # Two chain tips
        self.left_tip = None
        self.right_tip = None
        self.pass_count = 0

        # Turn control
        self.turn = "p1" if self.net_enabled else "player"
        self.state = "PLAY"
        self.skip_msg = ""
        self.skip_msg_timer = 0.0

    # ---------- net helpers ----------
    def _local_turn(self):
        return (not self.net_enabled) or (self.turn == self.local_side)

    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[DominoDuel] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        payload = {
            "kind": kind,
            "turn": self.turn,
            "board": self._pack_board_state(),
            "left_tip": self.left_tip,
            "right_tip": self.right_tip,
        }
        payload.update(extra)
        if force or self._local_turn():
            self._net_send_action(payload)

    def _apply_remote_move(self, action: Dict[str, Any]):
        tid = action.get("tile_id")
        tile_vals = action.get("tile")
        gx, gy = action.get("gx"), action.get("gy")
        angle = action.get("angle", 0)
        socket = action.get("socket")
        if tile_vals is None or gx is None or gy is None:
            return
        t = Tile(tile_vals[0], tile_vals[1])
        entry = {
            "tile_id": tid,
            "tile": t,
            "angle": angle,
            "pos": (
                self.play_rect.left + int(gx) * self.GRID_SIZE,
                self.play_rect.top + int(gy) * self.GRID_SIZE,
            ),
            "drag": False,
            "grab": (0, 0),
        }
        self._commit_entry(entry, socket=socket)
        # remove from opponent hand
        for idx, h in enumerate(self.opponent_hand):
            if h.get("id") == tid:
                self.opponent_hand.pop(idx)
                break
        if not self.opponent_hand and not self.pending_outcome:
            # Opponent placed all tiles; we lose.
            payload = self._build_payload("remote_out")
            self._finish("lose", "Opponent placed all tiles.", payload)

    def _pack_board_state(self):
        return [
            {"id": n["id"], "tile": (n["tile"].a, n["tile"].b), "gx": n["gx"], "gy": n["gy"], "angle": n["angle"]}
            for n in self.placed
        ]

    def _apply_board_state(self, board_state, left_tip=None, right_tip=None):
        if not board_state:
            return
        self.placed = []
        self.occ = {}
        self.left_tip = left_tip
        self.right_tip = right_tip
        max_id = 0
        for node in board_state:
            try:
                t = Tile(node["tile"][0], node["tile"][1])
                entry = {
                    "id": node.get("id"),
                    "tile": t,
                    "gx": int(node.get("gx", 0)),
                    "gy": int(node.get("gy", 0)),
                    "angle": int(node.get("angle", 0)),
                }
                max_id = max(max_id, entry["id"] or 0)
                self.placed.append(entry)
                # rebuild occ
                e = {
                    "tile": t,
                    "angle": entry["angle"],
                    "pos": (
                        self.play_rect.left + entry["gx"] * self.GRID_SIZE,
                        self.play_rect.top + entry["gy"] * self.GRID_SIZE,
                    ),
                }
                for cx, cy, which in self._cells_for_entry(e):
                    self.occ[(cx, cy)] = {
                        "tile_id": entry["id"],
                        "end": which,
                        "num": self._num_for_half(t, which),
                    }
            except Exception:
                continue
        # Ensure tips exist for first tile if missing.
        if self.placed and not (self.left_tip and self.right_tip):
            dummy_entry = {
                "tile": self.placed[0]["tile"],
                "angle": self.placed[0]["angle"],
            }
            dummy_entry["pos"] = (
                self.play_rect.left + self.placed[0]["gx"] * self.GRID_SIZE,
                self.play_rect.top + self.placed[0]["gy"] * self.GRID_SIZE,
            )
            self._init_tips_from_first(dummy_entry)
        self._next_id = max_id + 1

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            sender = msg.get("from")
            if sender and self.local_id and sender == self.local_id:
                continue
            action = msg.get("action") or {}
            kind = action.get("kind")
            board_applied = False
            if action.get("board"):
                self._apply_board_state(action.get("board"), action.get("left_tip"), action.get("right_tip"))
                board_applied = True
            if kind == "move":
                self.turn = self.local_side
                if not board_applied:
                    self._apply_remote_move(action)
                self.pass_count = 0
            elif kind == "finish":
                if self.pending_outcome or self._completed:
                    continue
                winner_side = action.get("winner_side")
                outcome = "lose"
                if winner_side == self.local_side:
                    outcome = "win"
                elif winner_side == "tie":
                    outcome = "tie"
                payload = action.get("payload", {})
                self.turn = None
                self._queue_outcome(outcome, "Duel finished", payload)
            elif kind == "forfeit":
                payload = action.get("payload", {})
                self.turn = None
                self._queue_outcome("win", "Opponent forfeited", payload)
            elif kind == "pass":
                # Opponent has no move; our turn.
                self.turn = self.local_side
                self.pass_count += 1
                if self.pass_count >= 2:
                    self._resolve_blocked_mp()
                self._show_skip_msg("Opponent skipped")
            elif kind == "replay":
                # Remote requested a replay after a tie; reset state.
                self._reset_for_replay()

    # --- centered rows above/below the play area ---
    def _place_center_rows(self, tiles_count=6, gap=10, margin=12):
        """Compute centered rows above/below the play area for NPC (top) and player (bottom)."""
        tile_w = CELL_H  # row tiles are drawn rotated 90°
        row_w = tiles_count * tile_w + (tiles_count - 1) * gap
        start_x = self.play_rect.centerx - row_w // 2
        top_y = max(self.table_rect.top + margin, self.play_rect.top - margin - CELL_W)
        bottom_y = min(
            self.table_rect.bottom - CELL_W - margin, self.play_rect.bottom + margin
        )
        self._npc_row = (start_x, top_y, gap, tile_w)
        self._ply_row = (start_x, bottom_y, gap, tile_w)

    # --- initial player hand layout (straight row of 6) ---
    def _layout_initial(self, tiles):
        self.drags.clear()
        tiles = tiles[:6]
        start_x, y, gap, tile_w = self._ply_row
        for i, (tid, t) in enumerate(tiles):
            x = start_x + i * (tile_w + gap)
            pos = (x, y)
            self.drags.append(
                {
                    "tile_id": tid,
                    "tile": t,
                    "pos": pos,
                    "home": pos,  # snap-back target
                    "angle": 90,  # horizontal in the row
                    "drag": False,
                    "grab": (0, 0),
                }
            )

    # --- grid overlay ---
    def _rebuild_grid_surface(self):
        w, h = self.play_rect.w, self.play_rect.h
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        color = (220, 220, 220, self.GRID_ALPHA)
        step = self.GRID_SIZE
        x = 0
        while x <= w:
            pygame.draw.line(s, color, (x, 0), (x, h), 1)
            x += step
        y = 0
        while y <= h:
            pygame.draw.line(s, color, (0, y), (w, y), 1)
            y += step
        self._grid_surface = s

    # --- small helpers for drag/orient/snap ---
    def _rotated(self, entry):
        surf = self.tile_surfs[entry["tile"].key()]
        return pygame.transform.rotozoom(surf, entry["angle"] % 360, 1.0)

    def _tile_size_rotated(self, entry):
        surf = self._rotated(entry)
        return surf.get_width(), surf.get_height()

    def _entry_center(self, entry):
        surf = self._rotated(entry)
        x, y = entry["pos"]
        return (x + surf.get_width() // 2, y + surf.get_height() // 2)

    def _set_center(self, entry, cx, cy):
        surf = self._rotated(entry)
        entry["pos"] = (cx - surf.get_width() // 2, cy - surf.get_height() // 2)

    def _snap_to_grid_top_left(self, entry):
        w, h = self._tile_size_rotated(entry)
        gx = self.GRID_SIZE
        gy = self.GRID_SIZE
        x, y = entry["pos"]
        lx = x - self.play_rect.left
        ly = y - self.play_rect.top
        cell_x = round(lx / gx) * gx
        cell_y = round(ly / gy) * gy
        nx = self.play_rect.left + cell_x
        ny = self.play_rect.top + cell_y
        min_x, min_y = self.play_rect.left, self.play_rect.top
        max_x, max_y = self.play_rect.right - w, self.play_rect.bottom - h
        entry["pos"] = (max(min_x, min(nx, max_x)), max(min_y, min(ny, max_y)))

    def _top_left_grid_of(self, entry):
        x, y = entry["pos"]
        gx = (x - self.play_rect.left) // self.GRID_SIZE
        gy = (y - self.play_rect.top) // self.GRID_SIZE
        return int(gx), int(gy)

    def _num_for_half(self, tile, which):
        return tile.a if which == "a" else tile.b

    def _cells_for_entry(self, entry):
        gx, gy = self._top_left_grid_of(entry)
        ang = entry["angle"] % 360
        if ang in (0, 180):  # vertical
            if ang == 0:
                return [(gx, gy, "a"), (gx, gy + 1, "b")]
            else:
                return [(gx, gy, "b"), (gx, gy + 1, "a")]
        else:  # horizontal
            if ang == 90:
                return [(gx, gy, "a"), (gx + 1, gy, "b")]
            else:
                return [(gx, gy, "b"), (gx + 1, gy, "a")]

    def _end_positions(self, entry):
        ang = entry["angle"] % 360
        (gx0, gy0, _), (gx1, gy1, _) = self._cells_for_entry(entry)
        if ang in (0, 180):
            top_half = "a" if ang == 0 else "b"
            bot_half = "b" if ang == 0 else "a"
            return [
                (gx0, gy0, "top", self._num_for_half(entry["tile"], top_half)),
                (gx1, gy1, "bottom", self._num_for_half(entry["tile"], bot_half)),
            ]
        else:
            left_half = "a" if ang == 90 else "b"
            right_half = "b" if ang == 90 else "a"
            return [
                (gx0, gy0, "left", self._num_for_half(entry["tile"], left_half)),
                (gx1, gy1, "right", self._num_for_half(entry["tile"], right_half)),
            ]

    def _in_bounds_cells(self, cells):
        for gx, gy, _ in cells:
            if gx < 0 or gy < 0 or gx >= GRID_COLS or gy >= GRID_ROWS:
                return False
        return True

    def _cells_empty(self, cells):
        for gx, gy, _ in cells:
            if (gx, gy) in self.occ:
                return False
        return True

    def _hit_topmost(self, mx, my):
        for i in range(len(self.drags) - 1, -1, -1):
            entry = self.drags[i]
            x, y = entry["pos"]
            w, h = self._tile_size_rotated(entry)
            if pygame.Rect(x, y, w, h).collidepoint(mx, my):
                return i
        return None

    # --- sockets from a tip (3 per tip: straight + two turns), never overlapping the tip cell ---
    def _sockets_from_tip(self, tip, side_label):
        need = tip["num"]
        gx, gy = tip["cell"]
        f = tip["facing"]  # 'L','R','U','D'
        S = []

        def add(gx0, gy0, touch, angle_hint, new_cell, new_face):
            s = {
                "side": side_label,
                "gx": gx0,
                "gy": gy0,
                "touch": touch,
                "need": need,
                "angle_hint": angle_hint,
                "new_tip_cell": new_cell,
                "new_facing": new_face,
            }
            # Disallow sockets that would place any half outside the grid or move the tip outside.
            for cgx, cgy in self._socket_required_cells(s):
                if cgx < 0 or cgy < 0 or cgx >= GRID_COLS or cgy >= GRID_ROWS:
                    return
            if new_cell[0] < 0 or new_cell[1] < 0 or new_cell[0] >= GRID_COLS or new_cell[1] >= GRID_ROWS:
                return
            S.append(s)

        if f == "L":
            add(gx - 2, gy, "right", 90, (gx - 2, gy), "L")  # straight
            add(gx, gy - 2, "bottom", 0, (gx, gy - 2), "U")  # turn up
            add(gx, gy + 1, "top", 0, (gx, gy + 2), "D")  # turn down
        elif f == "R":
            add(gx + 1, gy, "left", 90, (gx + 2, gy), "R")
            add(gx, gy - 2, "bottom", 0, (gx, gy - 2), "U")
            add(gx, gy + 1, "top", 0, (gx, gy + 2), "D")
        elif f == "U":
            add(gx, gy - 2, "bottom", 0, (gx, gy - 2), "U")
            add(gx - 2, gy, "right", 90, (gx - 2, gy), "L")
            add(gx + 1, gy, "left", 90, (gx + 2, gy), "R")
        else:  # 'D'
            add(gx, gy + 1, "top", 0, (gx, gy + 2), "D")
            add(gx - 2, gy, "right", 90, (gx - 2, gy), "L")
            add(gx + 1, gy, "left", 90, (gx + 2, gy), "R")
        return S

    def _open_sockets(self):
        if self.left_tip is None or self.right_tip is None:
            return []
        return self._sockets_from_tip(self.left_tip, "left") + self._sockets_from_tip(
            self.right_tip, "right"
        )

    def _socket_required_cells(self, s):
        if s["touch"] in ("left", "right"):  # horizontal 2×1
            return [(s["gx"], s["gy"]), (s["gx"] + 1, s["gy"])]
        else:  # vertical 1×2
            return [(s["gx"], s["gy"]), (s["gx"], s["gy"] + 1)]

    def _socket_in_bounds_and_free(self, s):
        for gx, gy in self._socket_required_cells(s):
            if gx < 0 or gy < 0 or gx >= GRID_COLS or gy >= GRID_ROWS:
                return False
            if (gx, gy) in self.occ:
                return False
        return True

    def _tile_can_use_socket(self, entry, s):
        need = s["need"]
        a, b = entry["tile"].a, entry["tile"].b
        if need not in (a, b):
            return False
        return self._socket_in_bounds_and_free(s)

    def _auto_orient_to_side(self, entry, side_name, need_value):
        a, b = entry["tile"].a, entry["tile"].b
        if side_name == "left":
            entry["angle"] = 90 if need_value == a else 270
        elif side_name == "right":
            entry["angle"] = 90 if need_value == b else 270
        elif side_name == "top":
            entry["angle"] = 0 if need_value == a else 180
        elif side_name == "bottom":
            entry["angle"] = 0 if need_value == b else 180

    def _commit_entry(self, entry, socket=None):
        tid = entry.get("tile_id") or entry.get("id")
        if tid is None:
            tid = self._next_id
            self._next_id += 1
        else:
            try:
                tid = int(tid)
            except Exception:
                tid = self._next_id
                self._next_id += 1
        self._next_id = max(self._next_id, tid + 1)
        gx, gy = self._top_left_grid_of(entry)
        ang = entry["angle"] % 360
        self.placed.append(
            {"id": tid, "tile": entry["tile"], "gx": gx, "gy": gy, "angle": ang}
        )
        for cx, cy, which in self._cells_for_entry(entry):
            self.occ[(cx, cy)] = {
                "tile_id": tid,
                "end": which,
                "num": self._num_for_half(entry["tile"], which),
            }
        if len(self.placed) == 1:
            self._init_tips_from_first(entry)
            return tid
        if socket is not None:
            other_num = (
                entry["tile"].a
                if socket["need"] == entry["tile"].b
                else entry["tile"].b
            )
            if socket["side"] == "left":
                self.left_tip = {
                    "cell": socket["new_tip_cell"],
                    "facing": socket["new_facing"],
                    "num": other_num,
                }
            else:
                self.right_tip = {
                    "cell": socket["new_tip_cell"],
                    "facing": socket["new_facing"],
                    "num": other_num,
                }
        return tid
    def _init_tips_from_first(self, entry):
        ang = entry["angle"] % 360
        (gx0, gy0, _), (gx1, gy1, _) = self._cells_for_entry(entry)
        ends = self._end_positions(entry)
        if ang in (90, 270):
            left_num = next(n for (x, y, name, n) in ends if name == "left")
            right_num = next(n for (x, y, name, n) in ends if name == "right")
            self.left_tip = {"cell": (gx0, gy0), "facing": "L", "num": left_num}
            self.right_tip = {"cell": (gx1, gy1), "facing": "R", "num": right_num}
        else:
            top_num = next(n for (x, y, name, n) in ends if name == "top")
            bot_num = next(n for (x, y, name, n) in ends if name == "bottom")
            self.left_tip = {"cell": (gx0, gy0), "facing": "U", "num": top_num}
            self.right_tip = {"cell": (gx1, gy1), "facing": "D", "num": bot_num}

    # ---------- turn / blocked checks ----------
    def _any_legal_move_left(self):
        if not self.drags:
            return False
        if not self.placed:
            return True
        sockets = self._open_sockets()
        if not sockets:
            return False
        for e in self.drags:
            for s in sockets:
                if self._tile_can_use_socket(e, s):
                    return True
        return False

    def _npc_has_legal_move(self):
        if self.net_enabled:
            return True
        if not self.placed:
            return True
        sockets = self._open_sockets()
        if not sockets:
            return False
        for t in self.npc_hand:
            for s in sockets:
                if (
                    t.a == s["need"] or t.b == s["need"]
                ) and self._socket_in_bounds_and_free(s):
                    return True
        return False

    def _check_blocked_and_score(self):
        """
        Detect a blocked state (no legal moves for either side), award the game based on
        lowest remaining pip total, and return True if the game ended.
        """
        if self.net_enabled:
            return False
        player_can_move = self._any_legal_move_left()
        npc_can_move = self._npc_has_legal_move()
        if player_can_move or npc_can_move:
            return False

        player_pips = self._pip_total_player()
        npc_pips = self._pip_total_npc()

        if player_pips < npc_pips:
            self._finish(
                "win",
                "Blocked — You win with lower pip total.",
                self._build_payload("blocked_win", blocked=True),
            )
        elif player_pips > npc_pips:
            self._finish(
                "lose",
                "Blocked — NPC wins with lower pip total.",
                self._build_payload("blocked_lose", blocked=True),
            )
        else:
            self._finish(
                "tie",
                "Blocked — Tie on pip totals.",
                self._build_payload("blocked_tie", blocked=True),
            )
        return True

    def _pip_total_player(self):
        return sum(e["tile"].a + e["tile"].b for e in self.drags)

    def _pip_total_npc(self):
        return sum(t.a + t.b for t in self.npc_hand)
    def _pip_total_opponent(self):
        return sum(h["tile"].a + h["tile"].b for h in self.opponent_hand)

    def _build_payload(self, reason, blocked=False):
        """Pack a simple minigame result payload."""
        opp_left = len(self.npc_hand) if not self.net_enabled else len(self.opponent_hand)
        opp_pips = self._pip_total_npc() if not self.net_enabled else self._pip_total_opponent()
        return {
            "id": "domino_duel",
            "reason": reason,            # 'player_out' | 'npc_out' | 'blocked_win' | 'blocked_lose' | 'blocked_tie' | 'npc_empty_before_start'
            "blocked": blocked,
            "placed_count": len(self.placed),
            "player_tiles_left": len(self.drags),
            "npc_tiles_left": opp_left,
            "player_pips": self._pip_total_player(),
            "npc_pips": opp_pips,
        }

    def _finish(self, outcome, banner_text, payload):
        """
        outcome: 'win' | 'lose' | 'tie'
        Show the unified banner/overlay and freeze turns until the banner completes.
        """
        self.turn = None
        self._queue_outcome(outcome, banner_text, payload)
        if self.net_enabled:
            winner_side = "tie"
            if outcome == "win":
                winner_side = self.local_side
            elif outcome in ("lose", "forfeit"):
                winner_side = self.remote_side
            self._net_send_state(kind="finish", force=True, winner_side=winner_side, payload=payload, outcome=outcome)

    # ---------- input ----------
    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.net_enabled and not self._local_turn():
            return
        if not self.net_enabled and self.turn != "player":
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_s:
            if self.placed and not self._any_legal_move_left():
                self._handle_pass()
            return

        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            if event.button == 1:
                idx = self._hit_topmost(mx, my)
                if idx is not None:
                    entry = self.drags[idx]
                    surf = self._rotated(entry)
                    entry["grab"] = (surf.get_width() // 2, surf.get_height() // 2)
                    entry["drag"] = True
                    # bring to top
                    self.drags.append(self.drags.pop(idx))
                    self.drag_index = len(self.drags) - 1
                    entry = self.drags[self.drag_index]
                    entry["pos"] = (mx - entry["grab"][0], my - entry["grab"][1])
            elif event.button == 3:
                if self.drag_index is not None:
                    entry = self.drags[self.drag_index]
                    cx, cy = self._entry_center(entry)
                    entry["angle"] = (entry["angle"] + 90) % 360
                    self._set_center(entry, cx, cy)

        elif event.type == pygame.MOUSEMOTION and self.drag_index is not None:
            mx, my = event.pos
            entry = self.drags[self.drag_index]
            gx, gy = entry["grab"]
            entry["pos"] = (mx - gx, my - gy)
            self._snap_to_grid_top_left(entry)

        elif (
            event.type == pygame.MOUSEBUTTONUP
            and event.button == 1
            and self.drag_index is not None
        ):
            entry = self.drags[self.drag_index]
            chosen = None
            # snap once (so the tile rect is grid-aligned for overlap tests)
            self._snap_to_grid_top_left(entry)
            gx, gy = self._top_left_grid_of(entry)

            committed_id = None
            if not self.placed:
                # First tile: simple in-bounds/empty with current angle
                cells = self._cells_for_entry(entry)
                if not self._in_bounds_cells(cells) or not self._cells_empty(cells):
                    entry["pos"] = entry.get("home", entry["pos"])
                    entry["drag"] = False
                    self.drag_index = None
                    return
                committed_id = self._commit_entry(entry, socket=None)
            else:
                # Find the intended socket (mouse-in-rect / overlap / fallback)
                chosen = self._pick_socket_at_drop(entry, event.pos)
                if (
                    not chosen
                    or (entry["tile"].a != chosen["need"] and entry["tile"].b != chosen["need"])
                    or not self._socket_in_bounds_and_free(chosen)
                ):
                    entry["pos"] = entry.get("home", entry["pos"])
                    entry["drag"] = False
                    self.drag_index = None
                    return
                # Auto-orient to match the socket and lock to its top-left
                self._auto_orient_to_side(entry, chosen["touch"], chosen["need"])
                entry["pos"] = (
                    self.play_rect.left + chosen["gx"] * self.GRID_SIZE,
                    self.play_rect.top  + chosen["gy"] * self.GRID_SIZE,
                )
                committed_id = self._commit_entry(entry, socket=chosen)

            # Remove from hand and continue turn flow
            self.drags.pop(self.drag_index)
            self.drag_index = None

            # Player win?
            if not self.drags:
                payload = self._build_payload("player_out")
                if self.net_enabled:
                    self._finish("win", "You Win! All tiles placed.", payload)
                    self._net_send_state(kind="finish", force=True, winner_side=self.local_side, payload=payload)
                    return
                self._finish("win", "You Win! All tiles placed.", payload)
                return

            if self.net_enabled:
                self.turn = self.remote_side
                self._net_send_state(
                    kind="move",
                    force=True,
                    tile_id=committed_id,
                    tile=(entry["tile"].a, entry["tile"].b),
                    gx=gx,
                    gy=gy,
                    angle=entry.get("angle", 0),
                    socket=chosen if self.placed else None,
                    left_tip=self.left_tip,
                    right_tip=self.right_tip,
                )
                self.pass_count = 0
                return

            # NPC turn
            self.turn = "npc"
            self.pass_count = 0
            self._npc_take_turn()
            return

    def _npc_take_turn(self):
        """Very simple NPC: plays any legal move, preferring higher 'other' side."""
        if not self.placed:
            if not self.npc_hand:
                self._finish("win", "You Win! NPC has no tiles.", self._build_payload("npc_empty_before_start"))
                return
            t = max(self.npc_hand, key=lambda x: max(x.a, x.b))
            gx = max(0, min(GRID_COLS - 2, GRID_COLS // 2 - 1))
            gy = max(0, min(GRID_ROWS - 1, GRID_ROWS // 2))
            entry = {
                "tile": t,
                "angle": 90,
                "pos": (
                    self.play_rect.left + gx * self.GRID_SIZE,
                    self.play_rect.top + gy * self.GRID_SIZE,
                ),
                "drag": False,
                "grab": (0, 0),
            }
            self._commit_entry(entry, socket=None)
            self.npc_hand.remove(t)
        else:
            sockets = self._open_sockets()
            best = None  # (score, tile, socket)
            for t in list(self.npc_hand):
                for s in sockets:
                    if (
                        t.a == s["need"] or t.b == s["need"]
                    ) and self._socket_in_bounds_and_free(s):
                        other = t.b if t.a == s["need"] else t.a
                        score = other + (0.1 if s["touch"] in ("left", "top") else 0.0)
                        if best is None or score > best[0]:
                            best = (score, t, s)
            if best is None:
                self.turn = "player"
                if self._check_blocked_and_score():
                    return
                return
            _, t, s = best
            entry = {
                "tile": t,
                "angle": 0,
                "pos": (
                    self.play_rect.left + s["gx"] * self.GRID_SIZE,
                    self.play_rect.top + s["gy"] * self.GRID_SIZE,
                ),
                "drag": False,
                "grab": (0, 0),
            }
            self._auto_orient_to_side(entry, s["touch"], s["need"])
            entry["pos"] = (
                self.play_rect.left + s["gx"] * self.GRID_SIZE,
                self.play_rect.top + s["gy"] * self.GRID_SIZE,
            )
            self._commit_entry(entry, socket=s)
            self.npc_hand.remove(t)

        # npc win?
        if not self.npc_hand:
            self._finish("lose", "You Lose — NPC played all tiles.", self._build_payload("npc_out"))
            return
        self.turn = "player"
        self.pass_count = 0

    # ---------- update / draw ----------
    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return
        if self.skip_msg_timer > 0:
            self.skip_msg_timer -= dt
            if self.skip_msg_timer <= 0:
                self.skip_msg = ""

        # === normal running ===
        if not self.net_enabled:
            # Auto-pass if player has no move
            if self.turn == "player" and self.placed:
                if not self._any_legal_move_left():
                    if self._check_blocked_and_score():
                        return
                    self.turn = "npc"
                    self._npc_take_turn()
                    return
            # Global blocked check
            if self.placed and (not self._any_legal_move_left()) and (not self._npc_has_legal_move()):
                self._check_blocked_and_score()
        else:
            # If both sides have passed consecutively, resolve by pip totals.
            if self.pass_count >= 2 and not self.pending_outcome:
                self._resolve_blocked_mp()

    def _socket_rect_px(self, s):
        if s["touch"] in ("left", "right"):
            w, h = self.GRID_SIZE * 2, self.GRID_SIZE * 1
        else:
            w, h = self.GRID_SIZE * 1, self.GRID_SIZE * 2
        x = self.play_rect.left + s["gx"] * self.GRID_SIZE
        y = self.play_rect.top + s["gy"] * self.GRID_SIZE
        return pygame.Rect(x, y, w, h)

    def _pick_socket_at_drop(self, entry, mouse_pos):
        """Pick the intended socket at drop-time.
        Priority: mouse in socket rect → tile rect overlaps rect → snapped top-left equality."""
        sockets = self._open_sockets()
        if not sockets:
            return None
        mx, my = mouse_pos
        # 1) Mouse inside the green/red box
        for s in sockets:
            if self._socket_rect_px(s).collidepoint(mx, my):
                return s
        # 2) Tile rect overlaps the box (helps at edges)
        surf = self._rotated(entry)
        trect = pygame.Rect(entry["pos"], surf.get_size())
        for s in sockets:
            if self._socket_rect_px(s).colliderect(trect):
                return s
        # 3) Fallback: snapped top-left equals the socket top-left
        gx_drop, gy_drop = self._top_left_grid_of(entry)
        for s in sockets:
            if (gx_drop, gy_drop) == (s["gx"], s["gy"]):
                return s
        return None

    def draw(self):
        bg = pygame.transform.smoothscale(self.background, (self.w, self.h))
        self.screen.blit(bg, (0, 0))
        if self._grid_surface is None or self._grid_surface.get_size() != (
            self.play_rect.w,
            self.play_rect.h,
        ):
            self._rebuild_grid_surface()
        self.screen.blit(self._grid_surface, self.play_rect.topleft)

        # Opponent row (backs) — single row, no trays, no duplicates
        opp_count = len(self.npc_hand) if not self.net_enabled else len(self.opponent_hand)
        if opp_count:
            start_x, y, gap, tile_w = self._npc_row
            back = pygame.transform.rotozoom(self.back_surf, 90, 1.0)
            for i in range(opp_count):
                x = start_x + i * (tile_w + gap)
                self.screen.blit(back, (x, y))

        # play area border
        pygame.draw.rect(self.screen, (255, 255, 255), self.play_rect, 2)

        # placed tiles first
        for node in self.placed:
            surf = self.tile_surfs[(node["tile"].a, node["tile"].b)]
            rs = pygame.transform.rotozoom(surf, node["angle"], 1.0)
            px = self.play_rect.left + node["gx"] * self.GRID_SIZE
            py = self.play_rect.top + node["gy"] * self.GRID_SIZE
            self.screen.blit(rs, (px, py))

        # then player drags/hand
        for entry in self.drags:
            self.screen.blit(self._rotated(entry), entry["pos"])

        # tips line
        if self.left_tip and self.right_tip:
            t = f"Tips: L({self.left_tip['num']}) R({self.right_tip['num']})"
            self.screen.blit(self.small.render(t, True, UI_WHITE), (12, 30))

        # instructions + skip message
        tip_text = "Left-click to pick up (centers on mouse) • Right-click to rotate 90°"
        if self.net_enabled:
            tip_text += " • Press S to pass if blocked"
        tip = self.small.render(tip_text, True, UI_WHITE)
        self.screen.blit(tip, (12, 10))
        if self.skip_msg:
            msg = self.big.render(self.skip_msg, True, (255, 230, 140))
            rect = msg.get_rect(center=(self.w // 2, int(self.h * 0.12)))
            self.screen.blit(msg, rect)

        # socket hint overlay (when dragging, after first tile)
        if self.drag_index is not None and self.placed:
            dragging = self.drags[self.drag_index]
            for s in self._open_sockets():
                rect = self._socket_rect_px(s)
                ok = self._tile_can_use_socket(dragging, s)
                overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
                overlay.fill((0, 200, 0, 70) if ok else (220, 0, 0, 70))
                self.screen.blit(overlay, rect.topleft)
                pygame.draw.rect(self.screen, OK_GREEN if ok else BAD_RED, rect, 2)

        if self.pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.w, self.h))
            self._draw_end_report()
            return

    def _draw_end_report(self):
        """Overlay showing remaining tiles and pip totals for both sides."""
        pad = 10
        panel_w = int(self.w * 0.75)
        panel_h = 120
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((20, 20, 35, 215))

        player_tiles = [f"{e['tile'].a}|{e['tile'].b}" for e in self.drags]
        npc_tiles = (
            [f"{t.a}|{t.b}" for t in self.npc_hand]
            if not self.net_enabled
            else [f"{h['tile'].a}|{h['tile'].b}" for h in self.opponent_hand]
        )
        opp_pips = self._pip_total_npc() if not self.net_enabled else self._pip_total_opponent()
        lines = [
            f"Your tiles ({len(player_tiles)}) • pip total {self._pip_total_player()}: "
            + (", ".join(player_tiles) if player_tiles else "none"),
            f"Opponent tiles ({len(npc_tiles)}) • pip total {opp_pips}: "
            + (", ".join(npc_tiles) if npc_tiles else "none"),
        ]

        surface_lines = [self.small.render(text, True, UI_WHITE) for text in lines]
        y = pad
        for surf in surface_lines:
            panel.blit(surf, (pad, y))
            y += surf.get_height() + 6

        # center near the bottom of the screen
        dest = panel.get_rect(center=(self.w // 2, int(self.h * 0.78)))
        self.screen.blit(panel, dest.topleft)

    # ---------- shared finalize helpers ----------
    def _queue_outcome(self, outcome, subtitle, payload):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.pending_payload = payload or {}
        self.banner.show(outcome, subtitle=subtitle)
        self.pass_count = 0

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[DominoDuel] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload or {},
        }
        if self.duel_id:
            self.context.last_result["duel_id"] = self.duel_id
        if self.net_enabled and self.participants and len(self.participants) >= 2:
            winner = None
            loser = None
            if outcome == "win":
                winner, loser = self.local_id, self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner, loser = self.remote_id, self.local_id
            if winner:
                self.context.last_result["winner"] = winner
            if loser:
                self.context.last_result["loser"] = loser
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[DominoDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[DominoDuel] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            payload = self._build_payload("forfeit")
            self._queue_outcome("forfeit", "Forfeit", payload)
            if self.net_enabled:
                self._net_send_state(kind="forfeit", force=True, payload=payload, winner_side=self.remote_side)

    def _handle_pass(self):
        """Allow the local player to pass/skip when no legal moves remain."""
        if not self.placed or self._any_legal_move_left():
            return
        if self.net_enabled:
            self.pass_count += 1
            if self.pass_count >= 2:
                self._resolve_blocked_mp()
                return
            self.turn = self.remote_side
            self._net_send_state(kind="pass", force=True)
            self._show_skip_msg("You skipped")
            return
        # Singleplayer: let NPC take a turn; if NPC also blocked, finish via blocked scoring.
        if self._npc_has_legal_move():
            self.turn = "npc"
            self._npc_take_turn()
        else:
            self._check_blocked_and_score()

    def _resolve_blocked_mp(self):
        """Both players stuck; decide winner by lowest pip total."""
        if self.pending_outcome:
            return
        player_pips = self._pip_total_player()
        opp_pips = self._pip_total_opponent()
        if player_pips == opp_pips:
            # Force a replay by resetting the state; no ties allowed.
            self._reset_for_replay()
            self._net_send_state(kind="replay", force=True)
            return
        if player_pips < opp_pips:
            outcome = "win"
            winner_side = self.local_side
            subtitle = "Blocked — you win on pip total"
        else:
            outcome = "lose"
            winner_side = self.remote_side
            subtitle = "Blocked — opponent wins on pip total"
        payload = self._build_payload("blocked", blocked=True)
        self._finish(outcome, subtitle, payload)
        self._net_send_state(kind="finish", force=True, winner_side=winner_side, payload=payload, outcome=outcome)

    def _reset_for_replay(self):
        """Reset state for a replay when pip totals tie."""
        # Re-deal using the same seed so both players stay deterministic.
        seed = self.duel_id or None
        rng = random.Random(seed)
        deck = [(idx, Tile(i, j)) for idx, (i, j) in enumerate([(a, b) for a in range(7) for b in range(a, 7)])]
        rng.shuffle(deck)
        p1_hand = deck[:6]
        p2_hand = deck[6:12]
        self.drags.clear()
        self.opponent_hand.clear()
        if self.local_side == "p1":
            self._layout_initial(p1_hand)
            self.opponent_hand = [{"id": tid, "tile": t} for tid, t in p2_hand]
        else:
            self._layout_initial(p2_hand)
            self.opponent_hand = [{"id": tid, "tile": t} for tid, t in p1_hand]
        self.drag_index = None
        self.occ.clear()
        self.placed.clear()
        self._next_id = 1
        self.left_tip = None
        self.right_tip = None
        self.turn = "p1" if self.net_enabled else "player"
        self.pending_outcome = None
        self.pending_payload = {}
        self.pass_count = 0
        self.skip_msg = ""
        self.skip_msg_timer = 0.0

    def _show_skip_msg(self, text: str, duration: float = 1.6):
        self.skip_msg = text
        self.skip_msg_timer = duration


# ---------- factory ----------
def launch(manager, context, callback, **kwargs):
    return DominoDuelScene(manager, context, callback, **kwargs)
