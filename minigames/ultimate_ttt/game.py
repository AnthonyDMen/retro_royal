# minigames/ultimate_ttt/game.py
# Ultimate Tic-Tac-Toe (Freeplay variant) — play anywhere; ties don't count; majority wins if no global line

import pygame
import random
import time
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Ultimate Tic-Tac-Toe (Freeplay)"
MINIGAME_ID = "ultimate_ttt"

LINES = [
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
]

X_COLOR = (240, 230, 120)
O_COLOR = (130, 200, 255)
GRID_COLOR = (210, 210, 230)
SUBGRID_COLOR = (160, 160, 190)
PLAYABLE_OVERLAY = (70, 170, 110, 60)
BLOCKED_OVERLAY = (200, 90, 90, 70)
BG = (14, 16, 22)
END_BANNER_SECS = 2.5


class UltimateTTTScene(Scene):
    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.big, self.font, self.small = load_game_fonts()
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.minigame_id = MINIGAME_ID
        self.difficulty = float(kwargs.get("difficulty", 1.0))
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or flags.get("duel_id")
        raw_participants = kwargs.get("participants") or flags.get("participants") or []
        self.participants = [str(p) for p in raw_participants]
        self.net_client = kwargs.get("multiplayer_client") or flags.get("multiplayer_client")
        local_id = kwargs.get("local_player_id") or flags.get("local_player_id")
        self.local_id = str(local_id) if local_id is not None else None
        self.local_idx = 0
        if self.participants and self.local_id:
            if self.local_id in self.participants:
                self.local_idx = self.participants.index(self.local_id)
            else:
                for idx, pid in enumerate(self.participants):
                    if self.local_id in pid or pid in self.local_id:
                        self.local_idx = idx
                        self.local_id = pid
                        break
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = (
            self.participants[self.remote_idx]
            if len(self.participants) > self.remote_idx
            else None
        )
        self.net_enabled = bool(
            self.duel_id and self.participants and self.net_client and self.local_id in self.participants
        )
        self.is_authority = not self.net_enabled or self.local_idx == 0
        self.local_symbol = "X" if self.local_idx == 0 else "O"
        self.remote_symbol = "O" if self.local_symbol == "X" else "X"
        seed_val = self.duel_id if self.net_enabled else time.time()
        try:
            self.rng = random.Random(int(seed_val))
        except Exception:
            self.rng = random.Random(str(seed_val))
        self._net_last = 0.0
        self._net_interval = 1.0 / 15.0
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", END_BANNER_SECS)),
            titles={
                "win": "Ultimate TTT Cleared!",
                "lose": "Ultimate TTT Failed",
                "forfeit": "Match Forfeited",
            },
        )
        self.pending_outcome = None
        self.pending_payload = {}
        self.end_reason = ""
        self._completed = False

        # Geometry
        self.board_px = min(self.w, self.h) * 0.82
        self.cell_pad = 4
        self.left = (self.w - self.board_px) * 0.5
        self.top = (self.h - self.board_px) * 0.5

        self.sub_size = self.board_px / 3
        self.cell_size = self.sub_size / 3

        # State: 9 mini-boards, each 9 cells: "", "X", or "O"
        self.boards = [[""] * 9 for _ in range(9)]
        # Winner per mini-board: "", "X", "O", or "T" (tie/filled)
        self.local_winner = [""] * 9
        # Freeplay: no forced board targeting
        self.turn = "X"  # human starts
        self.last_move = None
        self.ending = None       # "win" or "lose" or "forfeit"
        self.has_moved = False   # Track if any move has been made

        # Simple AI knob (feel)
        self.ai_think_ms = 220
        self._ai_timer = 0
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ---------- math helpers ----------
    def _sub_rect(self, b_idx):
        br, bc = divmod(b_idx, 3)
        return pygame.Rect(
            int(self.left + bc * self.sub_size),
            int(self.top + br * self.sub_size),
            int(self.sub_size),
            int(self.sub_size),
        )

    def _cell_rect(self, b_idx, c_idx):
        sub = self._sub_rect(b_idx)
        cr, cc = divmod(c_idx, 3)
        return pygame.Rect(
            sub.x + int(cc * self.cell_size),
            sub.y + int(cr * self.cell_size),
            int(self.cell_size),
            int(self.cell_size),
        )

    def _pos_to_move(self, mx, my):
        if not (
            self.left <= mx <= self.left + self.board_px
            and self.top <= my <= self.top + self.board_px
        ):
            return None
        bc = int((mx - self.left) // self.sub_size)
        br = int((my - self.top) // self.sub_size)
        b_idx = br * 3 + bc
        sub = self._sub_rect(b_idx)
        cc = int((mx - sub.x) // self.cell_size)
        cr = int((my - sub.y) // self.cell_size)
        c_idx = cr * 3 + cc
        return (b_idx, c_idx)

    # ---------- rules ----------
    def _winner_of_cells(self, cells):
        for a, b, c in LINES:
            if cells[a] and cells[a] == cells[b] == cells[c]:
                return cells[a]
        if all(cells):
            return "T"  # tie
        return ""

    def _update_local_winner(self, b_idx):
        if self.local_winner[b_idx]:
            return self.local_winner[b_idx]
        w = self._winner_of_cells(self.boards[b_idx])
        if w:
            self.local_winner[b_idx] = w
        return w

    def _global_winner(self):
        # global considers only X/O; "T" treated as empty
        g = [w if w in ("X", "O") else "" for w in self.local_winner]
        for a, b, c in LINES:
            if g[a] and g[a] == g[b] == g[c]:
                return g[a]
        return ""

    def _majority_winner(self):
        # tie-insensitive majority
        x = sum(1 for w in self.local_winner if w == "X")
        o = sum(1 for w in self.local_winner if w == "O")
        if x > o:
            return "X"
        if o > x:
            return "O"
        return ""  # equal

    def _valid_moves(self):
        # Freeplay: any empty cell in any *open* mini-board (won/tied minis are closed)
        moves = []
        for bi in range(9):
            if self.local_winner[bi]:  # closed (X/O/T)
                continue
            for ci, v in enumerate(self.boards[bi]):
                if not v:
                    moves.append((bi, ci))
        return moves

    def _queue_outcome(self, outcome: str, reason: str = "", send_finish: bool = False):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.ending = outcome
        self.end_reason = reason or ""
        self.turn = None
        self._set_pending_payload(outcome, self.end_reason)
        self.banner.show(outcome, subtitle=self.end_reason or None)
        if send_finish:
            self._net_send_finish(outcome, self.end_reason)

    def _set_pending_payload(self, outcome: str, reason: str = ""):
        payload = {}
        if reason:
            payload["reason"] = reason
        if self.net_enabled and self.local_id and self.remote_id:
            if outcome == "win":
                payload["winner"] = self.local_id
                payload["loser"] = self.remote_id
            elif outcome in ("lose", "forfeit"):
                payload["winner"] = self.remote_id
                payload["loser"] = self.local_id
        self.pending_payload = payload

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[UltimateTTT] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome: str):
        if self._completed:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        x_wins = sum(1 for w in self.local_winner if w == "X")
        o_wins = sum(1 for w in self.local_winner if w == "O")
        t_wins = sum(1 for w in self.local_winner if w == "T")
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "stats": {"x_wins": x_wins, "o_wins": o_wins, "ties": t_wins},
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.pending_payload:
            result.update({k: v for k, v in self.pending_payload.items() if v is not None})
        if (
            self.net_enabled
            and self.participants
            and len(self.participants) >= 2
            and "winner" not in result
        ):
            if outcome == "win":
                result["winner"] = self.local_id
                result["loser"] = self.remote_id
            elif outcome in ("lose", "forfeit"):
                result["winner"] = self.remote_id
                result["loser"] = self.local_id
        self.context.last_result = result
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[UltimateTTT] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[UltimateTTT] Callback error: {exc}")

    def _apply_move(self, b_idx, c_idx, who):
        self.boards[b_idx][c_idx] = who
        self.last_move = (b_idx, c_idx)
        self._update_local_winner(b_idx)
        self.has_moved = True

    def _maybe_finish(self):
        # Only check for end after at least one move
        if not self.has_moved:
            return False
        gw = self._global_winner()
        if gw == "X":
            self._start_end("win", "3-in-a-row on the big board")
        elif gw == "O":
            reason = "Opponent made 3-in-a-row"
            if self.net_enabled:
                reason = "O made 3-in-a-row"
            self._start_end("lose", reason)
        elif not self._valid_moves():
            # Majority winner if no global line and no moves left
            x = sum(1 for w in self.local_winner if w == "X")
            o = sum(1 for w in self.local_winner if w == "O")
            if x > o:
                self._start_end("win", f"Majority minis: X {x} – O {o}")
            elif o > x:
                self._start_end("lose", f"Majority minis: O {o} – X {x}")
            else:
                self._start_end("lose", "Equal minis (tie)")  # tweak if you want neutral return
        return self.ending is not None

    # ---------- AI (simple: win/block; center/corners; random among top) ----------
    def _ai_choose(self):
        moves = self._valid_moves()
        if not moves:
            return None
        me, opp = "O", "X"

        # 1) local immediate win
        for b, c in moves:
            cells = self.boards[b][:]
            cells[c] = me
            if self._winner_of_cells(cells) == me:
                return (b, c)
        # 2) block opponent immediate win
        for b, c in moves:
            cells = self.boards[b][:]
            cells[c] = opp
            if self._winner_of_cells(cells) == opp:
                return (b, c)

        # 3) heuristics: center/corners in local; also prefer playing in minis not yet decided
        scored = []
        for b, c in moves:
            score = 0.0
            if c == 4:
                score += 0.6
            elif c in (0, 2, 6, 8):
                score += 0.35
            if b == 4:
                score += 0.4
            elif b in (0, 2, 6, 8):
                score += 0.2
            # light preference for advancing a mini already leaning O
            o_count = sum(1 for v in self.boards[b] if v == "O")
            x_count = sum(1 for v in self.boards[b] if v == "X")
            score += (o_count - x_count) * 0.05
            scored.append((score, (b, c)))
        scored.sort(reverse=True, key=lambda t: t[0])
        topk = max(1, 2)  # small randomness
        return self.rng.choice([m for _, m in scored[:topk]])

    # ---------- net helpers ----------
    def _local_turn(self):
        if not self.net_enabled:
            return self.turn == "X"
        return self.turn == self.local_symbol

    def _pack_state(self):
        return {
            "boards": [list(board) for board in self.boards],
            "local_winner": list(self.local_winner),
            "turn": self.turn,
            "last_move": list(self.last_move) if self.last_move else None,
            "has_moved": self.has_moved,
        }

    def _apply_state(self, state):
        if not state or self._completed:
            return
        boards = state.get("boards")
        if boards:
            self.boards = [list(board) for board in boards]
        local_winner = state.get("local_winner")
        if local_winner:
            self.local_winner = list(local_winner)
        self.turn = state.get("turn", self.turn)
        last_move = state.get("last_move")
        if last_move and len(last_move) == 2:
            self.last_move = (int(last_move[0]), int(last_move[1]))
        else:
            self.last_move = None
        self.has_moved = bool(state.get("has_moved", self.has_moved))

    def _net_send_action(self, payload):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[UltimateTTT] Failed to send action: {exc}")

    def _net_send_state(self, force=False):
        if not self.net_enabled or not self.is_authority:
            return
        now = time.perf_counter()
        if not force and (now - self._net_last) < self._net_interval:
            return
        self._net_last = now
        self._net_send_action({"kind": "state", "state": self._pack_state()})

    def _net_send_finish(self, outcome: str, reason: str = ""):
        if not self.net_enabled or not self.is_authority:
            return
        winner = None
        loser = None
        if self.local_id and self.remote_id:
            if outcome == "win":
                winner, loser = self.local_id, self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner, loser = self.remote_id, self.local_id
        payload = {
            "kind": "finish",
            "outcome": outcome,
            "winner": winner,
            "loser": loser,
            "reason": reason,
        }
        self._net_send_action(payload)

    def _net_poll_actions(self, dt):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            sender = msg.get("from")
            if sender and self.local_id and sender == self.local_id:
                continue
            self._apply_remote_action(msg.get("action") or {})

    def _apply_remote_action(self, action):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "finish":
            if self.pending_outcome:
                return
            winner = action.get("winner")
            loser = action.get("loser")
            outcome = action.get("outcome")
            reason = action.get("reason") or ""
            mapped = outcome or "lose"
            if winner and self.local_id:
                if winner == self.local_id:
                    mapped = "win"
                elif loser == self.local_id:
                    mapped = "lose"
            self._queue_outcome(mapped, reason, send_finish=False)
            if winner or loser or reason:
                self.pending_payload = {"winner": winner, "loser": loser, "reason": reason}
            return
        if not self.is_authority:
            return
        if kind == "move":
            move = action.get("move")
            if move and len(move) == 2:
                b_idx, c_idx = int(move[0]), int(move[1])
            else:
                b_idx = action.get("b")
                c_idx = action.get("c")
                if b_idx is None or c_idx is None:
                    return
                b_idx, c_idx = int(b_idx), int(c_idx)
            if self.turn != self.remote_symbol:
                return
            if (b_idx, c_idx) not in self._valid_moves():
                self._net_send_state(force=True)
                return
            self._apply_move(b_idx, c_idx, self.remote_symbol)
            if self._maybe_finish():
                return
            self.turn = self.local_symbol
            self._net_send_state(force=True)
            return
        if kind == "forfeit":
            if self.pending_outcome:
                return
            self._queue_outcome("win", "Opponent forfeited", send_finish=True)

    # ---------- scene API ----------
    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if not self._local_turn():
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            mc = self._pos_to_move(*pos)
            if mc and mc in self._valid_moves():
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "move", "move": [mc[0], mc[1]]})
                    return
                mover = self.local_symbol if self.net_enabled else "X"
                self._apply_move(mc[0], mc[1], mover)
                if self._maybe_finish():
                    return
                if self.net_enabled:
                    self.turn = self.remote_symbol
                    self._net_send_state(force=True)
                else:
                    self.turn = "O"
                    self._ai_timer = self.ai_think_ms

    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        if self.net_enabled and not self.is_authority:
            return

        # AI turn
        if not self.net_enabled and self.turn == "O":
            self._ai_timer -= dt * 1000  # dt is seconds; timer is milliseconds
            if self._ai_timer <= 0:
                mv = self._ai_choose()
                if mv:
                    self._apply_move(mv[0], mv[1], "O")
                if self._maybe_finish():
                    return
                self.turn = "X"
        if self.net_enabled and self.is_authority:
            self._net_send_state()

    def draw(self):
        surf = self.screen
        surf.fill(BG)
        allowed = set(self._valid_moves())
        self._draw_grid_base(surf, allowed)
        self._draw_marks(surf)
        self._draw_local_wins(surf)
        self._draw_hud(surf)
        if self.pending_outcome:
            self.banner.draw(surf, self.big, self.small, (self.w, self.h))

    def _start_end(self, result: str, reason: str = ""):
        self._queue_outcome(result, reason, send_finish=self.net_enabled and self.is_authority)

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        if self.net_enabled and not self.is_authority:
            self._queue_outcome("forfeit", "Forfeit")
            self._net_send_action({"kind": "forfeit"})
            return
        self._queue_outcome("forfeit", "Forfeit", send_finish=self.net_enabled and self.is_authority)

    # ---------- drawing ----------
    def _draw_grid_base(self, surf, allowed):
        for b in range(9):
            sub = self._sub_rect(b)
            pad = 6
            r = sub.inflate(-pad, -pad)
            overlay = pygame.Surface(r.size, pygame.SRCALPHA)
            if any((b, c) in allowed for c in range(9)):
                overlay.fill(PLAYABLE_OVERLAY)
            else:
                overlay.fill(BLOCKED_OVERLAY)
            surf.blit(overlay, r.topleft)

        # fine grid
        for i in range(10):
            x = int(self.left + i * self.cell_size)
            y0, y1 = int(self.top), int(self.top + self.board_px)
            pygame.draw.line(surf, SUBGRID_COLOR, (x, y0), (x, y1), 1)
            y = int(self.top + i * self.cell_size)
            x0, x1 = int(self.left), int(self.left + self.board_px)
            pygame.draw.line(surf, SUBGRID_COLOR, (x0, y), (x1, y), 1)

        # big dividers
        for i in range(4):
            x = int(self.left + i * self.sub_size)
            pygame.draw.line(
                surf,
                GRID_COLOR,
                (x, int(self.top)),
                (x, int(self.top + self.board_px)),
                4,
            )
            y = int(self.top + i * self.sub_size)
            pygame.draw.line(
                surf,
                GRID_COLOR,
                (int(self.left), y),
                (int(self.left + self.board_px), y),
                4,
            )

    def _draw_marks(self, surf):
        for b in range(9):
            for c, v in enumerate(self.boards[b]):
                if not v:
                    continue
                r = self._cell_rect(b, c).inflate(-self.cell_pad, -self.cell_pad)
                if v == "X":
                    pygame.draw.line(surf, X_COLOR, r.topleft, r.bottomright, 5)
                    pygame.draw.line(surf, X_COLOR, r.topright, r.bottomleft, 5)
                else:
                    pygame.draw.ellipse(surf, O_COLOR, r, 5)
        if self.last_move:
            b, c = self.last_move
            r = self._cell_rect(b, c).inflate(-self.cell_pad // 2, -self.cell_pad // 2)
            pygame.draw.rect(surf, (255, 255, 255), r, 2, border_radius=6)

    def _draw_local_wins(self, surf):
        for b in range(9):
            w = self.local_winner[b]
            if not w:
                continue
            sub = self._sub_rect(b).inflate(-16, -16)
            if w == "X":
                pygame.draw.line(
                    surf, (255, 245, 160), sub.topleft, sub.bottomright, 16
                )
                pygame.draw.line(
                    surf, (255, 245, 160), sub.topright, sub.bottomleft, 16
                )
            elif w == "O":
                pygame.draw.ellipse(surf, (160, 220, 255), sub, 16)
            elif w == "T":
                overlay = pygame.Surface(sub.size, pygame.SRCALPHA)
                overlay.fill((100, 100, 120, 90))
                surf.blit(overlay, sub.topleft)

    def _draw_hud(self, surf):
        title = self.font.render(TITLE, True, (235, 235, 245))
        surf.blit(title, (12, 10))

        x_wins = sum(1 for w in self.local_winner if w == "X")
        o_wins = sum(1 for w in self.local_winner if w == "O")
        t_wins = sum(1 for w in self.local_winner if w == "T")

        if self.net_enabled:
            if self.turn == self.local_symbol:
                turn_text = f"Your turn ({self.local_symbol})"
            elif self.turn == self.remote_symbol:
                turn_text = f"Opponent turn ({self.remote_symbol})"
            else:
                turn_text = "Match complete"
        else:
            turn_text = "Your turn (X)" if self.turn == "X" else "Opponent (O) thinking…"
        surf.blit(self.small.render(turn_text, True, (220, 220, 230)), (12, 34))
        surf.blit(
            self.small.render(
                "Rule: Play anywhere. Ties don't count. Majority wins if no line.",
                True,
                (210, 210, 220),
            ),
            (12, 54),
        )
        surf.blit(
            self.small.render(
                f"Minis: X={x_wins}  O={o_wins}  T={t_wins}", True, (210, 210, 220)
            ),
            (12, 74),
        )


def launch(manager, context, callback, **kwargs):
    return UltimateTTTScene(manager, context, callback, **kwargs)
