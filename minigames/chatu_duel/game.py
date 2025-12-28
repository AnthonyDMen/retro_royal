# minigames/chatu_duel/game.py
import random
from collections import Counter
from pathlib import Path
from typing import Optional, Dict, Any, List
import pygame

from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from resource_path import resource_path

TITLE = "Chatu Duel"
MINIGAME_ID = "chatu_duel"

ASSET_DIR = Path(resource_path("minigames", "chatu_duel"))
BACKGROUND = ASSET_DIR / "background.png"  # baked board art
SPRITESHEET = (
    ASSET_DIR / "spritesheet.png"
)  # 6 cols x 2 rows: K,Q,B,H,R,P ; row0=white,row1=black

# Background native size and board box in source art (pixels)
BASE_W, BASE_H = 1080, 720
BOARD_BOX = pygame.Rect(105, 76, 400, 560)  # left, top, w, h in source art

# --- board geometry -----------------------------------------------------------
BOARD_W = 5
BOARD_H = 7
FILES = "ABCDE"
RANKS = "1234567"

# Spritesheet columns (left → right) **as specified**
SHEET_COL_INDEX = {"K": 0, "Q": 1, "B": 2, "H": 3, "R": 4, "P": 5}

# UI colors
COL_HALO = (60, 200, 255)
COL_WARN = (250, 95, 95)


def in_bounds(x, y):
    return 0 <= x < BOARD_W and 0 <= y < BOARD_H


class Piece:
    __slots__ = ("kind", "side", "x", "y")

    def __init__(self, kind, side, x, y):
        self.kind, self.side, self.x, self.y = kind, side, x, y

    def copy(self):
        return Piece(self.kind, self.side, self.x, self.y)


