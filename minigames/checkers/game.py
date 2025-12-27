# minigames/checkers/game.py
# Neon-styled Checkers / Draughts minigame (P1 vs simple AI or 2P local)

import math
import random
import pygame
from typing import Dict, Any, List
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Checkers"
MINIGAME_ID = "checkers"
BOARD_SIZE = 8
CELL_MARGIN = 28
AI_DELAY = 0.7

PALETTE = {
    "bg": (8, 10, 18),
    "grid": (40, 200, 255),
    "grid_dim": (24, 120, 150),
    "p1": (80, 255, 220),
    "p2": (255, 120, 255),
    "king_glow": (255, 215, 120),
    "select": (255, 255, 120),
    "hint": (140, 200, 255),
    "text": (225, 235, 245),
}


def inside(r, c):
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


class CheckersScene(Scene):
    def __init__(self, manager, context=None, callback=None, players=1, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
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
        self._local_side = "p1" if self.local_idx == 0 else "p2"
        self._remote_side = "p2" if self._local_side == "p1" else "p1"
        self.players = 2 if self.net_enabled else (1 if players != 2 else 2)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.big, self.font, self.small = load_game_fonts()
        self.banner = EndBanner(
            duration=2.5,
            titles={
                "win": "Checkers Win!",
                "lose": "Checkers Lost",
                "forfeit": "Checkers Forfeit",
            },
        )
        self.minigame_id = MINIGAME_ID
        self.pending_outcome = None
        self.pending_payload = {}
        self._completed = False

        self._grid_rect = pygame.Rect(
            CELL_MARGIN,
            CELL_MARGIN,
            self.w - CELL_MARGIN * 2,
            self.h - CELL_MARGIN * 2,
        )
        self._cell = min(self._grid_rect.w, self._grid_rect.h) // BOARD_SIZE
        self._grid_rect.w = self._grid_rect.h = self._cell * BOARD_SIZE
        self._grid_rect.center = (self.w // 2, self.h // 2)

        self._reset_board()
        self.selected = None
        self.valid_moves = []
        self.turn = "p1"
        self.ai_timer = 0.0
        self.net_sync_timer = 0.0
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    # ---------- board helpers ----------

    def _reset_board(self):
        self.board = {}
        # top rows = p2, bottom rows = p1
        for r in range(3):
            for c in range(BOARD_SIZE):
                if (r + c) % 2 == 1:
                    self.board[(r, c)] = {"side": "p2", "king": False}
        for r in range(BOARD_SIZE - 3, BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if (r + c) % 2 == 1:
                    self.board[(r, c)] = {"side": "p1", "king": False}

    def _dirs_for(self, piece):
        if piece["king"]:
            return [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        return [(-1, -1), (-1, 1)] if piece["side"] == "p1" else [(1, -1), (1, 1)]

    def _promote_if_needed(self, pos, piece):
        r, _ = pos
        if piece["king"]:
            return piece["king"]
        if piece["side"] == "p1" and r == 0:
            return True
        if piece["side"] == "p2" and r == BOARD_SIZE - 1:
            return True
        return False

    # ---------- net helpers ----------
    def _local_turn(self):
        return (not self.net_enabled) or (self.turn == self._local_side)

    def _pack_state(self, kind="state", extra=None):
        state = {
            "kind": kind,
            "turn": self.turn,
            "board": {f"{r},{c}": {"side": v["side"], "king": v["king"]} for (r, c), v in self.board.items()},
            "pending": self.pending_outcome,
            "payload": self.pending_payload,
        }
        if extra:
            state.update(extra)
        return state

    def _apply_state(self, state: Dict[str, Any]):
        if not state:
            return
        board = {}
        for key, val in (state.get("board") or {}).items():
            try:
                r_str, c_str = key.split(",", 1)
                r, c = int(r_str), int(c_str)
                board[(r, c)] = {"side": val.get("side"), "king": bool(val.get("king", False))}
            except Exception:
                continue
        if board:
            self.board = board
        self.turn = state.get("turn", self.turn)
        # Map finish outcome relative to local side when provided.
        winner_side = state.get("winner")
        raw_outcome = state.get("outcome")
        if winner_side:
            mapped = raw_outcome or "win"
            if raw_outcome == "forfeit":
                mapped = "lose" if winner_side != self._local_side else "win"
            elif winner_side == self._local_side:
                mapped = "win"
            elif winner_side:
                mapped = "lose"
            self.pending_outcome = mapped
        else:
            # Fallback to raw pending if no winner info was sent.
            self.pending_outcome = state.get("pending", self.pending_outcome)
        self.pending_payload = state.get("payload", self.pending_payload)
        if self.pending_outcome and not self.banner.active:
            reason = (self.pending_payload or {}).get("reason", "")
            self.banner.show(self.pending_outcome, subtitle=reason)

    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[Checkers] Failed to send action: {exc}")

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

    # capture search with multi-jumps
    def _captures_from(self, pos, piece, board):
        r, c = pos
        results = []
        for dr, dc in self._dirs_for(piece):
            mid = (r + dr, c + dc)
            land = (r + 2 * dr, c + 2 * dc)
            if not inside(*land):
                continue
            if mid in board and board[mid]["side"] != piece["side"] and land not in board:
                # clone board minimally
                next_board = dict(board)
                next_board.pop(pos, None)
                next_board.pop(mid, None)
                new_piece = dict(piece)
                new_piece["king"] = self._promote_if_needed(land, new_piece)
                next_board[land] = new_piece
                tails = self._captures_from(land, new_piece, next_board)
                if tails:
                    for path, caps in tails:
                        results.append(([land] + path, [mid] + caps))
                else:
                    results.append(([land], [mid]))
        return results

    def _legal_moves_for_piece(self, pos, piece, board):
        captures = self._captures_from(pos, piece, board)
        if captures:
            return [
                {"start": pos, "path": path, "captured": caps, "capture": True}
                for path, caps in captures
            ]
        # steps only if no captures globally; handled by caller
        moves = []
        r, c = pos
        for dr, dc in self._dirs_for(piece):
            nr, nc = r + dr, c + dc
            if inside(nr, nc) and (nr, nc) not in board:
                moves.append({"start": pos, "path": [(nr, nc)], "captured": [], "capture": False})
        return moves

    def _all_legal_moves(self, side):
        board = self.board
        captures = []
        steps = []
        for pos, piece in board.items():
            if piece["side"] != side:
                continue
            mvs = self._legal_moves_for_piece(pos, piece, board)
            for mv in mvs:
                if mv["capture"]:
                    captures.append(mv)
                else:
                    steps.append(mv)
        return captures if captures else steps

    # ---------- turn helpers ----------
    def _apply_move(self, move):
        start = move["start"]
        dest = move["path"][-1]
        piece = dict(self.board.pop(start))
        for mid in move["captured"]:
            self.board.pop(mid, None)
        piece["king"] = self._promote_if_needed(dest, piece)
        self.board[dest] = piece

    def _end_if_done(self):
        p1_moves = self._all_legal_moves("p1")
        p2_moves = self._all_legal_moves("p2")
        if not any(p["side"] == "p2" for p in self.board.values()) or not p2_moves:
            self._queue_outcome("win", "All opponent pieces blocked")
            if self.net_enabled:
                self._net_send_state(kind="finish", force=True, outcome="win", winner="p1")
            return True
        if not any(p["side"] == "p1" for p in self.board.values()) or not p1_moves:
            self._queue_outcome("lose", "No moves remain")
            if self.net_enabled:
                self._net_send_state(kind="finish", force=True, outcome="lose", winner="p2")
            return True
        return False

    # ---------- input ----------
    def _grid_at(self, pos):
        x, y = pos
        if not self._grid_rect.collidepoint(x, y):
            return None
        gx = (x - self._grid_rect.left) // self._cell
        gy = (y - self._grid_rect.top) // self._cell
        return int(gy), int(gx)  # row, col

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
        if self.players == 1 and self.turn == "p2":
            return  # wait for AI

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            rc = self._grid_at(event.pos)
            if not rc:
                return
            if self.selected:
                # Try to complete a move
                for mv in self.valid_moves:
                    if mv["start"] == self.selected and (rc == mv["path"][-1]):
                        self._apply_move(mv)
                        self.selected = None
                        self.valid_moves = []
                        if self._end_if_done():
                            return
                        self.turn = "p2" if self.turn == "p1" else "p1"
                        self.ai_timer = 0.0
                        if self.net_enabled:
                            self._net_send_state(kind="move", force=True)
                        return
            # Select piece
            if rc in self.board and self.board[rc]["side"] == self.turn:
                self.selected = rc
                # respect forced captures: compute global, then filter for this piece
                all_moves = self._all_legal_moves(self.turn)
                if any(m["capture"] for m in all_moves):
                    self.valid_moves = [m for m in all_moves if m["start"] == rc and m["capture"]]
                else:
                    self.valid_moves = [m for m in all_moves if m["start"] == rc]
            else:
                self.selected = None
                self.valid_moves = []

    # ---------- ai ----------
    def _ai_take_turn(self, dt):
        if self.net_enabled or self.players != 1 or self.turn != "p2" or self.pending_outcome:
            return
        self.ai_timer += dt
        if self.ai_timer < AI_DELAY:
            return
        moves = self._all_legal_moves("p2")
        if not moves:
            self._queue_outcome("win", "Opponent has no moves")
            return
        # prefer captures, then random
        cap_moves = [m for m in moves if m["capture"]]
        if cap_moves:
            moves = cap_moves
        move = random.choice(moves)
        self._apply_move(move)
        if self._end_if_done():
            return
        self.turn = "p1"
        self.ai_timer = 0.0

    # ---------- update / draw ----------
    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return
        self._ai_take_turn(dt)
        if self.net_enabled:
            self.net_sync_timer += dt
            if self.net_sync_timer >= 0.12:
                self.net_sync_timer = 0.0
                self._net_send_state(kind="state")

    def _square_rect(self, r, c):
        return pygame.Rect(
            self._grid_rect.left + c * self._cell,
            self._grid_rect.top + r * self._cell,
            self._cell,
            self._cell,
        )

    def _draw_piece(self, r, c, piece):
        cx = self._grid_rect.left + c * self._cell + self._cell // 2
        cy = self._grid_rect.top + r * self._cell + self._cell // 2
        color = PALETTE["p1"] if piece["side"] == "p1" else PALETTE["p2"]
        pygame.draw.circle(self.screen, color, (cx, cy), int(self._cell * 0.36), 2)
        pygame.draw.circle(self.screen, color, (cx, cy), int(self._cell * 0.28))
        if piece["king"]:
            pygame.draw.circle(
                self.screen, PALETTE["king_glow"], (cx, cy), int(self._cell * 0.18), 2
            )

    def draw(self):
        self.screen.fill(PALETTE["bg"])
        # grid squares
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                rect = self._square_rect(r, c)
                shade = PALETTE["grid"] if (r + c) % 2 else PALETTE["grid_dim"]
                pygame.draw.rect(self.screen, shade, rect, 1)
        # selection + move hints
        if self.selected:
            pygame.draw.rect(
                self.screen, PALETTE["select"], self._square_rect(*self.selected), 2
            )
            for mv in self.valid_moves:
                dest = mv["path"][-1]
                pygame.draw.circle(
                    self.screen,
                    PALETTE["hint"],
                    self._square_rect(*dest).center,
                    int(self._cell * 0.16),
                    2,
                )
        # pieces
        for (r, c), piece in self.board.items():
            self._draw_piece(r, c, piece)

        # HUD
        title = self.big.render(TITLE, True, PALETTE["text"])
        self.screen.blit(title, (16, 12))
        if self.net_enabled:
            turn_txt = "Your turn" if self._local_turn() else "Opponent turn"
        else:
            turn_txt = "Your turn" if self.turn == "p1" else ("CPU thinking" if self.players == 1 else "P2 turn")
        info = self.font.render(turn_txt, True, PALETTE["text"])
        self.screen.blit(info, (16, 44))
        hint = self.small.render(
            "Click piece then destination • Captures are forced • Esc pauses", True, PALETTE["text"]
        )
        self.screen.blit(hint, (16, 68))

        if self.pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.w, self.h))

    # ---------- finalize / pause ----------
    def _queue_outcome(self, outcome, reason):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.pending_payload = {"reason": reason}
        self.banner.show(outcome, subtitle=reason)
        if self.net_enabled:
            winner = "p1" if outcome == "win" else ("p2" if outcome == "lose" else None)
            self._net_send_state(kind="finish", force=True, outcome=outcome, winner=winner)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[VectorCheckers] Pause menu unavailable: {exc}")
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
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[VectorCheckers] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[VectorCheckers] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            self.pending_outcome = "forfeit"
            self.pending_payload = {"reason": "forfeit"}
            self.banner.show("forfeit", subtitle="Forfeit")
            if self.net_enabled:
                self._net_send_state(kind="finish", force=True, outcome="forfeit", winner="p2" if self._local_turn() else "p1")


def launch(manager, context=None, callback=None, **kwargs):
    return CheckersScene(manager, context, callback, **kwargs)
