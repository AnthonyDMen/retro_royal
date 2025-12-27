"""
Mine Sweep (classic square Minesweeper)
---------------------------------------
- Solo round, ~2 minutes soft cap
- 9x9 with 10 mines by default (tweak config), or set to 7x7 with 9 mines if preferred
- First click is always safe (mines are placed AFTER first reveal)
- LMB reveal, RMB flag, MMB or Shift+LMB chord (if flags match number)
- ESC to forfeit (loss)
- HUD: timer (mm:ss), mines remaining (mines - flags), hint text fades after first move
- End flow: post-result timer then report outcome via the shared minigame callback

No external assets; rendering is vector-only via graphics.py.
"""

from __future__ import annotations
import math, random, time
from pathlib import Path
from typing import List, Optional, Tuple

import pygame
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner
from .graphics import Graphics

TITLE = "Mine Sweep"
MINIGAME_ID = "mine_sweep"

# ------------------------- CONFIG (easy tuning) -------------------------
WINDOW_W, WINDOW_H = 960, 720
BOARD_W, BOARD_H = 9, 9  # Classic 9x9
MINE_COUNT = 10
CELL = 48  # tile size in px

ROUND_LIMIT_S = 120  # soft cap
POST_RESULT_S = 1.2  # non-blocking post-result pause
ENABLE_CHORD = True
FIRST_HINT_FADE_S = 1.0
ANIM_REVEAL_S = 0.12  # subtle reveal "pop"

# Colors & number palette (classic readable)
COLORS = {
    "bg": (18, 20, 24),
    "board_bg": (28, 32, 38),
    "hidden": (56, 62, 72),
    "hidden_hi": (66, 74, 86),
    "revealed": (196, 203, 214),
    "grid": (32, 36, 42),
    "outline": (22, 25, 30),
    "flag": (220, 60, 60),
    "flag_pole": (40, 40, 48),
    "mine": (30, 30, 30),
    "det_red": (255, 60, 60),
    "hud": (230, 236, 244),
    "hud_dim": (180, 186, 194),
    "win_flash": (255, 255, 255),
}
NUMBER_COLORS = {
    1: (47, 104, 222),  # blue
    2: (38, 145, 62),  # green
    3: (214, 60, 60),  # red
    4: (112, 64, 196),  # purple
    5: (128, 28, 48),  # maroon
    6: (24, 132, 132),  # teal
    7: (30, 34, 40),  # near-black
    8: (96, 106, 118),  # gray
}

# ------------------------------- MODEL ----------------------------------

NEIGHBORS8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


class Cell:
    __slots__ = ("mine", "adj", "revealed", "flagged", "detonated", "reveal_t")

    def __init__(self):
        self.mine: bool = False
        self.adj: int = 0
        self.revealed: bool = False
        self.flagged: bool = False
        self.detonated: bool = False
        self.reveal_t: float = -1.0  # timestamp for reveal anim