# =============================================================================
class ChatuDuelScene(Scene):
    def __init__(self, manager, context=None, callback=None, difficulty=1.0, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.big, self.font, self.small = load_game_fonts()
        self.screen = manager.screen
        self.w, self.h = manager.size

        # Assets
        self.bg = self._img(BACKGROUND)
        self.sheet = self._img(SPRITESHEET)
        self.sheet_cols, self.sheet_rows = 6, 2
        if self.sheet:
            self.cell_w = self.sheet.get_width() // self.sheet_cols
            self.cell_h = self.sheet.get_height() // self.sheet_rows
        else:
            self.cell_w = self.cell_h = 96  # fallback

        # Layout derived from baked art; recomputed if window resizes
        self._compute_layout()

        # Game state
        self.turn = "W"
        self.pieces = []
        self._place_start_positions()
        self.sel, self.legal = None, []
        self.captured = {"W": [], "B": []}

        # Banners / flow
        self._banner_text = None
        self._banner_subtitle = ""
        self._banner_timer = 0.0
        self._pending_outcome = None  # "win" | "lose" | "draw" | "forfeit"
        self._flash_timer = 0.0
        self.difficulty = float(difficulty) if difficulty else 1.0
        self.debug_grid = False  # toggle with G
        self.pending_payload = {}
        self.minigame_id = MINIGAME_ID
        self._completed = False
        self.forfeited = False
        self._end_reason = ""
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
        self.local_side = "W" if self.local_idx == 0 else "B"
        self.remote_side = "B" if self.local_side == "W" else "W"
        self.is_multiplayer = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.net_enabled = self.is_multiplayer
        self.labels = {
            "W": "You" if self.local_side == "W" else "Opponent",
            "B": "You" if self.local_side == "B" else "Opponent",
        }
        self.net_sync_timer = 0.0

        # Draw/tie machinery
        self.position_counts = Counter()
        self.halfmove_clock = 0  # 50-move rule (100 halfmoves)
        self._record_position()  # initial

        # Promotion UI (human/White)
        self._promo_pending = None  # {"piece": Piece, "square": (x,y)}
        self._promo_rects = {}  # "Q","R","B","H" -> Rect

        self._push_banner("Chatu Duel", 1.0)
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    def _local_turn(self):
        return (not self.net_enabled) or self.turn == self.local_side


def launch(manager, context=None, callback=None, **kwargs):
    return ChatuDuelScene(manager, context, callback, **kwargs)


# =============================================================================
class ChatuDuelScene(ChatuDuelScene):
    # ------------------- setup & helpers --------------------------------------
    def _img(self, path: Path):
        try:
            if path.exists():
                return pygame.image.load(str(path)).convert_alpha()
        except Exception:
            pass
        return None

    def _compute_layout(self):
        """Compute background scale/offset and board rect aligned to baked art."""
        self.w, self.h = self.manager.size
        self.scale = min(self.w / BASE_W, self.h / BASE_H) if self.w and self.h else 1.0
        self.scaled_w = int(BASE_W * self.scale)
        self.scaled_h = int(BASE_H * self.scale)
        self.offset_x = (self.w - self.scaled_w) // 2
        self.offset_y = (self.h - self.scaled_h) // 2
        self.sq = max(4, int((BOARD_BOX.w * self.scale) / BOARD_W))
        self.board_rect = pygame.Rect(
            self.offset_x + int(BOARD_BOX.left * self.scale),
            self.offset_y + int(BOARD_BOX.top * self.scale),
            self.sq * BOARD_W,
            self.sq * BOARD_H,
        )
        self._centers = [
            [
                (
                    self.board_rect.left + x * self.sq + self.sq // 2,
                    self.board_rect.top + y * self.sq + self.sq // 2,
                )
                for y in range(BOARD_H)
            ]
            for x in range(BOARD_W)
        ]


    def _current_scores(self):
        """Return material scores for White and Black."""
        values = {"Q": 9, "R": 5, "B": 3, "H": 3, "P": 1, "K": 0}
        w_score = sum(values[p.kind] for p in self.pieces if p.side == "W")
        b_score = sum(values[p.kind] for p in self.pieces if p.side == "B")
        return w_score, b_score

    # Back rank: B, Q, aK, H, R  (king centered); pawns in front
    def _place_start_positions(self):
        self.pieces.clear()
        # White at bottom
        wy_back, wy_pawn = BOARD_H - 1, BOARD_H - 2
        layout = [("R",0), ("H",1), ("K",2), ("Q",3), ("B",4)]
        for kind, x in layout:
            self.pieces.append(Piece(kind, "W", x, wy_back))
        for x in range(BOARD_W):
            self.pieces.append(Piece("P", "W", x, wy_pawn))
        # Black mirror at top
        by_back, by_pawn = 0, 1
        for kind, x in layout:
            self.pieces.append(Piece(kind, "B", x, by_back))
        for x in range(BOARD_W):
            self.pieces.append(Piece("P", "B", x, by_pawn))

    def piece_at(self, x, y):
        for p in self.pieces:
            if p.x == x and p.y == y:
                return p
        return None

    def is_friend(self, side, x, y):
        q = self.piece_at(x, y)
        return q is not None and q.side == side

    def is_enemy(self, side, x, y):
        q = self.piece_at(x, y)
        return q is not None and q.side != side

    # ------------------- move generation (raw) --------------------------------
    def gen_moves_basic(self, p):
        x, y, side = p.x, p.y, p.side
        mv = []

        if p.kind == "K":
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        nx, ny = x + dx, y + dy
                        if in_bounds(nx, ny) and not self.is_friend(side, nx, ny):
                            mv.append((nx, ny))

        elif p.kind == "Q":
            mv.extend(
                self._slide_dirs(
                    p,
                    (
                        (1, 0),
                        (-1, 0),
                        (0, 1),
                        (0, -1),
                        (1, 1),
                        (1, -1),
                        (-1, 1),
                        (-1, -1),
                    ),
                )
            )

        elif p.kind == "R":
            mv.extend(self._slide_dirs(p, ((1, 0), (-1, 0), (0, 1), (0, -1))))

        elif p.kind == "B":
            mv.extend(self._slide_dirs(p, ((1, 1), (1, -1), (-1, 1), (-1, -1))))

        elif p.kind == "H":
            for dx, dy in (
                (1, 2),
                (2, 1),
                (-1, 2),
                (-2, 1),
                (1, -2),
                (2, -1),
                (-1, -2),
                (-2, -1),
            ):
                nx, ny = x + dx, y + dy
                if in_bounds(nx, ny) and not self.is_friend(side, nx, ny):
                    mv.append((nx, ny))

        elif p.kind == "P":
            diry = -1 if side == "W" else 1
            ny = y + diry
            # advance
            if in_bounds(x, ny) and not self.piece_at(x, ny):
                mv.append((x, ny))
            # captures
            for dx in (-1, 1):
                nx = x + dx
                if in_bounds(nx, ny) and self.is_enemy(side, nx, ny):
                    mv.append((nx, ny))

        return mv

    def _slide_dirs(self, p, dirs):
        mv = []
        x, y, side = p.x, p.y, p.side
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            while in_bounds(nx, ny) and not self.is_friend(side, nx, ny):
                mv.append((nx, ny))
                if self.piece_at(nx, ny):
                    break
                nx += dx
                ny += dy
        return mv

    # --- legality (king safety) via snapshot play
    def legal_moves_for(self, p):
        legal = []
        for nx, ny in self.gen_moves_basic(p):
            snap = [q.copy() for q in self.pieces]
            self._apply_move_on_list(snap, (p.x, p.y), (nx, ny))
            if not self._in_check_on_list(snap, p.side):
                legal.append((nx, ny))
        return legal

    def _apply_move_on_list(self, plist, src, dst):
        sx, sy = src
        dx, dy = dst
        # capture
        victim = next((q for q in plist if q.x == dx and q.y == dy), None)
        if victim:
            plist.remove(victim)
        # move
        for q in plist:
            if q.x == sx and q.y == sy:
                q.x, q.y = dx, dy
                break

    def _in_check_on_list(self, plist, side):
        # locate king
        kx = ky = None
        for q in plist:
            if q.side == side and q.kind == "K":
                kx, ky = q.x, q.y
                break
        enemy = "B" if side == "W" else "W"

        # build enemy attack set
        atk = set()

        def occ_friend(x, y, s):
            return any((r.x == x and r.y == y and r.side == s) for r in plist)

        def occ_any(x, y):
            return any((r.x == x and r.y == y) for r in plist)

        for q in plist:
            if q.side != enemy:
                continue
            if q.kind == "P":
                dy = -1 if enemy == "W" else 1
                ny = q.y + dy
                for dx in (-1, 1):
                    nx = q.x + dx
                    if in_bounds(nx, ny):
                        atk.add((nx, ny))
            elif q.kind == "K":
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx or dy:
                            nx, ny = q.x + dx, q.y + dy
                            if in_bounds(nx, ny):
                                atk.add((nx, ny))
            elif q.kind == "H":
                for dx, dy in (
                    (1, 2),
                    (2, 1),
                    (-1, 2),
                    (-2, 1),
                    (1, -2),
                    (2, -1),
                    (-1, -2),
                    (-2, -1),
                ):
                    nx, ny = q.x + dx, q.y + dy
                    if in_bounds(nx, ny):
                        atk.add((nx, ny))
            elif q.kind in ("R", "B", "Q"):
                dirs = {
                    "R": ((1, 0), (-1, 0), (0, 1), (0, -1)),
                    "B": ((1, 1), (1, -1), (-1, 1), (-1, -1)),
                    "Q": (
                        (1, 0),
                        (-1, 0),
                        (0, 1),
                        (0, -1),
                        (1, 1),
                        (1, -1),
                        (-1, 1),
                        (-1, -1),
                    ),
                }[q.kind]
                for dx, dy in dirs:
                    nx, ny = q.x + dx, q.y + dy
                    while in_bounds(nx, ny) and not occ_friend(nx, ny, enemy):
                        atk.add((nx, ny))
                        if occ_any(nx, ny):
                            break
                        nx += dx
                        ny += dy
        return (kx, ky) in atk

    # ------------------- flow, draws, mercy rule, AI --------------------------
    def _move_piece(self, p, nx, ny):
        """Apply a real move on the live board (handles capture, halfmove clock,
        repetition table, and promotion trigger)."""
        capture = self.piece_at(nx, ny)
        if capture:
            self.captured[p.side].append(capture.kind)
            self.pieces.remove(capture)

        pawn_move = p.kind == "P"
        p.x, p.y = nx, ny

        # 50-move rule clock
        self.halfmove_clock = 0 if (capture or pawn_move) else self.halfmove_clock + 1

        # Promotion trigger
        if p.kind == "P":
            if (p.side == "W" and p.y == 0) or (p.side == "B" and p.y == BOARD_H - 1):
                if p.side == "W":
                    self._begin_promotion_ui(p)  # human picks
                else:
                    p.kind = "Q"  # AI auto-queens

    def _record_position(self):
        key = (
            self.turn,
            tuple(sorted((q.kind, q.side, q.x, q.y) for q in self.pieces)),
        )
        self.position_counts[key] += 1

    def in_check(self, side):
        kx = ky = None
        for p in self.pieces:
            if p.side == side and p.kind == "K":
                kx, ky = p.x, p.y
                break
        enemy = "B" if side == "W" else "W"
        atk = set()
        for p in self.pieces:
            if p.side != enemy:
                continue
            if p.kind == "P":
                dy = -1 if enemy == "W" else 1
                ny = p.y + dy
                for dx in (-1, 1):
                    nx = p.x + dx
                    if in_bounds(nx, ny):
                        atk.add((nx, ny))
            else:
                for mv in self.gen_moves_basic(p):
                    atk.add(mv)
        return (kx, ky) in atk

    def _any_legal_for(self, side):
        return any(self.legal_moves_for(q) for q in self.pieces if q.side == side)

    def _insufficient_material(self):
        kinds = [p.kind for p in self.pieces]
        if any(k == "P" for k in kinds):
            return False
        if any(k in ("Q", "R") for k in kinds):
            return False
        minors = sum(1 for k in kinds if k in ("B", "H"))
        return minors <= 2

    def _draw_reason(self):
        key = (
            self.turn,
            tuple(sorted((q.kind, q.side, q.x, q.y) for q in self.pieces)),
        )
        if self.position_counts.get(key, 0) >= 3:
            print("DEBUG: Threefold repetition triggered")
            return "Draw — threefold repetition"

    def _mercy_loser(self):
        """Mercy rule: if one side has ONLY a King and the other has King + any extra piece,
        the side with just a King immediately loses."""

        def count(side):
            ks = [p for p in self.pieces if p.side == side]
            nonking = [p for p in ks if p.kind != "K"]
            return len(ks), len(nonking)

        wk, w_non = count("W")
        bk, b_non = count("B")
        # single king loses if the other has any non-king piece
        if w_non == 0 and b_non >= 1:  # white only king
            return "W"
        if b_non == 0 and w_non >= 1:  # black only king
            return "B"
        return None

    def _check_end_now_for(self, side_to_move):
        self._end_reason = ""
        # Mercy rule first
        loser = self._mercy_loser()
        if loser:
            self._end_reason = "Material advantage"
            return loser

        # Draws that do not depend on whose turn it is
        reason = self._draw_reason()
        if reason:
            self._end_reason = reason
            return "DRAW"

        # Mate / stalemate
        if not self._any_legal_for(side_to_move):
            if self.in_check(side_to_move):
                self._end_reason = "Checkmate"
                return side_to_move
            self._end_reason = "Stalemate"
            return "DRAW"
        return None

    def _finish(self, loser):
        subtitle = self._end_reason or None
        reason_extra = {"reason": self._end_reason} if self._end_reason else None
        if loser == "DRAW":
            values = {"Q": 9, "R": 5, "B": 3, "H": 3, "P": 1, "K": 0}
            w_score = sum(values[p.kind] for p in self.pieces if p.side == "W")
            b_score = sum(values[p.kind] for p in self.pieces if p.side == "B")

            if w_score > b_score:
                self._queue_result(
                    "win", "Tiebreak — White wins", subtitle=subtitle, extra=reason_extra
                )
            elif b_score > w_score:
                self._queue_result(
                    "lose", "Tiebreak — Black wins", subtitle=subtitle, extra=reason_extra
                )
            else:
                self._queue_result(
                    "draw", "Draw — scores tied", subtitle=subtitle, extra=reason_extra
                )

        elif loser == "W":
            text = (
                "Material advantage — Black wins"
                if self._end_reason == "Material advantage"
                else "Checkmate — Black wins"
            )
            display_sub = (
                None
                if self._end_reason in ("Material advantage", "Checkmate")
                else subtitle
            )
            self._queue_result("lose", text, subtitle=display_sub, extra=reason_extra)

        elif loser == "B":
            text = (
                "Material advantage — White wins"
                if self._end_reason == "Material advantage"
                else "Checkmate — White wins"
            )
            display_sub = (
                None
                if self._end_reason in ("Material advantage", "Checkmate")
                else subtitle
            )
            self._queue_result("win", text, subtitle=display_sub, extra=reason_extra)

    def _ai_move(self):
        if self.net_enabled:
            return
        cand = []
        for p in self.pieces:
            if p.side != "B":
                continue
            for nx, ny in self.legal_moves_for(p):
                victim = self.piece_at(nx, ny)
                score = 0.0
                if victim:
                    score += {"K": 100, "Q": 9, "R": 5, "B": 3, "H": 3, "P": 1}.get(
                        victim.kind, 1
                    ) + random.random() * 0.1
                if p.kind == "P":
                    score += 0.05 * (ny if p.side == "B" else (BOARD_H - 1 - ny))
                cand.append((score, p, (nx, ny)))
        if not cand:
            return
        cand.sort(key=lambda t: t[0], reverse=True)
        _, p, (nx, ny) = cand[0]
        self._move_piece(p, nx, ny)
        self.turn = "W"
        self._record_position()   # <-- NEW

    # ------------------- promotion UI (for White) -----------------------------
    def _begin_promotion_ui(self, pawn_piece):
        self._promo_pending = {
            "piece": pawn_piece,
            "square": (pawn_piece.x, pawn_piece.y),
        }
        self._promo_rects.clear()
        cx, cy = self.board_rect.center
        size = max(40, self.sq - 8)
        pad = max(8, self.sq // 8)
        labels = [("Q", "Queen"), ("R", "Rook"), ("B", "Bishop"), ("H", "Knight")]
        total_w = 4 * size + 3 * pad
        x0 = cx - total_w // 2
        y0 = cy - size // 2
        for i, (kind, _name) in enumerate(labels):
            r = pygame.Rect(x0 + i * (size + pad), y0, size, size)
            self._promo_rects[kind] = r

    def _handle_promotion_click(self, pos):
        for kind, r in self._promo_rects.items():
            if r.collidepoint(pos):
                self._promo_pending["piece"].kind = kind
                self._promo_pending = None
                return True
        return False

    # ------------------- input ------------------------------------------------
    def handle_event(self, event):
        if self._pending_outcome and self._banner_timer <= 0:
            self._finalize(self._pending_outcome)
            return

        if self._pending_outcome and self._banner_timer > 0:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._banner_timer = 0
                self._finalize(self._pending_outcome)
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        # Promotion choice blocks everything except clicking a button
        if self._promo_pending:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._handle_promotion_click(event.pos):
                    self._record_position()
                    self._end_after_player_move()
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_g:
            self.debug_grid = not self.debug_grid

        if self._banner_timer > 0:
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._local_turn() and self.board_rect.collidepoint(mx, my):
                x = (mx - self.board_rect.left) // self.sq
                y = (my - self.board_rect.top) // self.sq
                self._click_board(x, y)

    def _click_board(self, x, y):
        if self.sel is None:
            p = self.piece_at(x, y)
            if p and p.side == self.local_side:
                self.sel = (x, y)
                self.legal = self.legal_moves_for(p)
        else:
            sx, sy = self.sel
            p = self.piece_at(sx, sy)
            if p and (x, y) in self.legal:
                self._move_piece(p, x, y)
                self.sel, self.legal = None, []
                if not self._promo_pending:
                    self._end_after_player_move()
            else:
                q = self.piece_at(x, y)
                if q and q.side == self.local_side:
                    self.sel = (x, y)
                    self.legal = self.legal_moves_for(q)
                else:
                    self.sel, self.legal = None, []

    def _end_after_player_move(self):
        # Advance turn to opponent.
        self.turn = "B" if self.turn == "W" else "W"
        self._record_position()
        loser = self._check_end_now_for(self.turn)
        if loser:
            self._finish(loser)
        else:
            if self.in_check(self.turn):
                self._flash_timer = 0.9
            if not self.net_enabled and self.turn != self.local_side:
                self._ai_move()
                self._record_position()
                loser = self._check_end_now_for(self.turn)
                if loser:
                    self._finish(loser)
                elif self.in_check(self.turn):
                    self._flash_timer = 0.9
        if self.net_enabled:
            self._net_send_state(kind="state", force=True)

    # ------------------- loop & draw -----------------------------------------
    def _push_banner(self, text, seconds, subtitle=None):
        self._banner_text = text
        self._banner_subtitle = subtitle or ""
        self._banner_timer = float(seconds)

    def _build_payload(self, extra=None):
        w_score, b_score = self._current_scores()
        payload = {
            "player_score": w_score,
            "enemy_score": b_score,
            "captured": {side: list(kinds) for side, kinds in self.captured.items()},
            "difficulty": self.difficulty,
            "halfmove_clock": self.halfmove_clock,
            "forfeit": self.forfeited,
        }
        if extra:
            payload.update(extra)
        return payload

    # ------------------- networking helpers ----------------------------------
    def _pack_state(self, kind="state", extra=None):
        state = {
            "kind": kind,
            "turn": self.turn,
            "pieces": [{"k": p.kind, "s": p.side, "x": p.x, "y": p.y} for p in self.pieces],
            "captured": {side: list(kinds) for side, kinds in self.captured.items()},
            "halfmove_clock": self.halfmove_clock,
            "banner_text": self._banner_text,
            "banner_subtitle": self._banner_subtitle,
            "banner_timer": self._banner_timer,
            "flash": self._flash_timer,
            "end_reason": self._end_reason,
            "payload": self.pending_payload or {},
        }
        if extra:
            state.update(extra)
        return state

    def _apply_state(self, state: Dict[str, Any]):
        if not state:
            return
        ps = []
        for d in state.get("pieces", []):
            try:
                ps.append(Piece(d.get("k"), d.get("s"), int(d.get("x", 0)), int(d.get("y", 0))))
            except Exception:
                continue
        if ps:
            self.pieces = ps
        self.sel, self.legal = None, []
        self._promo_pending = None
        cap = state.get("captured") or {}
        self.captured = {
            "W": list(cap.get("W", [])),
            "B": list(cap.get("B", [])),
        }
        self.turn = state.get("turn", self.turn)
        try:
            self.halfmove_clock = int(state.get("halfmove_clock", self.halfmove_clock))
        except Exception:
            pass
        self.position_counts = Counter()
        self._record_position()
        self._banner_text = state.get("banner_text", self._banner_text)
        self._banner_subtitle = state.get("banner_subtitle", self._banner_subtitle)
        self._banner_timer = float(state.get("banner_timer", self._banner_timer or 0))
        self._flash_timer = float(state.get("flash", self._flash_timer or 0))
        self._end_reason = state.get("end_reason", self._end_reason)
        self.pending_payload = state.get("payload", self.pending_payload)
        outcome = state.get("outcome")
        winner_side = state.get("winner_side")
        if outcome and winner_side:
            mapped = outcome
            if winner_side == "DRAW":
                mapped = "draw"
            elif winner_side == self.local_side and outcome != "draw":
                mapped = "win"
            elif winner_side == self.remote_side and outcome != "draw":
                mapped = "lose" if outcome != "forfeit" else "forfeit"
            self._pending_outcome = mapped
            if self._banner_timer <= 0:
                self._push_banner(self._banner_text or "Duel Over", 1.0, self._banner_subtitle)

    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[ChatuDuel] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        payload = self._pack_state(kind=kind, extra=extra)
        if force or self._local_turn():
            self._net_send_action(payload)

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
            self._apply_state(action)

    def _queue_result(self, outcome, text, seconds=1.8, subtitle=None, extra=None):
        self.pending_payload = self._build_payload(extra)
        self._pending_outcome = outcome
        self._push_banner(text, seconds, subtitle)
        if self.net_enabled:
            winner_side = "DRAW"
            if outcome == "win":
                winner_side = self.local_side
            elif outcome in ("lose", "forfeit"):
                winner_side = self.remote_side
            self._net_send_state(
                kind="finish",
                force=True,
                outcome=outcome,
                winner_side=winner_side,
            )

    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self._banner_timer > 0:
            self._banner_timer -= dt
            if self._banner_timer <= 0 and self._pending_outcome:
                self._finalize(self._pending_outcome)
        elif self._pending_outcome:
            self._finalize(self._pending_outcome)
        if self._flash_timer > 0:
            self._flash_timer -= dt

    def draw(self):
        # Recompute layout on resize
        if (self.w, self.h) != self.manager.size:
            self._compute_layout()
        # Background (board baked in)
        if self.bg:
            bg_scaled = pygame.transform.smoothscale(
                self.bg, (self.scaled_w, self.scaled_h)
            )
            self.screen.blit(bg_scaled, (self.offset_x, self.offset_y))
        else:
            self.screen.fill((22, 24, 28))

        # Optional alignment grid
        if self.debug_grid:
            for x in range(BOARD_W + 1):
                X = self.board_rect.left + x * self.sq
                pygame.draw.line(
                    self.screen,
                    (255, 255, 255),
                    (X, self.board_rect.top),
                    (X, self.board_rect.bottom),
                    1,
                )
            for y in range(BOARD_H + 1):
                Y = self.board_rect.top + y * self.sq
                pygame.draw.line(
                    self.screen,
                    (255, 255, 255),
                    (self.board_rect.left, Y),
                    (self.board_rect.right, Y),
                    1,
                )

        # Selection & legal indicators
        if self.sel:
            sx, sy = self.sel
            rect = pygame.Rect(
                self.board_rect.left + sx * self.sq,
                self.board_rect.top + sy * self.sq,
                self.sq,
                self.sq,
            )
            pygame.draw.rect(self.screen, COL_HALO, rect, 3, border_radius=6)
            for mx, my in self.legal:
                cx, cy = self._centers[mx][my]
                pygame.draw.circle(
                    self.screen, (0, 0, 0), (cx, cy), max(6, self.sq // 6)
                )
                pygame.draw.circle(
                    self.screen, COL_HALO, (cx, cy), max(6, self.sq // 6), 2
                )

        # Pieces
        for p in sorted(self.pieces, key=lambda q: q.kind == "P"):
            self._draw_piece(p)

        # --- Scoreboard (tie-break preview) ---
        w_score, b_score = self._current_scores()

        # White score (bottom-left)
        w_text = self.small.render(f"White Score: {w_score}", True, (220, 220, 255))
        self.screen.blit(w_text, (self.board_rect.left, self.board_rect.bottom + 5))

        # Black score (top-left)
        b_text = self.small.render(f"Black Score: {b_score}", True, (255, 220, 220))
        self.screen.blit(b_text, (self.board_rect.left, self.board_rect.top - 38))

        # Check flash / banners
        if self._flash_timer > 0:
            t = self.big.render("Check!", True, COL_WARN)
            self.screen.blit(
                t,
                t.get_rect(center=(self.board_rect.centerx, self.board_rect.top - 28)),
            )
        if self._banner_timer > 0 and self._banner_text:
            self._draw_banner(self._banner_text, self._banner_subtitle)

        # Promotion overlay
        if self._promo_pending:
            self._draw_promotion_overlay()

    def _draw_piece(self, p):
        margin = max(6, self.sq // 8)
        scale = max(4, self.sq - 2 * margin)

        if not self.sheet:
            cx, cy = self._centers[p.x][p.y]
            glyph = {"K": "K", "Q": "Q", "B": "B", "H": "N", "R": "R", "P": "P"}[p.kind]
            bg = (240, 240, 240) if p.side == "W" else (40, 40, 40)
            fg = (10, 10, 10) if p.side == "W" else (240, 240, 240)
            pygame.draw.circle(self.screen, bg, (cx, cy), scale // 2)
            t = self.font.render(glyph, True, fg)
            self.screen.blit(t, t.get_rect(center=(cx, cy)))
            return

        col = SHEET_COL_INDEX[p.kind]
        row = 0 if p.side == "W" else 1
        src = pygame.Rect(
            col * self.cell_w, row * self.cell_h, self.cell_w, self.cell_h
        )
        img = self.sheet.subsurface(src)
        img2 = pygame.transform.smoothscale(img, (scale, scale))
        x = self.board_rect.left + p.x * self.sq + (self.sq - scale) // 2
        y = self.board_rect.top + p.y * self.sq + (self.sq - scale) // 2
        self.screen.blit(img2, (x, y))

    def _draw_banner(self, text, subtitle=""):
        w, h = self.w, self.h
        dim = pygame.Surface((w, h), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self.screen.blit(dim, (0, 0))
        box = pygame.Rect(0, 0, 560, 140)
        box.center = (w // 2, h // 2)
        pygame.draw.rect(self.screen, (35, 39, 54), box, border_radius=16)
        pygame.draw.rect(self.screen, (180, 190, 220), box, 2, border_radius=16)
        t = self.big.render(text, True, (255, 236, 140))
        t_rect = t.get_rect(center=(box.centerx, box.centery - 12))
        self.screen.blit(t, t_rect)
        if subtitle:
            sub = self.font.render(subtitle, True, (220, 230, 250))
            self.screen.blit(sub, sub.get_rect(center=(box.centerx, box.centery + 28)))

    # --- promotion overlay (Q,R,B,H)
    def _draw_promotion_overlay(self):
        dim = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 150))
        self.screen.blit(dim, (0, 0))

        title = self.big.render("Promote to…", True, (255, 236, 140))
        self.screen.blit(
            title,
            title.get_rect(center=(self.board_rect.centerx, self.board_rect.top - 28)),
        )

        for kind, r in self._promo_rects.items():
            pygame.draw.rect(self.screen, (35, 39, 54), r, border_radius=10)
            pygame.draw.rect(self.screen, (180, 190, 220), r, 2, border_radius=10)
            col = SHEET_COL_INDEX[kind]
            row = 0  # white icons for picker
            src = pygame.Rect(
                col * self.cell_w, row * self.cell_h, self.cell_w, self.cell_h
            )
            img = self.sheet.subsurface(src)
            img2 = pygame.transform.smoothscale(img, (r.w - 10, r.h - 10))
            self.screen.blit(img2, (r.x + 5, r.y + 5))

    # ------------------- pause / finalize hooks -------------------------------
    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[ChatuDuel] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed or outcome is None:
            return
        self._completed = True
        self._pending_outcome = None
        if self.context is None:
            self.context = GameContext()
        if not self.pending_payload:
            self.pending_payload = self._build_payload()
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
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
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[ChatuDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[ChatuDuel] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self.forfeited = True
        extra = {"reason": "forfeit", "forfeit": True}
        self._queue_result(
            "forfeit",
            "Forfeit — Black wins",
            subtitle="You ended the duel early",
            extra=extra,
        )
