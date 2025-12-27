# minigames/go_quick/game.py
"""
Go (Quick — Atari) — Minigame Module Format
- Two-file module: game.py (logic/scene/UI) + graphics.py (pure renderer)
- TITLE constant
- launch(manager, on_win, on_lose, **kwargs)
- Scene subclass with handle_event / update / draw
- Uses load_game_fonts(); vector graphics only (no assets)

Controls:
  H — toggle hint overlay (highlights capture spots for side-to-move)
  R — restart

Rules (Atari-Go):
  • First capture wins (no territory scoring).
  • Suicide illegal unless the move captures.
  • Simple ko: forbid immediate recapture at the ko point.

Timers:
  • Player (Black): 10.0s per turn (auto-move or pass on timeout).
  • NPC (White):   3.0s “thinking” delay before move.
"""

import random
from typing import List, Optional, Tuple, Set

import pygame
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from .graphics import GoRenderer

TITLE = "Go (Quick — Atari)"
MINIGAME_ID = "go_quick"
END_DELAY = 3.0

# Colors
BG = (18, 20, 24)
GRID = (210, 200, 170)
STAR = (140, 130, 100)
BLACK = (24, 24, 24)
WHITE = (238, 238, 238)
LASTMOVE = (255, 240, 120)
ACCENT = (235, 235, 245)

# Difficulty → base board-size (we'll add +2 overall for “one bigger on each side”)
SIZE_BY_DIFFICULTY = {1: 7, 2: 9, 3: 11}

Vec2 = Tuple[int, int]


def _neighbors(n: int, r: int, c: int):
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = r + dr, c + dc
        if 0 <= rr < n and 0 <= cc < n:
            yield (rr, cc)


def _group_and_liberties(board, start: Vec2):
    n = len(board)
    color = board[start[0]][start[1]]
    seen: Set[Vec2] = {start}
    libs: Set[Vec2] = set()
    stack = [start]
    while stack:
        r, c = stack.pop()
        for rr, cc in _neighbors(n, r, c):
            v = board[rr][cc]
            if v == 0:
                libs.add((rr, cc))
            elif v == color and (rr, cc) not in seen:
                seen.add((rr, cc))
                stack.append((rr, cc))
    return seen, libs


def _clone(board):
    return [row[:] for row in board]


def _remove_group(board, group: Set[Vec2]) -> int:
    k = 0
    for r, c in group:
        if board[r][c] != 0:
            board[r][c] = 0
            k += 1
    return k