class BoardModel:
    def __init__(self, w: int, h: int, mines: int):
        self.w, self.h = w, h
        self.mines_count = mines
        self.cells: List[List[Cell]] = [[Cell() for _ in range(h)] for _ in range(w)]
        self.first_click_done = False
        self.safe_revealed = 0
        self.flags_placed = 0
        self.start_time: float = 0.0
        self.elapsed: float = 0.0

    def reset(self):
        for x in range(self.w):
            for y in range(self.h):
                c = self.cells[x][y]
                c.mine = False
                c.adj = 0
                c.revealed = False
                c.flagged = False
                c.detonated = False
                c.reveal_t = -1.0
        self.first_click_done = False
        self.safe_revealed = 0
        self.flags_placed = 0
        self.start_time = 0.0
        self.elapsed = 0.0

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def neighbors(self, x: int, y: int):
        for dx, dy in NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if self.in_bounds(nx, ny):
                yield nx, ny

    def place_mines(self, exclude: Tuple[int, int], rng: Optional[random.Random] = None):
        # Uniformly place mines excluding the first-click cell
        spots = [
            (x, y) for x in range(self.w) for y in range(self.h) if (x, y) != exclude
        ]
        if rng is None:
            random.shuffle(spots)
        else:
            rng.shuffle(spots)
        for mx, my in spots[: self.mines_count]:
            self.cells[mx][my].mine = True
        self.compute_adj()

    def compute_adj(self):
        for x in range(self.w):
            for y in range(self.h):
                c = self.cells[x][y]
                if c.mine:
                    c.adj = -1
                else:
                    c.adj = sum(
                        1 for nx, ny in self.neighbors(x, y) if self.cells[nx][ny].mine
                    )

    def toggle_flag(self, x: int, y: int):
        c = self.cells[x][y]
        if c.revealed:
            return
        if c.flagged:
            c.flagged = False
            self.flags_placed = max(0, self.flags_placed - 1)
        else:
            c.flagged = True
            self.flags_placed += 1

    def reveal(self, x: int, y: int, now: float) -> str:
        c = self.cells[x][y]
        if c.revealed or c.flagged:
            return "noop"
        c.revealed = True
        c.reveal_t = now
        if c.mine:
            c.detonated = True
            return "mine"
        # safe
        self.safe_revealed += 1
        if c.adj == 0:
            self._flood_from(x, y, now)
            return "flood"
        return "ok"

    def _flood_from(self, sx: int, sy: int, now: float):
        # BFS reveal for zero-adjacent, plus numeric borders
        q = [(sx, sy)]
        visited = set(q)
        while q:
            x, y = q.pop(0)
            for nx, ny in self.neighbors(x, y):
                n = self.cells[nx][ny]
                if n.revealed or n.flagged:
                    continue
                n.revealed = True
                n.reveal_t = now
                if not n.mine:
                    self.safe_revealed += 1
                if n.adj == 0 and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    q.append((nx, ny))

    def chord(self, x: int, y: int, now: float) -> Optional[str]:
        c = self.cells[x][y]
        if not c.revealed or c.adj <= 0:
            return None
        flags = sum(1 for nx, ny in self.neighbors(x, y) if self.cells[nx][ny].flagged)
        if flags != c.adj:
            return None
        # reveal unflagged neighbors
        result = "ok"
        for nx, ny in self.neighbors(x, y):
            n = self.cells[nx][ny]
            if not n.revealed and not n.flagged:
                r = self.reveal(nx, ny, now)
                if r == "mine":
                    result = "mine"
        return result

    def is_win(self) -> bool:
        return self.safe_revealed == (self.w * self.h - self.mines_count)

    def reveal_all_mines(self):
        for x in range(self.w):
            for y in range(self.h):
                c = self.cells[x][y]
                if c.mine:
                    c.revealed = True

    def mines_remaining_ui(self) -> int:
        return max(0, self.mines_count - self.flags_placed)


# ---------------------------- CONTROLLER --------------------------------


