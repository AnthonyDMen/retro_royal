# minigames/dots_boxes_ascii/game.py
import pygame, random, math
from typing import List, Dict, Any
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Dots & Boxes (Retro)"
DEFAULT_BOXES = 5  # 5x5 boxes ⇒ 6x6 dots
TURN_LIMIT = 30.0  # per-turn timer for human players (seconds)

# Retro palette
BG = (12, 14, 18)
GRID_FAINT = (58, 64, 75)
EDGE_EMPTY = (85, 95, 110)
EDGE_HOVER = (180, 220, 255)
EDGE_FILLED = (235, 240, 250)
DOT = (240, 245, 255)
BOX_A = (120, 210, 160)
BOX_B = (210, 140, 120)
HUD = (230, 232, 240)
BORDER = (120, 130, 150)

# Pixel sizing — bigger than last build (easier to see/click)
PX = 4
CELL = PX * 18  # was 7*PX; larger cells
THIN = PX
THIC = PX * 4  # thicker edge for easier clicks
DOTSCALE = PX * 4  # bigger dots


class DotsBoxesRetro(Scene):
    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.big, self.font, self.small = load_game_fonts()
        self.minigame_id = "dots_boxes_ascii"

        # board geometry
        self.boxes = max(1, int(kwargs.get("boxes", DEFAULT_BOXES)))
        self.dots = self.boxes + 1

        # game mode (cpu or hotseat)
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
        self.mode = "mp" if self.net_enabled else kwargs.get("mode", "cpu")
        self.difficulty = kwargs.get("difficulty", 2)

        # end-state helpers
        self.pending_outcome = None
        self._completed = False
        banner_titles = {
            "win": "Dots & Boxes Cleared!",
            "lose": "Dots & Boxes Failed",
            "draw": "Dots & Boxes Draw",
        }
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", 2.5)),
            titles=banner_titles,
        )

        # state
        self._init_board()
        self._turn = 1

        # cursor over edges
        self._cursor = ("H", 0, 0)

        # timers / pulse
        self.turn_time = None
        self._pulse = 0.0
        self._start_turn_timer()

        # layout
        self._layout_cache_dirty = True
        self._rebuild_layout()
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    # ------------------------
    # Model
    # ------------------------
    def _init_board(self):
        n = self.dots
        self.H = [[False] * (n - 1) for _ in range(n)]  # horizontal edges
        self.V = [[False] * n for _ in range(n - 1)]  # vertical edges
        self.box = [[0] * (n - 1) for _ in range(n - 1)]  # 0 none, 1 P1, 2 P2
        self.score = [0, 0, 0]

    def _is_done(self):
        return all(all(r) for r in self.H) and all(all(r) for r in self.V)

    def _is_box_complete(self, br, bc):
        top = self.H[br][bc]
        bottom = self.H[br + 1][bc]
        left = self.V[br][bc]
        right = self.V[br][bc + 1]
        return top and bottom and left and right

    def _boxes_touched_by(self, kind, r, c):
        res = []
        if kind == "H":
            if r > 0:
                res.append((r - 1, c))
            if r < self.dots - 1:
                res.append((r, c))
        else:
            if c > 0:
                res.append((r, c - 1))
            if c < self.dots - 1:
                res.append((r, c))
        return [
            (br, bc)
            for (br, bc) in res
            if 0 <= br < self.dots - 1 and 0 <= bc < self.dots - 1
        ]

    # ------------------------
    # Layout (pixel-perfect rects)
    # ------------------------
    def _rebuild_layout(self):
        grid_w = (self.dots - 1) * CELL
        grid_h = (self.dots - 1) * CELL

        # roomy margins
        top_hud = PX * 30
        self.origin_x = (self.w - grid_w) // 2
        self.origin_y = max((self.h - grid_h) // 2, top_hud)

        # side “ASCII” border columns (no top/bottom)
        pad = PX * 10
        self.border_rect = pygame.Rect(
            self.origin_x - pad,
            self.origin_y - PX * 6,  # a little space above grid for HUD text
            grid_w + pad * 2,
            grid_h + PX * 12,
        )

        # precompute rects for edges and boxes
        n = self.dots
        self.edge_rects_H = [[None] * (n - 1) for _ in range(n)]
        self.edge_rects_V = [[None] * n for _ in range(n - 1)]
        self.box_rects = [[None] * (n - 1) for _ in range(n - 1)]

        for r in range(n):
            y = self.origin_y + r * CELL
            for c in range(n - 1):
                x = self.origin_x + c * CELL
                self.edge_rects_H[r][c] = pygame.Rect(
                    x + DOTSCALE, y - THIC // 2, CELL - DOTSCALE * 2, THIC
                )

        for r in range(n - 1):
            y = self.origin_y + r * CELL
            for c in range(n):
                x = self.origin_x + c * CELL
                self.edge_rects_V[r][c] = pygame.Rect(
                    x - THIC // 2, y + DOTSCALE, THIC, CELL - DOTSCALE * 2
                )

        for br in range(n - 1):
            y = self.origin_y + br * CELL + THIC
            for bc in range(n - 1):
                x = self.origin_x + bc * CELL + THIC
                self.box_rects[br][bc] = pygame.Rect(
                    x, y, CELL - THIC * 2, CELL - THIC * 2
                )

        self._layout_cache_dirty = False

    # ------------------------
    # Input
    # ------------------------
    def handle_event(self, e):
        if self.pending_outcome:
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.net_enabled and not self._current_turn_is_human():
            return

        if e.type == pygame.KEYDOWN:
            mode, r, c = self._cursor
            if e.key in (pygame.K_TAB,):
                self._cursor = ("V", r, c) if mode == "H" else ("H", r, c)
            elif e.key in (pygame.K_LEFT, pygame.K_a):
                self._cursor = (mode, r, max(0, c - 1))
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                maxc = (self.dots - 2) if mode == "H" else (self.dots - 1)
                self._cursor = (mode, r, min(maxc, c + 1))
            elif e.key in (pygame.K_UP, pygame.K_w):
                self._cursor = (mode, max(0, r - 1), c)
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                maxr = (self.dots - 1) if mode == "H" else (self.dots - 2)
                self._cursor = (mode, min(maxr, r + 1), c)
            elif e.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._try_place_edge()
        elif e.type == pygame.MOUSEMOTION:
            self._cursor = self._edge_under_point(e.pos) or self._cursor
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button in (1,):
            cur = self._edge_under_point(e.pos)
            if cur:
                self._cursor = cur
                self._try_place_edge()

    def _edge_under_point(self, pos):
        x, y = pos
        inflate = PX * 8  # extra forgiveness
        # try horizontal first
        for r, row in enumerate(self.edge_rects_H):
            for c, rect in enumerate(row):
                if rect.inflate(0, inflate).collidepoint(x, y) and not self.H[r][c]:
                    return ("H", r, c)
        # then vertical
        for r, row in enumerate(self.edge_rects_V):
            for c, rect in enumerate(row):
                if rect.inflate(inflate, 0).collidepoint(x, y) and not self.V[r][c]:
                    return ("V", r, c)
        return None

    # ------------------------
    # Turn timer helpers
    # ------------------------
    def _current_turn_is_human(self):
        if self.net_enabled:
            return self._turn == (1 if self.local_idx == 0 else 2)
        return self.mode == "hotseat" or (self.mode == "cpu" and self._turn == 1)

    def _start_turn_timer(self):
        if self.net_enabled:
            self.turn_time = None
        else:
            self.turn_time = TURN_LIMIT if self._current_turn_is_human() else None

    # ------------------------
    # Rules
    # ------------------------
    def _try_place_edge(self):
        mode, r, c = self._cursor
        made = False
        if mode == "H" and not self.H[r][c]:
            self.H[r][c] = True
            made = self._claim_if_box_from_edge("H", r, c)
        elif mode == "V" and not self.V[r][c]:
            self.V[r][c] = True
            made = self._claim_if_box_from_edge("V", r, c)

        if self._is_done():
            self._finish()
            return

        if not made:
            self._toggle_turn()
        self._start_turn_timer()
        if self.net_enabled:
            # Always force send so the other client receives the updated grid/turn even if control passes to them.
            self._net_send_state(kind="move", edge=(mode, r, c), force=True)

    def _toggle_turn(self):
        self._turn = 1 if self._turn == 2 else 2

    def _claim_if_box_from_edge(self, kind, r, c):
        claimed = 0
        for br, bc in self._boxes_touched_by(kind, r, c):
            if self.box[br][bc] == 0 and self._is_box_complete(br, bc):
                self.box[br][bc] = self._turn
                self.score[self._turn] += 1
                claimed += 1
        return claimed > 0

    # ------------------------
    # CPU + timeout auto-move
    # ------------------------
    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self._layout_cache_dirty:
            self._rebuild_layout()

        # pulse for hover
        self._pulse = (self._pulse + dt * 2.0) % (2 * math.pi)

        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        # decrement human turn timer; if hits 0, auto-move
        if self.turn_time is not None:
            self.turn_time = max(0.0, self.turn_time - dt)
            if self.turn_time <= 0.0:
                self._timeout_move()
                return

        # CPU move
        if not self.net_enabled and self.mode == "cpu" and self._turn == 2:
            self._cpu_move()
        # Net polling handled earlier; nothing else to do here for MP.

    def _pack_state(self, kind="state", extra=None):
        state = {
            "kind": kind,
            "turn": self._turn,
            "H": self.H,
            "V": self.V,
            "box": self.box,
            "score": self.score,
            "cursor": self._cursor,
        }
        if extra:
            state.update(extra)
        return state

    def _apply_state(self, state: Dict[str, Any]):
        if not state:
            return
        if self._completed:
            return
        self.H = [list(row) for row in state.get("H", self.H)]
        self.V = [list(row) for row in state.get("V", self.V)]
        self.box = [list(row) for row in state.get("box", self.box)]
        try:
            self.score = [int(x) for x in state.get("score", self.score)]
        except Exception:
            pass
        self._turn = int(state.get("turn", self._turn))
        self._cursor = tuple(state.get("cursor", self._cursor))
        winner_side = state.get("winner_side")
        outcome = state.get("outcome")
        if winner_side is not None and outcome:
            if self.pending_outcome:
                return
            mapped = outcome
            if winner_side == "tie":
                mapped = "draw"
            elif winner_side == (1 if self.local_idx == 0 else 2):
                mapped = "win"
            elif winner_side == (2 if self.local_idx == 0 else 1):
                mapped = "lose"
            subtitle = state.get("subtitle") or state.get("score_line") or f"{self.score[1]} - {self.score[2]}"
            self._queue_outcome(mapped, subtitle=subtitle)

    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[DotsBoxes] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        payload = self._pack_state(kind=kind, extra=extra)
        if force or self._current_turn_is_human():
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

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            self._queue_outcome("forfeit", "Forfeit")
            if self.net_enabled:
                self._net_send_state(kind="finish", force=True, winner_side=self.remote_idx + 1, outcome="forfeit")

    def _collect_moves(self):
        moves = []
        for r in range(self.dots):
            for c in range(self.dots - 1):
                if not self.H[r][c]:
                    moves.append(("H", r, c))
        for r in range(self.dots - 1):
            for c in range(self.dots):
                if not self.V[r][c]:
                    moves.append(("V", r, c))
        return moves

    def _pick_smart_move(self, moves):
        # 1) finish boxes if possible
        finishers = []
        for k, r, c in moves:
            for br, bc in self._boxes_touched_by(k, r, c):
                if self.box[br][bc] == 0 and self._would_complete(k, r, c, br, bc):
                    finishers.append((k, r, c))
                    break
        if finishers:
            return finishers[0]
        # 2) avoid creating 3-siders (difficulty >=2)
        if self.difficulty >= 2:
            safe = [
                (k, r, c)
                for (k, r, c) in moves
                if not self._creates_three_sider(k, r, c)
            ]
            if safe:
                return random.choice(safe)
        # 3) random
        return random.choice(moves) if moves else None

    def _timeout_move(self):
        moves = self._collect_moves()
        choice = self._pick_smart_move(moves)
        if choice:
            self._cursor = choice
            self._try_place_edge()

    def _cpu_move(self):
        moves = self._collect_moves()
        choice = self._pick_smart_move(moves)
        if choice:
            self._cursor = choice
            self._try_place_edge()

    def _would_complete(self, k, r, c, br, bc):
        top = self.H[br][bc] or (k == "H" and r == br and c == bc)
        bottom = self.H[br + 1][bc] or (k == "H" and r == br + 1 and c == bc)
        left = self.V[br][bc] or (k == "V" and r == br and c == bc)
        right = self.V[br][bc + 1] or (k == "V" and r == br and c == bc + 1)
        return top and bottom and left and right

    def _creates_three_sider(self, k, r, c):
        for br, bc in self._boxes_touched_by(k, r, c):
            sides = sum(
                [self.H[br][bc], self.H[br + 1][bc], self.V[br][bc], self.V[br][bc + 1]]
            )
            if sides == 2:
                return True
        return False

    def _queue_outcome(self, outcome, subtitle=""):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.banner.show(outcome, subtitle=subtitle)
        if self.net_enabled:
            local_side = 1 if self.local_idx == 0 else 2
            remote_side = 2 if local_side == 1 else 1
            winner_side = "tie"
            if outcome == "win":
                winner_side = local_side
            elif outcome in ("lose", "forfeit"):
                winner_side = remote_side
            score_line = f"{self.score[1]} - {self.score[2]}"
            self._net_send_state(
                kind="finish",
                force=True,
                winner_side=winner_side,
                outcome=outcome,
                score_line=score_line,
                subtitle=subtitle or score_line,
            )

    def _finish(self):
        p1, p2 = self.score[1], self.score[2]
        if p1 > p2:
            outcome = "win"
        elif p2 > p1:
            outcome = "lose"
        else:
            outcome = "draw"
        score_line = f"{p1} - {p2}"
        self._queue_outcome(outcome, subtitle=score_line)
        if self.net_enabled:
            winner_side = "tie"
            if outcome == "win":
                winner_side = 1 if self.local_idx == 0 else 2
            elif outcome == "lose":
                winner_side = 2 if self.local_idx == 0 else 1
            self._net_send_state(
                kind="finish",
                force=True,
                winner_side=winner_side,
                outcome=outcome,
                score_line=score_line,
                subtitle=score_line,
            )

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[DotsBoxes] Pause menu unavailable: {exc}")
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
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "score": {"player": self.score[1], "opponent": self.score[2]},
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.net_enabled and self.participants and len(self.participants) >= 2:
            winner = None
            loser = None
            if outcome == "win":
                winner = self.local_id
                loser = self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner = self.remote_id
                loser = self.local_id
            if winner:
                result["winner"] = winner
            if loser:
                result["loser"] = loser
        self.context.last_result = result
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[DotsBoxes] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[DotsBoxes] Callback error: {exc}")

    # ------------------------
    # Drawing
    # ------------------------
    def draw(self):
        screen = self.screen
        screen.fill(BG)

        # side ASCII columns only (no top/bottom)
        self._draw_ascii_side_columns(screen)

        # faint grid
        self._draw_grid_faint(screen)

        # boxes (fill + tiny dither)
        for br in range(self.dots - 1):
            for bc in range(self.dots - 1):
                owner = self.box[br][bc]
                if owner:
                    rect = self.box_rects[br][bc]
                    pygame.draw.rect(screen, BOX_A if owner == 1 else BOX_B, rect)
                    # little dither dots
                    step = PX * 3
                    for yy in range(rect.top + step // 2, rect.bottom, step):
                        for xx in range(rect.left + step // 2, rect.right, step):
                            screen.fill(EDGE_EMPTY, (xx, yy, 1, 1))

        # edges (filled vs empty)
        for r in range(self.dots):
            for c in range(self.dots - 1):
                rect = self.edge_rects_H[r][c]
                if self.H[r][c]:
                    pygame.draw.rect(screen, EDGE_FILLED, rect)
                else:
                    pygame.draw.rect(
                        screen, EDGE_EMPTY, rect.inflate(0, -(THIC - THIN))
                    )
        for r in range(self.dots - 1):
            for c in range(self.dots):
                rect = self.edge_rects_V[r][c]
                if self.V[r][c]:
                    pygame.draw.rect(screen, EDGE_FILLED, rect)
                else:
                    pygame.draw.rect(
                        screen, EDGE_EMPTY, rect.inflate(-(THIC - THIN), 0)
                    )

        # hover / cursor highlight on empty edge (with subtle pulse)
        k, cr, cc = self._cursor
        if (k == "H" and not self.H[cr][cc]) or (k == "V" and not self.V[cr][cc]):
            rect = self.edge_rects_H[cr][cc] if k == "H" else self.edge_rects_V[cr][cc]
            pulse = 0.35 + 0.65 * (0.5 * (math.sin(self._pulse) + 1.0))
            color = (
                min(255, int(EDGE_HOVER[0] * pulse)),
                min(255, int(EDGE_HOVER[1] * pulse)),
                min(255, int(EDGE_HOVER[2] * pulse)),
            )
            pygame.draw.rect(screen, color, rect)

        # dots
        for r in range(self.dots):
            y = self.origin_y + r * CELL
            for c in range(self.dots):
                x = self.origin_x + c * CELL
                pygame.draw.rect(
                    screen,
                    DOT,
                    (x - DOTSCALE // 2, y - DOTSCALE // 2, DOTSCALE, DOTSCALE),
                )

        # HUD (bigger font)
        tlabel = "--" if self.turn_time is None else f"{int(self.turn_time+0.99):02d}s"
        who = "P1" if self._turn == 1 else ("CPU" if self.mode == "cpu" else "P2")
        hud = (
            f"Turn: {who}  •  P1 {self.score[1]}  P2 {self.score[2]}  •  Time {tlabel}"
        )
        hud_surf = self.big.render(hud, True, HUD)
        hud_pos = (self.border_rect.left, self.border_rect.top - PX * 10)
        screen.blit(hud_surf, hud_pos)

        if self.pending_outcome:
            self.banner.draw(screen, self.big, self.small, (self.w, self.h))

    def _draw_grid_faint(self, screen):
        left = self.origin_x
        top = self.origin_y
        right = left + (self.dots - 1) * CELL
        bottom = top + (self.dots - 1) * CELL
        for r in range(self.dots):
            y = top + r * CELL
            pygame.draw.line(screen, GRID_FAINT, (left, y), (right, y), 1)
        for c in range(self.dots):
            x = left + c * CELL
            pygame.draw.line(screen, GRID_FAINT, (x, top), (x, bottom), 1)

    def _draw_ascii_side_columns(self, screen):
        """Two multi-line 'ASCII' columns left/right of the grid; no top/bottom."""
        fnt = self.font  # medium font for chunkier look
        cw, ch = fnt.size("|")

        # vertical span next to the grid
        top = self.border_rect.top + ch  # start a bit below hud
        bottom = self.border_rect.bottom - ch
        nrows = max(0, (bottom - top) // ch)

        # left columns x positions
        lx1 = self.border_rect.left
        lx2 = lx1 + cw  # second column
        # right columns
        rx2 = self.border_rect.right - cw
        rx1 = rx2 - cw

        # animated glyph pattern
        t = pygame.time.get_ticks() // 160
        glyphs = ["|", ":", "•", ":"]
        g1 = glyphs[(t) % len(glyphs)]
        g2 = glyphs[(t + 1) % len(glyphs)]

        # draw columns
        for i in range(nrows):
            y = top + i * ch
            screen.blit(fnt.render(g1, True, BORDER), (lx1, y))
            screen.blit(fnt.render(g2, True, BORDER), (lx2, y))
            screen.blit(fnt.render(g2, True, BORDER), (rx1, y))
            screen.blit(fnt.render(g1, True, BORDER), (rx2, y))


def launch(manager, context, callback, **kwargs):
    """
    kwargs:
      - boxes: int (default 5)   -> number of boxes per side (dots = boxes+1)
      - mode: 'cpu' or 'hotseat' -> single-player vs CPU, or 2P on one keyboard
      - difficulty: 1..3         -> CPU aggressiveness/avoidance
    """
    return DotsBoxesRetro(manager, context, callback, **kwargs)