class GoQuickScene(Scene):
    def __init__(
        self,
        manager,
        context=None,
        callback=None,
        difficulty: int = 2,
        size: Optional[int] = None,
        duel_id: Optional[str] = None,
        participants: Optional[list] = None,
        multiplayer_client=None,
        local_player_id: Optional[str] = None,
    ):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.big, self.font, self.small = load_game_fonts()

        self.screen = manager.screen
        self.w, self.h = manager.size
        self.minigame_id = MINIGAME_ID
        self.pending_payload = {}
        self._completed = False
        self._pending_outcome = None
        self.forfeited = False
        self.difficulty = difficulty
        self._init_size = size
        # multiplayer plumbing
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = duel_id or flags.get("duel_id")
        self.participants = participants or flags.get("participants") or []
        self.net_client = multiplayer_client or flags.get("multiplayer_client")
        self.local_id = local_player_id or flags.get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = (
            self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        )
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        # color assignment: idx0 is Black (1), idx1 is White (2)
        self.local_color = 1 if self.local_idx == 0 else 2
        self.remote_color = 3 - self.local_color
        self.winner_id = None
        self.loser_id = None
        self.net_timer = 0.0
        self.net_interval = 0.12  # ~8 Hz for small payloads

        base_n = size or SIZE_BY_DIFFICULTY.get(int(difficulty), 9)
        # Make the matrix one row/column bigger on each side → +2 overall
        self.n = max(5, min(13, base_n + 2))

        # game state
        self.board = [
            [0] * self.n for _ in range(self.n)
        ]  # 0 empty, 1 black (you), 2 white (npc)
        self.turn = 1  # 1 = Black (idx0), 2 = White (idx1)
        self.ko_point: Optional[Vec2] = None
        self.last_move: Optional[Vec2] = None
        self.end_timer = 0.0
        self.result_text: Optional[str] = None
        self.help_open = False

        # timers
        self.human_timer_max = 20.0
        self.npc_timer_max = 3.0
        self.human_timer = self.human_timer_max
        self.ai_timer = 0.0  # counts down during NPC turn
        if self.net_enabled:
            # only track local timer; opponent timer handled remotely
            self.ai_timer = 0.0
            self.npc_timer_max = 0.0

        # renderer/layout
        self.renderer = GoRenderer(
            self.n,
            side_px=min(self.w, self.h) - 120,
            margin=40,
            colors={
                "bg": BG,
                "grid": GRID,
                "star": STAR,
                "black": BLACK,
                "white": WHITE,
                "last": LASTMOVE,
            },
        )
        # initialize mapping (kept in sync every draw)
        self.board_rect = self.renderer.board_rect(self.w, self.h)
        self.cell = self.renderer.cell

        self._ended = False
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    # ---------------- rules ----------------
    def _is_on_board(self, rc: Vec2) -> bool:
        r, c = rc
        return 0 <= r < self.n and 0 <= c < self.n

    def _place_legal(self, rc: Vec2, color: int):
        """Try a move on a copy. Return (legal, captured_count, ko_point|None)."""
        r, c = rc
        if not self._is_on_board(rc):
            return False, 0, None
        if self.board[r][c] != 0:
            return False, 0, None
        if self.ko_point is not None and rc == self.ko_point:
            return False, 0, None

        B = _clone(self.board)
        B[r][c] = color
        enemy = 3 - color
        captured_total = 0
        captured_coords: List[Vec2] = []

        # capture adjacent enemy groups with no liberties
        for rr, cc in _neighbors(self.n, r, c):
            if B[rr][cc] == enemy:
                g, libs = _group_and_liberties(B, (rr, cc))
                if not libs:
                    captured_total += _remove_group(B, g)
                    captured_coords.extend(list(g))

        # suicide illegal unless it captures
        g, libs = _group_and_liberties(B, (r, c))
        if not libs and captured_total == 0:
            return False, 0, None

        # simple ko (single-stone capture marks ko point)
        new_ko = captured_coords[0] if captured_total == 1 else None
        return True, captured_total, new_ko

    def _apply_move(self, rc: Vec2, color: int) -> int:
        r, c = rc
        self.board[r][c] = color
        enemy = 3 - color
        captured_total = 0
        captured_coords: List[Vec2] = []

        for rr, cc in _neighbors(self.n, r, c):
            if self.board[rr][cc] == enemy:
                g, libs = _group_and_liberties(self.board, (rr, cc))
                if not libs:
                    captured_total += _remove_group(self.board, g)
                    captured_coords.extend(list(g))

        # safety: avoid illegal suicide state
        g, libs = _group_and_liberties(self.board, (r, c))
        if not libs and captured_total == 0:
            self.board[r][c] = 0
            return 0

        self.ko_point = captured_coords[0] if captured_total == 1 else None
        self.last_move = (r, c)
        return captured_total

    # ---------------- AI / autopick helpers ----------------
    def _legal_moves(self, color: int):
        moves = []
        for r in range(self.n):
            for c in range(self.n):
                ok, cap, _ = self._place_legal((r, c), color)
                if ok:
                    moves.append(((r, c), cap))
        return moves

    def _own_groups_in_atari(self, color: int):
        seen: Set[Vec2] = set()
        atari = []
        for r in range(self.n):
            for c in range(self.n):
                if self.board[r][c] == color and (r, c) not in seen:
                    g, libs = _group_and_liberties(self.board, (r, c))
                    seen |= g
                    if len(libs) == 1:
                        atari.append((g, libs))
        return atari

    def _pick_for_color(self, color: int) -> Optional[Vec2]:
        # 1) immediate capture if any
        caps = [(rc, cap) for rc, cap in self._legal_moves(color) if cap > 0]
        if caps:
            random.shuffle(caps)
            return caps[0][0]
        # 2) save own atari
        for _g, libs in self._own_groups_in_atari(color):
            (save_rc,) = list(libs)
            ok, _cap, _ = self._place_legal(save_rc, color)
            if ok:
                return save_rc
        # 3) heuristic adjacency
        candidates = []
        for rc, _cap in self._legal_moves(color):
            r, c = rc
            adj_own = sum(
                1 for rr, cc in _neighbors(self.n, r, c) if self.board[rr][cc] == color
            )
            adj_enemy = sum(
                1
                for rr, cc in _neighbors(self.n, r, c)
                if self.board[rr][cc] == 3 - color
            )
            score = adj_own * 2 - adj_enemy
            candidates.append((score, rc))
        if candidates:
            candidates.sort(reverse=True)
            top = [rc for s, rc in candidates if s == candidates[0][0]]
            return random.choice(top)
        return None

    def _random_legal(self, color: int) -> Optional[Vec2]:
        moves = self._legal_moves(color)
        if not moves:
            return None
        random.shuffle(moves)
        return moves[0][0]

    # ---------------- input ----------------
    def _mouse_to_intersection(self, pos: Tuple[int, int]) -> Optional[Vec2]:
        # keep rect in sync with renderer (window may resize)
        self.board_rect = self.renderer.board_rect(self.w, self.h)
        self.cell = self.renderer.cell
        x, y = pos
        bx, by, bw, bh = self.board_rect
        if not (bx <= x <= bx + bw and by <= y <= by + bh):
            return None
        # nearest grid crossing
        gx = round((x - bx) / self.cell)
        gy = round((y - by) / self.cell)
        if 0 <= gx < self.n and 0 <= gy < self.n:
            cx = bx + gx * self.cell
            cy = by + gy * self.cell
            if (x - cx) ** 2 + (y - cy) ** 2 <= (self.cell * 0.33) ** 2:
                return (gy, gx)  # rows = y, cols = x
        return None

    # ---------------- scene API ----------------
    def handle_event(self, event):
        if self._pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._finalize(self._pending_outcome)
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._pause_game()
            elif event.key == pygame.K_h:
                self.help_open = not self.help_open
            elif event.key == pygame.K_r:
                if self.net_enabled:
                    return  # avoid desync resets in multiplayer
                self.__init__(
                    self.manager,
                    self.context,
                    self.callback,
                    difficulty=self.difficulty,
                    size=self.n,
                )
                return
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.turn == self.local_color and self.end_timer <= 0:
                rc = self._mouse_to_intersection(event.pos)
                if rc is None:
                    return
                ok, _cap, _ko = self._place_legal(rc, self.local_color)
                if ok:
                    got = self._apply_move(rc, self.local_color)
                    if got > 0:
                        self._end("You captured a stone! You win!", you_win=True)
                        return
                    self.turn = self.remote_color
                    self.ai_timer = self.npc_timer_max
                    self._net_send_state(force=True, last_move=rc)

    def _end(
        self,
        text: str,
        you_win: bool = True,
        outcome: Optional[str] = None,
        send_net: bool = True,
        winner: Optional[str] = None,
        loser: Optional[str] = None,
    ):
        if self._pending_outcome:
            return
        self.result_text = text
        self.end_timer = END_DELAY
        self.you_win = you_win
        resolved = outcome or ("win" if you_win else "lose")
        self._pending_outcome = resolved
        self.pending_payload = {
            "board_size": self.n,
            "last_move": self.last_move,
            "forfeit": self.forfeited,
        }
        if self.net_enabled:
            win_id = winner or (self.local_id if you_win else self.remote_id)
            lose_id = loser or (self.remote_id if you_win else self.local_id)
            self.winner_id = win_id
            self.loser_id = lose_id
            if send_net:
                self._net_send_state(
                    kind="finish",
                    force=True,
                    outcome=resolved,
                    winner=win_id,
                    loser=lose_id,
                    last_move=self.last_move,
                )

    def update(self, dt: float):
        # poll remote actions early
        self._net_poll_actions(dt)

        if self.end_timer > 0:
            self.end_timer -= dt
            if self.end_timer <= 0 and self._pending_outcome:
                self._finalize(self._pending_outcome)
            return

        if self.turn == self.local_color:
            # human timer countdown
            self.human_timer -= dt
            if self.human_timer <= 0:
                # Auto-move: random legal; else pass
                move = self._random_legal(self.local_color)
                if move is None:
                    # pass: give turn to opponent
                    self.turn = self.remote_color
                    self.ai_timer = self.npc_timer_max
                    self.human_timer = self.human_timer_max
                    self._net_send_state(force=True)
                else:
                    got = self._apply_move(move, self.local_color)
                    if got > 0:
                        self._end("(Timeout) Auto-capture played — You win!")
                        return
                    self.turn = self.remote_color
                    self.ai_timer = self.npc_timer_max
                    self.human_timer = self.human_timer_max
                    self._net_send_state(force=True, last_move=move)

        elif self.turn == self.remote_color and not self.net_enabled:
            # NPC thinking timer
            self.ai_timer -= dt
            if self.ai_timer <= 0:
                move = self._pick_for_color(self.remote_color)
                if move is None:
                    # no legal move for NPC → you win by stalemate
                    self._end("Opponent has no legal moves. You win!", you_win=True)
                    return
                got = self._apply_move(move, self.remote_color)
                if got > 0:
                    self._end("Your stone was captured. You lose.", you_win=False)
                    return
                # back to player
                self.turn = self.local_color
                self.human_timer = self.human_timer_max

    def draw(self):
        self.screen.fill(BG)
        # keep rect/cell synced each frame
        self.board_rect = self.renderer.board_rect(self.w, self.h)
        self.cell = self.renderer.cell

        hint_moves: List[Vec2] = []
        if self.help_open and self.end_timer <= 0:
            color = self.turn
            for r in range(self.n):
                for c in range(self.n):
                    ok, cap, _ = self._place_legal((r, c), color)
                    if ok and cap > 0:
                        hint_moves.append((r, c))

        self.renderer.draw(
            self.screen,
            self.board,
            self.last_move,
            turn=self.turn,
            hint_moves=hint_moves,
        )

        # Single-line UI at top (like other minigames)
        if self.turn == self.local_color:
            secs = max(0.0, self.human_timer)
            who = "Your turn (Black)" if self.local_color == 1 else "Your turn (White)"
        else:
            secs = max(0.0, self.ai_timer)
            if self.net_enabled:
                who = "Opponent turn"
            else:
                who = "Opponent thinking…"
        ui_line = f"{TITLE}  |  {who}  |  Timer {secs:0.1f}s  |  H: hint  R: restart  Esc: pause"
        ui = self.font.render(ui_line, True, ACCENT)
        self.screen.blit(ui, (12, 10))

        # Help overlay (rules)
        if self.help_open:
            self._draw_help_overlay()

        # End banner
        if self.end_timer > 0 and self.result_text:
            self._draw_banner(self.result_text)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[GoQuick] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed or outcome is None:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        if not self.pending_payload:
            self.pending_payload = {
                "board_size": self.n,
                "last_move": self.last_move,
                "forfeit": self.forfeited,
            }
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.winner_id:
            result["winner"] = self.winner_id
        if self.loser_id:
            result["loser"] = self.loser_id
        self.context.last_result = result
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[GoQuick] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[GoQuick] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self.forfeited = True
        if self.net_enabled:
            self.winner_id = self.remote_id
            self.loser_id = self.local_id
            self._net_send_state(
                kind="finish",
                force=True,
                outcome="forfeit",
                winner=self.winner_id,
                loser=self.loser_id,
                last_move=self.last_move,
            )
        self._pending_outcome = "forfeit"
        self.pending_payload = {
            "board_size": self.n,
            "last_move": self.last_move,
            "forfeit": True,
            "reason": "forfeit",
        }
        self._finalize("forfeit")

    def _draw_help_overlay(self):
        lines = [
            "Atari-Go (Quick):",
            "• First capture wins (no territory).",
            "• No suicide unless capturing.",
            "• Simple ko: no immediate recapture at ko point.",
            "",
        ]
        text_surfs = [self.small.render(t, True, ACCENT) for t in lines]
        w = max(t.get_width() for t in text_surfs) + 24
        h = sum(t.get_height() for t in text_surfs) + 20
        box = pygame.Rect(0, 0, w, h)
        box.topleft = (12, 48)
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        s.fill((0, 0, 0, 150))
        pygame.draw.rect(s, (220, 220, 240), s.get_rect(), 1, border_radius=8)
        y = 10
        for t in text_surfs:
            s.blit(t, (12, y))
            y += t.get_height()
        self.screen.blit(s, box)

    def _draw_banner(self, text: str):
        w, h = self.w, self.h
        dim = pygame.Surface((w, h), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self.screen.blit(dim, (0, 0))
        box = pygame.Rect(0, 0, 680, 180)
        box.center = (w // 2, h // 2)
        pygame.draw.rect(self.screen, (35, 35, 55), box, border_radius=16)
        pygame.draw.rect(self.screen, (200, 200, 230), box, 2, border_radius=16)
        t = self.big.render(text, True, (255, 235, 140))
        self.screen.blit(t, t.get_rect(center=box.center))

    # ---------------- net helpers ----------------
    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[GoQuick] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force: bool = False, **extra):
        if not self.net_enabled:
            return
        self.net_timer += extra.pop("_dt", 0.0)
        if not force and self.net_timer < self.net_interval:
            return
        self.net_timer = 0.0
        payload = {
            "kind": kind,
            "turn": self.turn,
            "board": self.board,
            "ko": self.ko_point,
            "last_move": self.last_move,
            "human_timer": self.human_timer,
        }
        if extra:
            payload.update(extra)
        self._net_send_action(payload)

    def _apply_remote_state(self, action: dict):
        if not action or self._completed:
            return
        kind = action.get("kind")
        if kind == "finish":
            outcome = action.get("outcome")
            winner = action.get("winner")
            loser = action.get("loser")
            # map outcome to local view
            if winner and winner == self.local_id:
                mapped = "win"
            elif loser and loser == self.local_id:
                mapped = "lose"
            else:
                mapped = outcome or "lose"
            self.winner_id = winner
            self.loser_id = loser
            you_win = mapped == "win"
            msg = "You captured a stone! You win!" if you_win else "Your stone was captured. You lose."
            self._end(msg, you_win=you_win, outcome=mapped, send_net=False, winner=winner, loser=loser)
            return
        # sync board/turn
        prev_turn = self.turn
        board = action.get("board")
        if board:
            try:
                self.board = [list(map(int, row)) for row in board]
            except Exception:
                pass
        if "turn" in action:
            try:
                self.turn = int(action.get("turn", self.turn))
            except Exception:
                pass
        ko = action.get("ko")
        if ko is not None:
            self.ko_point = tuple(ko) if isinstance(ko, (list, tuple)) and len(ko) == 2 else None
        if "last_move" in action:
            lm = action.get("last_move")
            if isinstance(lm, (list, tuple)) and len(lm) == 2:
                self.last_move = (int(lm[0]), int(lm[1]))
        if "human_timer" in action and self.turn == self.local_color:
            try:
                self.human_timer = float(action.get("human_timer"))
            except Exception:
                pass
        # If control just returned to us, reset to full turn time.
        if prev_turn != self.turn and self.turn == self.local_color:
            self.human_timer = self.human_timer_max

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        self.net_timer += dt
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            sender = msg.get("from")
            if sender and sender == self.local_id:
                continue
            action = msg.get("action") or {}
            self._apply_remote_state(action)


def launch(manager, context=None, callback=None, **kwargs):
    return GoQuickScene(manager, context, callback, **kwargs)