class MineSweepScene(Scene):
    def __init__(self, manager, context=None, callback=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.minigame_id = MINIGAME_ID
        self.pending_payload = {}
        self._completed = False
        self._pending_outcome = None
        self.forfeited = False
        self.banner = EndBanner(
            duration=2.2,
            titles={
                "win": "Mines Cleared!",
                "lose": "Mine Detonated",
                "forfeit": "Mine Sweep Forfeit",
            },
        )
        self.big, self.font, self.small = load_game_fonts()
        self.screen = manager.screen
        self.clock = pygame.time.Clock()
        self.board = BoardModel(BOARD_W, BOARD_H, MINE_COUNT)
        self.board.reset()
        self.gfx = Graphics(
            cell_size=CELL,
            win_w=self.screen.get_width(),
            win_h=self.screen.get_height(),
            colors=COLORS,
            number_colors=NUMBER_COLORS,
            anim_reveal_s=ANIM_REVEAL_S,
        )

        # Center board
        self.board_px_w = BOARD_W * CELL
        self.board_px_h = BOARD_H * CELL
        self.origin_x = (self.screen.get_width() - self.board_px_w) // 2
        self.origin_y = (self.screen.get_height() - self.board_px_h) // 2

        # State
        self.state = "ready"  # "ready" -> "playing" -> "post_win"/"post_lose"
        self.post_timer = 0.0
        self.result_bool = None

        # UI helpers
        self.hover_xy: Optional[Tuple[int, int]] = None
        self.pressed_xy: Optional[Tuple[int, int]] = None
        self.first_hint_alpha = 255  # fades after first move

        # Multiplayer plumbing
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or flags.get("duel_id")
        self.participants = kwargs.get("participants") or flags.get("participants") or []
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
        self.turn_idx = 0  # idx0 starts
        self.turn_text = "Your turn" if self.turn_idx == self.local_idx else "Opponent turn"
        self._net_timer = 0.0
        self._net_interval = 0.12
        # deterministic mine seed so both clients share layouts
        seed_src = self.duel_id or str(time.time())
        self.board_seed_base = int.from_bytes(seed_src.encode("utf-8"), "little", signed=False) & 0xFFFFFFFF
        self.board_round = 0
        self.turn_time_max = 20.0
        self.turn_time_left = self.turn_time_max
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)
        self._update_turn_text()

    def _reset_board(self, next_turn: bool = False):
        self.board.reset()
        self.state = "ready"
        self.post_timer = 0.0
        self.result_bool = None
        self.first_hint_alpha = 255
        self._pending_outcome = None
        self.pending_payload = {}
        if next_turn:
            self.turn_idx = 1 - self.turn_idx
            self.board_round += 1
        self.turn_time_left = self.turn_time_max
        self._update_turn_text()
        if self.net_enabled:
            self._net_send_state(kind="board", force=True)

    def _auto_pick_random(self, now: float):
        if self._pending_outcome or self.state not in ("ready", "playing"):
            return
        # Choose any unrevealed, unflagged cell.
        avail = []
        for x in range(self.board.w):
            for y in range(self.board.h):
                c = self.board.cells[x][y]
                if not c.revealed and not c.flagged:
                    avail.append((x, y))
        if not avail:
            return
        rng = random.Random(f"{self.board_seed_base}-{self.board_round}-auto-{self.turn_idx}-{len(avail)}")
        x, y = rng.choice(avail)
        self._try_reveal(x, y, now)
        self.turn_time_left = self.turn_time_max

    # ---------- mapping helpers ----------
    def screen_to_grid(self, px: int, py: int) -> Optional[Tuple[int, int]]:
        x = (px - self.origin_x) // CELL
        y = (py - self.origin_y) // CELL
        if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
            return int(x), int(y)
        return None

    def grid_rect(self, gx: int, gy: int) -> pygame.Rect:
        return pygame.Rect(
            self.origin_x + gx * CELL, self.origin_y + gy * CELL, CELL, CELL
        )

    # ---------- main loop ----------
    # ---------- events ----------
    def handle_event(self, ev):
        if self._pending_outcome:
            if ev.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._finalize(self._pending_outcome)
            return
        if self.net_enabled and self.turn_idx != self.local_idx:
            return
        now = time.perf_counter()
        self.hover_xy = self.screen_to_grid(*pygame.mouse.get_pos())
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self._pause_game()
            elif ev.key == pygame.K_h:
                self.first_hint_alpha = 0
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self._on_mouse_down(ev, now)
        elif ev.type == pygame.MOUSEBUTTONUP:
            self.pressed_xy = None

    def _on_mouse_down(self, ev, now: float):
        if self.state not in ("ready", "playing"):
            return
        pos = pygame.mouse.get_pos()
        g = self.screen_to_grid(*pos)
        if g is None:
            self.pressed_xy = None
            return
        x, y = g
        self.pressed_xy = (x, y)

        mods = pygame.key.get_mods()
        shift = mods & pygame.KMOD_SHIFT

        if ev.button == 1:  # LMB
            if ENABLE_CHORD and shift and not self.net_enabled:
                self._try_chord(x, y, now)
            else:
                self._try_reveal(x, y, now)
        elif ev.button == 3 and not self.net_enabled:  # RMB
            self._toggle_flag(x, y)
        elif ev.button == 2 and ENABLE_CHORD and not self.net_enabled:  # MMB
            self._try_chord(x, y, now)

    # ---------- actions ----------
    def _start_timer_if_needed(self):
        if not self.board.first_click_done:
            self.board.first_click_done = True
            self.board.start_time = time.perf_counter()

    def _place_mines_if_needed(self, safe_xy: Tuple[int, int]):
        if self.board.first_click_done and self.board.safe_revealed == 0:
            # Just started; ensure mines are placed excluding safe_xy
            self.board.place_mines(safe_xy)

    def _try_reveal(self, x: int, y: int, now: float):
        if self.state not in ("ready", "playing"):
            return
        c = self.board.cells[x][y]
        if c.revealed or c.flagged:
            return
        # turn enforcement in MP
        if self.net_enabled and self.turn_idx != self.local_idx:
            return

        # First click path: place mines excluding this cell
        if not self.board.first_click_done:
            self._start_timer_if_needed()
            rng = random.Random(f"{self.board_seed_base}-{self.board_round}-{x}-{y}")
            self.board.place_mines((x, y), rng=rng)
            if self.net_enabled:
                self._net_send_state(kind="board", force=True)

        res = self.board.reveal(x, y, now)
        # Guard: never allow first move to hit a mine; reshuffle if it happens.
        if res == "mine" and self.board.safe_revealed == 0:
            self._reset_board(next_turn=False)
            rng = random.Random(f"{self.board_seed_base}-{self.board_round}-{x}-{y}-reroll")
            self.board.place_mines((x, y), rng=rng)
            res = self.board.reveal(x, y, now)
        if res == "mine":
            self._lose(x, y)
            if self.net_enabled:
                # local detonated -> send finish
                self._net_send_state(kind="finish", force=True, outcome="lose", winner=self.remote_id, loser=self.local_id, det=[x, y])
            return
        # transition to playing on first safe reveal
        if self.state == "ready":
            self.state = "playing"

        # Win check
        if self.board.is_win():
            if self.net_enabled:
                # clear board and start new one, alternate turn
                self._reset_board(next_turn=True)
                self._net_send_state(kind="board_reset", force=True, turn=self.turn_idx)
            else:
                self._win()
            return

        # hand off turn in MP
        if self.net_enabled:
            self.turn_idx = 1 - self.turn_idx
            self._net_send_state(
                kind="state",
                force=True,
                action="reveal",
                xy=[x, y],
                turn=self.turn_idx,
                board=self._pack_board_state(),
            )
            self.turn_time_left = self.turn_time_max

    def _toggle_flag(self, x: int, y: int):
        if self.state not in ("ready", "playing"):
            return
        c = self.board.cells[x][y]
        if c.revealed:
            return
        self.board.toggle_flag(x, y)

    def _try_chord(self, x: int, y: int, now: float):
        if not ENABLE_CHORD or self.state not in ("ready", "playing"):
            return
        # Chord only makes sense after first placement
        if not self.board.first_click_done:
            return
        res = self.board.chord(x, y, now)
        if res == "mine":
            self._lose(x, y)
            return
        if self.state == "ready":
            self.state = "playing"
        if self.board.is_win():
            self._win()

    def _forfeit(self):
        if self.state in ("post_win", "post_lose"):
            return
        self.state = "post_lose"
        self.forfeited = True
        self.board.reveal_all_mines()
        self.post_timer = POST_RESULT_S
        self._pending_outcome = "forfeit"
        self.banner.show("forfeit", subtitle="Forfeit")
        if self.net_enabled:
            self.pending_payload["winner"] = self.remote_id
            self.pending_payload["loser"] = self.local_id
            self._net_send_state(kind="finish", force=True, outcome="forfeit", winner=self.remote_id, loser=self.local_id)

    def _lose(self, det_x: int, det_y: int):
        self.state = "post_lose"
        self.board.reveal_all_mines()
        self.board.cells[det_x][det_y].detonated = True
        self.post_timer = POST_RESULT_S
        self._pending_outcome = "lose"
        self.banner.show("lose", subtitle="Mine detonated")
        if self.net_enabled:
            self.pending_payload["winner"] = self.remote_id
            self.pending_payload["loser"] = self.local_id
            self._net_send_state(kind="finish", force=True, outcome="lose", winner=self.remote_id, loser=self.local_id, det=[det_x, det_y])

    def _win(self):
        if self.net_enabled:
            # clear board and continue; toggle turn
            self._reset_board(next_turn=True)
            self._net_send_state(kind="board_reset", force=True, turn=self.turn_idx)
        else:
            self.state = "post_win"
            self.post_timer = POST_RESULT_S
            self._pending_outcome = "win"
            self.pending_payload = {
                "board_size": (BOARD_W, BOARD_H),
                "mines": self.board.mines_count,
                "time": int(self.board.elapsed),
                "forfeit": False,
            }
            self.banner.show("win", subtitle="All mines cleared")

    # ---------- update & draw ----------
    def update(self, dt: float):
        # poll net early
        self._net_poll_actions(float(dt))
        now = time.perf_counter()
        if self._pending_outcome:
            if self.banner.update(dt):
                self._finalize(self._pending_outcome)
            return
        if self.net_enabled and self.board.first_click_done and self.board.start_time <= 0.0:
            # If the remote board synced without a timer, start it now to avoid instant forfeits.
            self.board.start_time = now
        if (
            self.board.first_click_done
            and self.state in ("ready", "playing")
            and (not self.net_enabled or self.board.start_time > 0.0)
        ):
            self.board.elapsed = max(0.0, now - self.board.start_time)
            if ROUND_LIMIT_S > 0 and self.board.elapsed >= ROUND_LIMIT_S:
                self._forfeit()

        # per-turn timer (MP): auto reveal random cell if local timer hits 0
        if self.net_enabled and self.state in ("ready", "playing") and self._pending_outcome is None:
            if self.turn_idx == self.local_idx:
                self.turn_time_left = max(0.0, self.turn_time_left - dt)
                if self.turn_time_left <= 0.0:
                    self._auto_pick_random(now)

        # fade hint after first move
        if self.board.first_click_done and self.first_hint_alpha > 0:
            decay = int(255 * dt / max(0.0001, FIRST_HINT_FADE_S))
            self.first_hint_alpha = max(0, self.first_hint_alpha - decay)

        # post state countdown (legacy timer; kept for safety)
        if self.state in ("post_win", "post_lose"):
            self.post_timer -= dt
            return

    def draw(self):
        now = time.perf_counter()
        # background and board
        self.gfx.draw_background(self.screen)

        # draw tiles & UI
        self.gfx.draw_board(
            surface=self.screen,
            origin=(self.origin_x, self.origin_y),
            cell=CELL,
            model=self.board,
            hover_xy=self.hover_xy,
            pressed_xy=self.pressed_xy,
            now_time=now,
        )
        # HUD + result overlay
        self.gfx.draw_hud(
            surface=self.screen,
            elapsed_s=int(self.board.elapsed) if self.board.first_click_done else 0,
            mines_remaining=self.board.mines_remaining_ui(),
            help_alpha=self.first_hint_alpha,
            round_state=self.state,
            limit_s=ROUND_LIMIT_S,
        )
        if self.net_enabled:
            turn_txt = "Your turn" if self.turn_idx == self.local_idx else "Opponent turn"
            timer_txt = f"{int(self.turn_time_left+0.999):02d}s"
            t_surf = self.font.render(f"{turn_txt} • Timer {timer_txt}", True, COLORS["hud"])
            self.screen.blit(t_surf, (self.origin_x, 16 + t_surf.get_height()))
        self.gfx.draw_result_overlay(
            self.screen, self.state, self.post_timer, POST_RESULT_S
        )
        if self._pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.screen.get_width(), self.screen.get_height()))

        pygame.display.flip()

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[MineSweep] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    # --------- net helpers ----------
    def _update_turn_text(self):
        self.turn_text = "Your turn" if self.turn_idx == self.local_idx else "Opponent turn"

    def _pack_board_state(self):
        mines = []
        revealed = []
        flagged = []
        detonated = []
        for x in range(self.board.w):
            for y in range(self.board.h):
                c = self.board.cells[x][y]
                if c.mine:
                    mines.append([x, y])
                if c.revealed:
                    revealed.append([x, y])
                if c.flagged:
                    flagged.append([x, y])
                if c.detonated:
                    detonated.append([x, y])
        return {
            "mines": mines,
            "revealed": revealed,
            "flagged": flagged,
            "detonated": detonated,
            "first": self.board.first_click_done,
            "safe_revealed": self.board.safe_revealed,
            "flags_placed": self.board.flags_placed,
            "state": self.state,
            "mines_count": self.board.mines_count,
            "elapsed": self.board.elapsed,
        }

    def _apply_board_state(self, state: dict):
        if not state:
            return
        self.board.reset()
        mines = state.get("mines") or []
        if "mines_count" in state:
            try:
                self.board.mines_count = int(state.get("mines_count"))
            except Exception:
                pass
        for x, y in mines:
            if self.board.in_bounds(x, y):
                self.board.cells[x][y].mine = True
        # recompute adj
        for x in range(self.board.w):
            for y in range(self.board.h):
                if self.board.cells[x][y].mine:
                    continue
                adj = 0
                for dx, dy in NEIGHBORS8:
                    nx, ny = x + dx, y + dy
                    if self.board.in_bounds(nx, ny) and self.board.cells[nx][ny].mine:
                        adj += 1
                self.board.cells[x][y].adj = adj
        for arr in state.get("revealed") or []:
            x, y = arr
            if self.board.in_bounds(x, y):
                c = self.board.cells[x][y]
                c.revealed = True
                c.reveal_t = time.perf_counter()
        for arr in state.get("flagged") or []:
            x, y = arr
            if self.board.in_bounds(x, y):
                self.board.cells[x][y].flagged = True
        for arr in state.get("detonated") or []:
            x, y = arr
            if self.board.in_bounds(x, y):
                self.board.cells[x][y].detonated = True
                self.board.cells[x][y].revealed = True
        self.board.first_click_done = bool(state.get("first"))
        self.board.safe_revealed = int(state.get("safe_revealed", 0))
        self.board.flags_placed = int(state.get("flags_placed", 0))
        st = state.get("state")
        if st:
            self.state = st
        if "elapsed" in state:
            try:
                self.board.elapsed = float(state.get("elapsed", 0.0))
                self.board.start_time = time.perf_counter() - self.board.elapsed
            except Exception:
                self.board.start_time = time.perf_counter()
        self.turn_time_left = self.turn_time_max
        self._update_turn_text()

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[MineSweep] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        payload = {"kind": kind, "turn": self.turn_idx}
        if kind in ("board", "board_reset"):
            payload["board"] = self._pack_board_state()
        payload.update(extra or {})
        self._net_send_action(payload)

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            sender = msg.get("from")
            if sender and sender == self.local_id:
                continue
            action = msg.get("action") or {}
            self._apply_remote_action(action)

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind in ("board", "board_reset"):
            board_state = action.get("board") or {}
            prev_turn = self.turn_idx
            self.turn_idx = action.get("turn", self.turn_idx)
            self._pending_outcome = None
            self.state = "playing"
            self._apply_board_state(board_state)
            if prev_turn != self.turn_idx and self.turn_idx == self.local_idx:
                self.turn_time_left = self.turn_time_max
            self._update_turn_text()
            return
        if kind == "state":
            prev_turn = self.turn_idx
            if "turn" in action:
                self.turn_idx = action.get("turn", self.turn_idx)
            if "board" in action:
                self._apply_board_state(action.get("board") or {})
            self._pending_outcome = None
            if self.state not in ("ready", "playing"):
                self.state = "playing"
            if prev_turn != self.turn_idx and self.turn_idx == self.local_idx:
                self.turn_time_left = self.turn_time_max
            self._update_turn_text()
            return
        if kind == "finish":
            outcome = action.get("outcome")
            win_id = action.get("winner")
            lose_id = action.get("loser")
            if win_id or lose_id:
                if win_id == self.local_id:
                    mapped = "win"
                elif lose_id == self.local_id:
                    mapped = "lose"
                else:
                    mapped = outcome or "lose"
            else:
                mapped = outcome or "lose"
            self.pending_payload["winner"] = win_id
            self.pending_payload["loser"] = lose_id
            self.state = "post_lose" if mapped == "lose" else "post_win"
            self._pending_outcome = mapped
            self.post_timer = POST_RESULT_S
            self.turn_time_left = 0.0
            self.banner.show(mapped)
            return

    def _finalize(self, outcome):
        if self._completed or outcome is None:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        if not self.pending_payload:
            self.pending_payload = {
                "board_size": (BOARD_W, BOARD_H),
                "mines": MINE_COUNT,
                "time": int(self.board.elapsed),
                "forfeit": self.forfeited,
            }
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[MineSweep] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[MineSweep] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self._forfeit()


# ------------------------------- API ------------------------------------


def launch(manager, context=None, callback=None, **kwargs):
    """Shared entrypoint used by the arena/tournament controllers."""
    return MineSweepScene(manager, context, callback, **kwargs)
