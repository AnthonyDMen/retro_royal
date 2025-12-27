# minigames/kalah_duel/game.py
# Kalah Duel (Mancala) — animated sowing, clear capture, CPU/2P toggle, help & guides

import random
import pygame
import math
from pathlib import Path
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Kalah Duel (Mancala)"
MINIGAME_ID = "kalah_duel"

PITS_PER_SIDE = 6
# ---- label placement (tweak to taste) ----
PIT_NUM_OFFSET_Y       = -60  # raise pit counts a bit
STORE_NUM_OFFSET_Y_TOP = -250   # opponent store (left): show near top
STORE_NUM_OFFSET_Y_BOT = +250   # your store (right): show near bottom
DEFAULT_SEEDS = 4

# Background layout authored @ 1280x720 (big board).
_BASE_W, _BASE_H = 1280, 720
_X_COLS = [325, 451, 577, 703, 829, 955]  # x centers for 6 pits (left→right)
_Y_ROWS = {0: 219, 1: 501}  # y centers (0=opponent top, 1=player bottom)
_STORE_CENTERS = {0: (195, 360), 1: (1085, 360)}  # 0=opponent left, 1=player right


class KalahDuelScene(Scene):
    def _pit_label_pos(self, row, col):
        x, y = self._pit_center(row, col)
        return x, y + int(PIT_NUM_OFFSET_Y * self._sy)

    def _store_label_pos(self, side):
        x, y = self._store_center(side)
        dy = STORE_NUM_OFFSET_Y_TOP if side == 0 else STORE_NUM_OFFSET_Y_BOT
        return x, y + int(dy * self._sy)

    def _blit_label(self, surf, text, font, center, color=(20,20,20)):
        # soft shadow
        shadow = font.render(text, True, (0,0,0))
        surf.blit(shadow, shadow.get_rect(center=(center[0]+1, center[1]+1)))
        label  = font.render(text, True, color)
        surf.blit(label,  label.get_rect(center=center))
    def __init__(self, manager, context=None, callback=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.minigame_id = MINIGAME_ID
        self.banner = EndBanner(
            duration=float(max(4.0, kwargs.get("banner_duration", 4.0))),
            titles={
                "win": "Kalah Duel Win!",
                "lose": "Kalah Duel Lost",
                "tie": "Kalah Duel Tie",
                None: "Kalah Duel Complete",
            },
        )

        self.smallfont, self.medfont, self.bigfont = load_game_fonts()
        # Downscale fonts a bit to better fit the layout
        self.smallfont = pygame.font.SysFont(None, 18)
        self.medfont = pygame.font.SysFont(None, 24)
        self.bigfont = pygame.font.SysFont(None, 34)

        # multiplayer plumbing
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
        # Side mapping: local plays bottom row (1), remote is top row (0)
        self.local_side = 1 if self.local_idx == 0 else 0
        self.remote_side = 1 - self.local_side

        # board state
        self.board = [[DEFAULT_SEEDS] * PITS_PER_SIDE for _ in range(2)]
        self.stores = [0, 0]
        self.turn = 1  # global: bottom starts
        self.winner = None
        self.status_msg = "Your turn!" if self.turn == self.local_side else "Opponent turn"

        # mode / UX
        self.vs_cpu = not self.net_enabled  # [P] to toggle only in local
        self.show_help = True  # [H] to toggle
        self.show_opposites = False  # [O] to toggle (draw across-pit guides)
        self._hover_pit = None

        # AI timing (SECONDS)
        self._ai_wait_s = 0.35
        self._ai_elapsed = 0.0

        # Sowing animation (single moving stone) — fixed speed to keep MP in sync
        self.slow_mode = True
        self._drop_interval_s = 0.14
        self.animating = False
        self.anim_owner = None
        self.anim_steps = []  # list of ('pit', r, c) or ('store', p)
        self.anim_last_pos = None  # ('pit', r, c) or ('store', p) or (r, c)
        self.anim_timer = 0.0
        self.moving = None  # {'from':(x,y),'to':(x,y),'pos':(x,y),'t':0..1}
        self.post_pause = 0.0  # short pause after a move ends

        # assets beside this file
        here = Path(__file__).parent
        self._bg = self._load_background(here / "background.png")
        self._spritesheet = self._load_spritesheet(here / "spritesheet.png")
        # two-row stones sheet: row 0 = idle, row 1 = glow
        self._cell = 64
        self._stone_idle, self._stone_glow = [], []
        if self._spritesheet:
            for col in range(8):
                r_idle = pygame.Rect(col*self._cell, 0, self._cell, self._cell)
                r_glow = pygame.Rect(col*self._cell, self._cell, self._cell, self._cell)
                idle = self._spritesheet.subsurface(r_idle).copy()
                glow = self._spritesheet.subsurface(r_glow).copy()
                idle = pygame.transform.smoothscale(idle, (32, 32))
                glow = pygame.transform.smoothscale(glow, (32, 32))
                self._stone_idle.append(idle)
                self._stone_glow.append(glow)
        else:
            fb = pygame.Surface((32, 32), pygame.SRCALPHA)
            pygame.draw.circle(fb, (220,180,40), (16,16), 15)
            self._stone_idle = [fb]*8
            self._stone_glow = [fb]*8

        # coords scale
        screen = pygame.display.get_surface()
        self._sx = screen.get_width() / _BASE_W
        self._sy = screen.get_height() / _BASE_H

        self._store_pop = [0.0, 0.0]  # landing pop timers per store

        # --- per-stone color state (0..7 index into spritesheet columns) ---
        self._rng = random.Random(1337)  # deterministic but you can reseed
        def _rand_color_idx(): return self._rng.randrange(8)

        # each pit holds a LIST of color indices; stores too
        self.piles = [[[_rand_color_idx() for _ in range(self.board[r][c])]
                       for c in range(PITS_PER_SIDE)] for r in range(2)]
        self.store_piles = [[], []]

        # scatter seeds so each pit/store has stable random layout
        self._pit_seed = [[self._rng.randrange(1<<30) for _ in range(PITS_PER_SIDE)] for _ in range(2)]
        self._store_seed = [self._rng.randrange(1<<30), self._rng.randrange(1<<30)]

        self.move_player = None  # who started the current animated move
        self._pending_outcome = None
        self.pending_payload = {}
        self._completed = False
        self.forfeited = False
        self.help_timer = 3.0  # auto-hide help after a short delay
        # net helpers
        self._net_timer = 0.0
        self._net_interval = 0.15
        self._completed = False
        if self.net_enabled:
            self._net_send_state(kind="init", board=self.board, stores=self.stores, turn=self.turn)
        self._update_status()

    # ------------ engine hooks ------------

    def handle_event(self, event):
        if self._pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_h:
                self.show_help = not self.show_help
                self.help_timer = 3.0 if self.show_help else 0.0
                return
            if event.key == pygame.K_p and not self.net_enabled:
                self.vs_cpu = not self.vs_cpu
                return
            if event.key == pygame.K_o:
                self.show_opposites = not self.show_opposites
                return
            if event.key == pygame.K_ESCAPE:
                self._pause_game()
                return

        if self.winner is not None:
            return

        if self._is_busy():
            # allow only H/P/S/O toggles while busy
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_h, pygame.K_p, pygame.K_s, pygame.K_o):
                pass  # let toggles fall through
            else:
                return

        # keyboard picks for current player
        if event.type == pygame.KEYDOWN and pygame.K_1 <= event.key <= pygame.K_6:
            pit = event.key - pygame.K_1
            if (
                not self.net_enabled and 0 <= pit < PITS_PER_SIDE and self.board[self.turn][pit] > 0 and self.piles[self.turn][pit]
            ) or (
                self.net_enabled and self.turn == self.local_side and 0 <= pit < PITS_PER_SIDE and self.board[self.turn][pit] > 0 and self.piles[self.turn][pit]
            ):
                self._make_move(self.turn, pit, send_net=True)
            return

        # mouse picks for current player
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            pit = self._hit_pit(mx, my, row=self.turn)
            if pit is not None and self.board[self.turn][pit] > 0 and self.piles[self.turn][pit]:
                if not self.net_enabled or self.turn == self.local_side:
                    self._make_move(self.turn, pit, send_net=True)

    def update(self, dt):
        if self._pending_outcome:
            if self.banner.update(dt):
                self._finalize(self._pending_outcome)
            return
        # net poll early
        self._net_poll_actions(float(dt))

        # Auto-hide help after a brief display
        if self.show_help and self.help_timer > 0:
            self.help_timer = max(0.0, self.help_timer - float(dt))
            if self.help_timer == 0.0:
                self.show_help = False

        if self.winner is not None and not self._pending_outcome:
            self._queue_finish(self.winner == 1)
            return

        # animate sowing
        if self.animating:
            # spawn the next moving stone if needed
            if self.moving is None and self.anim_steps:
                kind, a, b = self.anim_steps[0]  # peek
                # FROM = last landing (store or pit)
                alp = self.anim_last_pos
                if isinstance(alp, tuple) and alp and alp[0] == 'store':
                    fr = self._store_center(alp[1])
                else:
                    r0, c0 = alp  # (row:int, col:int)
                    fr = self._pit_center(r0, c0)
                # to = next target
                to = self._store_center(a) if kind == 'store' else self._pit_center(a, b)
                self.moving = {'from': fr, 'to': to, 'pos': fr, 't': 0.0}

            # advance the moving stone
            if self.moving is not None:
                self.anim_timer += float(dt)
                travel = self._drop_interval_s
                t = min(1.0, self.anim_timer / travel)
                x0, y0 = self.moving["from"]
                x1, y1 = self.moving["to"]
                self.moving["t"] = t
                self.moving["pos"] = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

                if t >= 1.0:
                    # land this stone: update counts and pop the step
                    self.anim_timer -= travel
                    kind, a, b = self.anim_steps.pop(0)
                    # the color that just landed
                    land_color = self._anim_colors.pop(0) if hasattr(self, "_anim_colors") and self._anim_colors else 0
                    if kind == "store":
                        self.stores[a] += 1
                        self.store_piles[a].append(land_color)
                        self._store_pop[a] = 0.25  # 250ms pop
                        self.anim_last_pos = ("store", a)
                    else:
                        self.board[a][b] += 1
                        self.piles[a][b].append(land_color)
                        self.anim_last_pos = (a, b)
                    self._sync_counts_from_piles()
                    self.moving = None

                    # finished?
                    if not self.anim_steps:
                        # finished anim
                        self.animating = False
                        final_pos = self.anim_last_pos
                        mover = self.move_player
                        self._finalize_after_sow(mover, final_pos)
                        self.move_player = None
                        self.post_pause = 0.40
                        return
            return

        # post-move pause
        if self.post_pause > 0:
            self.post_pause -= float(dt)
            return

        # hover pit for previews (current player)
        self._hover_pit = None
        mx, my = pygame.mouse.get_pos()
        hp = self._hit_pit(mx, my, row=self.turn)
        if hp is not None and self.board[self.turn][hp] > 0:
            self._hover_pit = hp

        # CPU turn (top = 0) in vs_cpu
        if self.vs_cpu and not self.net_enabled and self.turn == 0 and not self._is_busy():
            self._ai_elapsed += float(dt)
            if self._ai_elapsed >= self._ai_wait_s and not self._pending_outcome:
                self._ai_elapsed = 0.0
                moves = [i for i in range(PITS_PER_SIDE) if self.board[0][i] > 0 and self.piles[0][i]]
                if moves:
                    choice = self._choose_ai_move(moves)
                    self._make_move(0, choice)

        # decay store pop
        self._store_pop[0] = max(0.0, self._store_pop[0] - float(dt))
        self._store_pop[1] = max(0.0, self._store_pop[1] - float(dt))

    def draw(self):
        screen = pygame.display.get_surface()
        screen.blit(self._bg, (0, 0))
        self._draw_pit_stones(screen)
        self._draw_store_stones(screen)

        # draw across-pit guide lines if enabled
        if self.show_opposites:
            for c in range(PITS_PER_SIDE):
                x1, y1 = self._pit_center(1, c)
                x2, y2 = self._pit_center(0, c)
                pygame.draw.aaline(
                    screen, (255, 255, 255), (x1, y1 - 10), (x2, y2 + 10)
                )

        # pit counts
        for r in range(2):
            for c in range(PITS_PER_SIDE):
                seeds = self.board[r][c]
                x, y = self._pit_label_pos(r, c)
                self._blit_label(screen, str(seeds), self.smallfont, (x, y))

        # store counts
        for s in (0, 1):
            x, y = self._store_label_pos(s)
            self._blit_label(screen, str(self.stores[s]), self.medfont, (x, y))

        # Store pop ring effect
        for s in (0, 1):
            pop = self._store_pop[s]
            if pop > 0.0:
                t = 1.0 - (pop / 0.25)
                cx, cy = self._store_center(s)
                rx = 50 * self._sx * (1.0 + 0.15 * t)
                ry = 36 * self._sy * (1.0 + 0.15 * t)
                self._draw_ring(
                    screen, cx, cy, rx, ry,
                    (255, 255, 180), width=3
                )

        # legal move highlights (current player) when idle
        if not self.animating and self.post_pause <= 0:
            self._draw_legal_highlights(screen, row=self.turn)
            if self._hover_pit is not None:
                self._draw_hover_preview(screen, player=self.turn, pit=self._hover_pit)

        # draw moving stone during animation
        if self.animating and self.moving is not None:
            x, y = self.moving['pos']
            img = getattr(self, "_moving_sprite", None)
            if img is None and hasattr(self, "_stone_glow") and self._stone_glow:
                img = self._stone_glow[0]
            if img is not None:
                rect = img.get_rect(center=(int(x), int(y)))
                screen.blit(img, rect)

        # during animation, highlight current drop target ring
        if self.animating and self.moving is None and self.anim_steps:
            # next target (peek)
            kind, a, b = self.anim_steps[0]
            if kind == "store":
                cx, cy = self._store_center(a)
            else:
                cx, cy = self._pit_center(a, b)
            self._draw_ring(
                screen, cx, cy, 46 * self._sx, 34 * self._sy, (255, 255, 255), 4
            )

        # status + mode
        mode = "vs CPU (P)" if self.vs_cpu else "2P (P)"
        msg = self.medfont.render(
            f"{self.status_msg}   [{mode}]", True, (255, 255, 255)
        )
        screen.blit(msg, (20, 20))

        # help overlay
        if self.show_help and self.winner is None:
            self._draw_help_overlay(screen)

        # end banner overlay
        if self._pending_outcome:
            self.banner.draw(screen, self.bigfont, self.medfont, screen.get_size())

    # ------------ move / rules ------------

    def _make_move(self, player, pit, send_net=False):
        """Start animated sowing from (player, pit)."""
        if self.animating or self.post_pause > 0:
            return
        if self.net_enabled and self.turn != player:
            return
        seeds = self.board[player][pit]
        if seeds <= 0: return

        # take the actual colors to sow (one-by-one), and visually empty the pit right away
        self._anim_colors = self.piles[player][pit][:]
        self.piles[player][pit] = []
        self.board[player][pit] = 0
        self.anim_last_pos = (player, pit)
        self.move_player = player

        r, c = player, pit
        for _ in range(seeds):
            r, c = self._next_pos(r, c, player)
            if r == "store":
                self.anim_steps.append(("store", player, 0))
            else:
                self.anim_steps.append(("pit", r, c))

        self.anim_timer = 0.0
        self.moving = None
        self.animating = True
        self.status_msg = "Sowing..."
        self.move_player = player
        if send_net and self.net_enabled:
            self._net_send_state(kind="move", pit=pit, turn=player)

    def _finalize_after_sow(self, player, final_pos):
        """Apply extra turn / capture after animation has placed the last stone."""
        # Guard: if caller didn't pass a player, infer something sensible
        if player is None:
            # If the last landing was a store, the store owner is the mover
            if isinstance(final_pos, tuple) and len(final_pos) == 2 and final_pos[0] == 'store':
                player = final_pos[1]
            else:
                # fall back to whoever's turn it was (prevents crash)
                player = self.turn

        # Extra turn?
        if (
            isinstance(final_pos, tuple)
            and len(final_pos) == 2
            and final_pos[0] == "store"
        ):
            # (we never pass this shape here; kept for completeness)
            pass

        if final_pos == ("store", player):
            self.status_msg = "Extra turn!"
            self.turn = player
        elif isinstance(final_pos, tuple) and len(final_pos) == 2:
            r, c = final_pos
            if r == player and self.board[player][c] == 1:
                opp_row = 1 - player
                opp_pit = c  # SAME COLUMN
                captured = self.board[opp_row][opp_pit]
                if captured > 0:
                    # move colors: last landed from our pit + all from opposite pit -> our store
                    last_color = self.piles[player][c].pop() if self.piles[player][c] else None
                    captured_colors = self.piles[opp_row][opp_pit][:]
                    self.piles[opp_row][opp_pit].clear()
                    if last_color is not None:
                        self.store_piles[player].append(last_color)
                    self.store_piles[player].extend(captured_colors)

                    # update counts (you already do this, keep it consistent)
                    self.board[opp_row][opp_pit] = 0
                    self.board[player][c] = 0
                    self.stores[player] += captured + 1
                    self._sync_counts_from_piles()

                    self.status_msg = f"Capture! Took {captured}+1 from opposite."
                    self.turn = 1 - player
                else:
                    self.turn = 1 - player
            else:
                self.turn = 1 - player
        else:
            self.turn = 1 - player

        # reset AI timer whenever it's CPU's turn
        if self.vs_cpu and self.turn == 0:
            self._ai_elapsed = 0.0

        # Sync state to opponent after move resolves.
        self._check_end()
        self._update_status()

    def _next_pos(self, r, c, player):
        """Counter-clockwise traversal independent of who is moving.
        Row 1 (bottom) goes left→right; Row 0 (top) goes right→left.
        Skip opponent's store; allow your own store."""
        # Coming out of a store: jump to the far end of the opponent's row
        if r == "store":
            # after P1 store -> top rightmost; after P0 store -> bottom leftmost
            return (1 - player, PITS_PER_SIDE - 1 if player == 1 else 0)

        if r == 1:
            # bottom row: left -> right, then possibly into player 1's store
            if c < PITS_PER_SIDE - 1:
                return (1, c + 1)
            else:
                return ("store", 1) if player == 1 else (0, PITS_PER_SIDE - 1)
        else:  # r == 0
            # top row: right -> left, then possibly into player 0's store
            if c > 0:
                return (0, c - 1)
            else:
                return ("store", 0) if player == 0 else (1, 0)

    def _check_end(self):
        if sum(self.board[0]) == 0 or sum(self.board[1]) == 0:
            self.stores[0] += sum(self.board[0])
            self.stores[1] += sum(self.board[1])

            # move all remaining colored stones to stores
            for r in (0, 1):
                for c in range(PITS_PER_SIDE):
                    if self.piles[r][c]:
                        self.store_piles[r].extend(self.piles[r][c])
                        self.piles[r][c] = []

            self.board = [[0] * PITS_PER_SIDE for _ in range(2)]
            if self.stores[1] > self.stores[0]:
                self.winner = 1
            elif self.stores[0] > self.stores[1]:
                self.winner = 0
            else:
                self._pending_outcome = "tie"
                self.pending_payload = {
                    "vs_cpu": self.vs_cpu,
                    "slow_mode": self.slow_mode,
                    "stores": list(self.stores),
                    "message": self.status_msg,
                    "forfeit": self.forfeited,
                }
                subtitle = f"Stores — You {self.stores[1]} : {self.stores[0]} Opp"
                self.banner.show("tie", subtitle=subtitle)
                if self.net_enabled:
                    self._net_send_state(kind="finish", outcome="tie", stores=list(self.stores))
                return
            if self.winner is not None and self.net_enabled:
                win_id = self.local_id if self.winner == self.local_side else self.remote_id
                lose_id = self.remote_id if self.winner == self.local_side else self.local_id
                self.pending_payload["winner"] = win_id
                self.pending_payload["loser"] = lose_id
                self._net_send_state(kind="finish", outcome="win" if self.winner == self.local_side else "lose", winner=win_id, loser=lose_id, stores=list(self.stores))
            self._update_status()

    # ------------ tiny AI ------------

    def _choose_ai_move(self, moves):
        # prefer extra-turn, then capture, else random
        for m in moves:
            if self._would_land_in_store(0, m):
                return m
        for m in moves:
            if self._would_capture(0, m):
                return m
        return random.choice(moves)

    def _simulate_move(self, player, pit):
        seeds = self.board[player][pit]
        if seeds <= 0:
            return None
        r, c = player, pit
        remaining = seeds
        while remaining > 0:
            r, c = self._next_pos(r, c, player)
            remaining -= 1
        return ("store", player) if r == "store" else (r, c)

    def _would_land_in_store(self, player, pit):
        return self._simulate_move(player, pit) == ("store", player)

    def _would_capture(self, player, pit):
        pos = self._simulate_move(player, pit)
        if not pos or pos == ("store", player):
            return False
        r, c = pos
        if r != player:
            return False
        # must land in an empty pit on our side; opposite must have stones
        if self.board[player][c] != 0:
            return False
        # Kalah capture: the opposite pit is the same column on the other side
        opp = self.board[1 - player][c]
        return opp > 0

    # ------------ UI helpers ------------

    def _set_speed(self, slow: bool):
        self.slow_mode = slow
        self._drop_interval_s = 0.14 if slow else 0.06

    def _pit_center(self, row, col):
        x = int(_X_COLS[col] * self._sx)
        y = int(_Y_ROWS[row] * self._sy)
        return x, y

    def _store_center(self, side):
        cx, cy = _STORE_CENTERS[side]
        return int(cx * self._sx), int(cy * self._sy)

    def _hit_pit(self, mx, my, row):
        # ellipse hitbox approximating the wells
        rx = 40 * self._sx
        ry = 30 * self._sy
        for c in range(PITS_PER_SIDE):
            cx, cy = self._pit_center(row, c)
            dx = (mx - cx) / (rx if rx else 1)
            dy = (my - cy) / (ry if ry else 1)
            if dx * dx + dy * dy <= 1.0:
                return c
        return None

    def _draw_ring(self, surf, cx, cy, rx, ry, color, width=3):
        rect = pygame.Rect(0, 0, int(rx * 2), int(ry * 2))
        rect.center = (cx, cy)
        pygame.draw.ellipse(surf, color, rect, width)

    def _draw_dot(self, surf, x, y, radius=4, color=(255, 255, 255)):
        pygame.draw.circle(surf, color, (int(x), int(y)), radius)

    def _draw_legal_highlights(self, screen, row):
        rx, ry = 40 * self._sx, 30 * self._sy
        for c in range(PITS_PER_SIDE):
            if self.board[row][c] > 0:
                cx, cy = self._pit_center(row, c)
                self._draw_ring(screen, cx, cy, rx, ry, (255, 255, 180), width=4)

        prompt = self.smallfont.render(
            "Click a pit (1–6).  [H] Help  [P] CPU/2P  [O] Show 'across' guides",
            True,
            (255, 255, 255),
        )
        screen.blit(prompt, (20, 48))

    def _sow_preview_positions(self, player, pit):
        seeds = self.board[player][pit]
        if seeds <= 0:
            return []
        positions = []
        r, c = player, pit
        for _ in range(seeds):
            r, c = self._next_pos(r, c, player)
            if r == "store":
                positions.append(self._store_center(player))
            else:
                positions.append(self._pit_center(r, c))
        return positions

    def _draw_hover_preview(self, screen, player, pit):
        path = self._sow_preview_positions(player, pit)
        if not path:
            return
        for i, (x, y) in enumerate(path):
            r = 2 + min(6, i // 2)
            self._draw_dot(screen, x, y, radius=r, color=(255, 255, 255))

        final = self._simulate_move(player, pit)
        if final == ("store", player):
            sx, sy = self._store_center(player)
            tip = self.smallfont.render("Extra turn", True, (255, 255, 0))
            screen.blit(tip, tip.get_rect(midbottom=(sx, sy - 28)))
        else:
            r, c = final
            if r == player and self.board[player][c] == 0:
                opp_pit = c  # same column
                if self.board[1 - player][opp_pit] > 0:
                    ox, oy = self._pit_center(1 - player, opp_pit)
                    self._draw_ring(
                        screen,
                        ox,
                        oy,
                        34 * self._sx,
                        24 * self._sy,
                        (255, 120, 120),
                        width=3,
                    )
                    tip = self.smallfont.render("Capture", True, (255, 120, 120))
                    lx, ly = self._pit_center(player, c)
                    screen.blit(tip, tip.get_rect(midtop=(lx, ly + 22)))

    def _draw_help_overlay(self, screen):
        """Auto-sized 'How to Play & Controls' panel."""
        w, h = screen.get_size()
        pad = 14
        panel_w = min(540, w - 40)

        def wrap_lines(text, font, max_w):
            lines = []
            for raw in text.split("\n"):
                if not raw:
                    lines.append("")
                    continue
                words, cur = raw.split(" "), ""
                for wtok in words:
                    test = (cur + " " + wtok).strip()
                    if font.size(test)[0] <= max_w:
                        cur = test
                    else:
                        if cur:
                            lines.append(cur)
                        cur = wtok
                lines.append(cur)
            return lines

        title = "How to Play"
        body = (
            "Goal: Collect the most stones in your store, H to Exit.\n\n"
            "On your turn:\n"
            "- Click one of your 6 pits (or press 1–6).\n"
            "- Stones drop one-by-one counter-clockwise.\n"
            "- You skip the opponent’s store but can drop into your own.\n"
            "- If your last stone lands in your store: take another turn.\n\n"
            "Capture: If your last stone lands in an EMPTY pit on your side and the pit directly "
            "across on your opponent’s row has stones, you take ALL stones from that opposite pit "
            "plus your last stone into your store. (Opposites are mirrored left↔right; toggle [O] to see.)\n\n"
            "End: When one side’s six pits are empty, the other player moves all remaining stones "
            "on their side into their store. Highest total wins.\n\n"
        
        )

        tx = pad
        title_surf = self.medfont.render(title, True, (255, 255, 0))
        wrap_w = panel_w - pad * 2
        lines = wrap_lines(body, self.smallfont, wrap_w)

        ty = pad + title_surf.get_height() + 6
        line_h = self.smallfont.get_height() + 2
        needed_h = ty + len(lines) * line_h + pad
        panel_h = min(max(needed_h, 420), h - 40)

        x = w - panel_w - 20
        y = 20
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 200))
        pygame.draw.rect(
            panel, (255, 255, 255, 230), panel.get_rect(), width=2, border_radius=10
        )

        panel.blit(title_surf, (tx, pad))
        ty = pad + title_surf.get_height() + 6
        for ln in lines:
            if ln == "":
                ty += 6
                continue
            surf = self.smallfont.render(ln, True, (235, 235, 235))
            panel.blit(surf, (tx, ty))
            ty += line_h

        screen.blit(panel, (x, y))

    # ------------ assets ------------

    def _load_background(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Missing background image: {path}")
        bg = pygame.image.load(str(path)).convert()
        screen = pygame.display.get_surface()
        if bg.get_size() != (screen.get_width(), screen.get_height()):
            bg = pygame.transform.smoothscale(
                bg, (screen.get_width(), screen.get_height())
            )
        return bg

    def _load_spritesheet(self, path: Path):
        if path.exists():
            return pygame.image.load(str(path)).convert_alpha()
        return None

    def _extract_stone_sprite(self):
        # Pull first stone from spritesheet (64x64 cell) and scale to ~22px
        if self._spritesheet is None:
            surf = pygame.Surface((22, 22), pygame.SRCALPHA)
            pygame.draw.circle(surf, (220, 180, 40), (11, 11), 10)
            pygame.draw.circle(surf, (255, 255, 255), (6, 6), 3)
            return surf
        cell = 64
        rect = pygame.Rect(0, 0, cell, cell)
        stone = self._spritesheet.subsurface(rect).copy()
        stone = pygame.transform.smoothscale(stone, (22, 22))
        return stone

    def _stone_index_for_pit(self, row, col):
        return (row * 6 + col) % 8

    def _scatter_points(self, seed, n, cx, cy, rx, ry):
        """Deterministic random points inside an ellipse; stable for given (seed,n)."""
        rnd = random.Random(seed ^ (n * 7919))
        pts = []
        for i in range(n):
            ang = rnd.random() * 2 * math.pi
            rad = math.sqrt(rnd.random())  # sqrt for uniform density
            x = cx + rx * rad * math.cos(ang)
            y = cy + ry * rad * math.sin(ang)
            pts.append((int(x), int(y)))
        return pts

    def _draw_pit_stones(self, screen):
        """Draw stone sprites from self.piles with natural scatter."""
        max_show = 12  # draw up to this many; number label still shows true count
        for r in (0, 1):
            for c in range(PITS_PER_SIDE):
                stones = self.piles[r][c]
                n = len(stones)
                if n <= 0: continue
                cx, cy = self._pit_center(r, c)
                # ellipse radii a bit smaller than the well
                rx = 26 * self._sx
                ry = 18 * self._sy
                pts = self._scatter_points(self._pit_seed[r][c], min(n, max_show), cx, cy, rx, ry)
                for (px, py), color_idx in zip(pts, stones[:max_show]):
                    img = self._stone_idle[color_idx]
                    screen.blit(img, img.get_rect(center=(px, py)))

    def _draw_store_stones(self, screen):
        """Draw stones sitting in the stores with scatter."""
        max_show = 28
        for s in (0, 1):
            stones = self.store_piles[s]
            n = len(stones)
            if n <= 0: continue
            cx, cy = self._store_center(s)
            rx = 44 * self._sx  # store is taller/wider
            ry = 30 * self._sy
            pts = self._scatter_points(self._store_seed[s], min(n, max_show), cx, cy, rx, ry)
            for (px, py), color_idx in zip(pts, stones[:max_show]):
                img = self._stone_idle[color_idx]
                screen.blit(img, img.get_rect(center=(px, py)))

    def _is_busy(self):
        # True while a move is animating or we’re in the brief post-move pause
        return bool(self.animating or self.post_pause > 0 or self.moving is not None)

    def _enter_pause(self, seconds=0.40):
        self.post_pause = float(seconds)
        # reset CPU timer so it doesn't immediately stack another move
        self._ai_elapsed = 0.0

    def _sync_counts_from_piles(self):
        # Resync self.board/stores from self.piles/store_piles (source of truth for visuals)
        for r in (0, 1):
            for c in range(PITS_PER_SIDE):
                self.board[r][c] = len(self.piles[r][c])
        self.stores[0] = len(self.store_piles[0])
        self.stores[1] = len(self.store_piles[1])

    def _queue_finish(self, player_won: bool):
        self.winner = 1 if player_won else 0
        self._pending_outcome = "win" if player_won else "lose"
        self.pending_payload = {
            "vs_cpu": self.vs_cpu,
            "slow_mode": self.slow_mode,
            "stores": list(self.stores),
            "message": self.status_msg,
            "forfeit": self.forfeited,
        }
        subtitle = f"Stores — You {self.stores[1]} : {self.stores[0]} Opp"
        if self.net_enabled:
            winner = self.local_id if player_won else self.remote_id
            loser = self.remote_id if player_won else self.local_id
            self.pending_payload["winner"] = winner
            self.pending_payload["loser"] = loser
            self._net_send_state(kind="finish", outcome=self._pending_outcome, winner=winner, loser=loser, stores=list(self.stores))
        self.banner.show(self._pending_outcome, subtitle=subtitle)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[KalahDuel] Pause menu unavailable: {exc}")
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
                "vs_cpu": self.vs_cpu,
                "slow_mode": self.slow_mode,
                "stores": list(self.stores),
                "forfeit": self.forfeited,
            }
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.pending_payload.get("winner"):
            result["winner"] = self.pending_payload.get("winner")
        if self.pending_payload.get("loser"):
            result["loser"] = self.pending_payload.get("loser")
        self.context.last_result = result
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[KalahDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[KalahDuel] Callback error: {exc}")

    # ------------- net helpers -------------
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

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[KalahDuel] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force: bool = False, **extra):
        if not self.net_enabled:
            return
        payload = {"kind": kind, "turn": self.turn}
        payload.update(extra or {})
        self._net_send_action(payload)

    def _update_status(self):
        if self.winner is not None:
            return
        if self.turn == self.local_side:
            self.status_msg = "Your turn!"
        else:
            self.status_msg = "Opponent turn"
    def _apply_remote_action(self, action: dict):
        if not action or self._pending_outcome:
            return
        kind = action.get("kind")
        if kind == "move":
            pit = action.get("pit")
            player = action.get("turn", self.remote_side)
            if isinstance(pit, int) and 0 <= pit < PITS_PER_SIDE:
                self.turn = player
                self._make_move(player, pit, send_net=False)
            return
        if kind == "finish":
            outcome = action.get("outcome")
            win_id = action.get("winner")
            lose_id = action.get("loser")
            stores = action.get("stores", self.stores)
            self.pending_payload["winner"] = win_id
            self.pending_payload["loser"] = lose_id
            self.pending_payload["stores"] = stores
            # Map outcome to local perspective if winner/loser provided.
            if win_id or lose_id:
                if win_id and win_id == self.local_id:
                    mapped = "win"
                elif lose_id and lose_id == self.local_id:
                    mapped = "lose"
                else:
                    mapped = outcome or "lose"
            else:
                mapped = outcome or "lose"
            # Show banner locally and let update() finalize after duration.
            subtitle = f"Stores — You {stores[1]} : {stores[0]} Opp"
            self._pending_outcome = mapped
            self.banner.show(mapped, subtitle=subtitle)
            return
        # After applying remote state/move, refresh status to local perspective.
        self._update_status()

    def _rebuild_piles_from_counts(self):
        """Recreate piles from board/stores counts (visual only) to stay in sync."""
        self._rng = random.Random(1337)
        def _rand_color_idx(): return self._rng.randrange(8)
        self.piles = []
        for r in range(2):
            row = []
            for c in range(PITS_PER_SIDE):
                cnt = self.board[r][c]
                row.append([_rand_color_idx() for _ in range(cnt)])
            self.piles.append(row)
        self.store_piles = [
            [_rand_color_idx() for _ in range(self.stores[0])],
            [_rand_color_idx() for _ in range(self.stores[1])],
        ]
        self._sync_counts_from_piles()

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self.forfeited = True
        winner = self.remote_id if self.net_enabled else None
        loser = self.local_id if self.net_enabled else None
        self.pending_payload = {
            "vs_cpu": self.vs_cpu,
            "slow_mode": self.slow_mode,
            "stores": list(self.stores),
            "forfeit": True,
            "reason": "forfeit",
            "winner": winner,
            "loser": loser,
        }
        if self.net_enabled:
            self._net_send_state(kind="finish", outcome="forfeit", winner=winner, loser=loser, stores=list(self.stores))
        self._finalize("forfeit")

# Entrypoint expected by the launcher
def launch(manager, context=None, callback=None, **kwargs):
    return KalahDuelScene(manager, context, callback, **kwargs)
