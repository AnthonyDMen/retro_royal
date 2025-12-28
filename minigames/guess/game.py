# minigames/guess/game.py — Guess (1..12) with 10s timer + off-clock feedback panel
import os, math, random
import pygame
from scene_manager import Scene
from game_context import GameContext
from resource_path import resource_path

MIN_N, MAX_N = 1, 12
MAX_TRIES = 4

CELL = 64
SLICE = {
    "chip_norm":     (0*CELL, 0*CELL, CELL, CELL),
    "chip_hover":    (1*CELL, 0*CELL, CELL, CELL),
    "chip_disabled": (2*CELL, 0*CELL, CELL, CELL),
    "ov_low":        (3*CELL, 0*CELL, CELL, CELL),
    "ov_high":       (4*CELL, 0*CELL, CELL, CELL),
    "ov_ok":         (5*CELL, 0*CELL, CELL, CELL),

    "icon_time":     (7*CELL, 0*CELL, CELL, CELL),

    "disc_base":     (0*CELL, 2*CELL, 2*CELL, 2*CELL),  # 128x128
    "disc_ring":     (2*CELL, 2*CELL, 2*CELL, 2*CELL),
    "info_panel":    (4*CELL, 2*CELL, 2*CELL, 2*CELL),
    "arrow_up":      (6*CELL, 2*CELL, CELL, CELL),
    "arrow_dn":      (7*CELL, 2*CELL, CELL, CELL),

    "banner_win":    (0*CELL, 4*CELL, 4*CELL, 2*CELL),
    "banner_lose":   (4*CELL, 4*CELL, 4*CELL, 2*CELL),
}

def _load_here(*names):
    here = resource_path("minigames", "guess")
    for n in names:
        p = os.path.join(here, n)
        if os.path.isfile(p):
            return pygame.image.load(p).convert_alpha()
    return None

class SpriteSheet:
    def __init__(self, surface): self.img = surface
    def get(self, key): return self.img.subsurface(pygame.Rect(SLICE[key])).copy()

class GuessScene(Scene):
    def __init__(self, manager, context, callback, mode="single", **kwargs):
        super().__init__(manager)
        self.context = context or GameContext()
        self.callback = callback
        self.mode = mode  # "single" or "versus" (multiplayer uses "versus")
        self.screen = manager.screen
        self.w, self.h = manager.size

        # fonts
        self.font_big   = pygame.font.SysFont(None, 36)
        self.font_mid   = pygame.font.SysFont(None, 28)
        self.font_small = pygame.font.SysFont(None, 20)
        self.font_big   = pygame.font.SysFont(None, 36)
        self.font_mid   = pygame.font.SysFont(None, 28)
        self.font_small = pygame.font.SysFont(None, 20)
        self.font_tile  = pygame.font.SysFont(None, 26)   

        # art files
        sheet = _load_here("spritesheet.npg", "spritesheet.png")
        if sheet is None: raise FileNotFoundError("minigames/guess/spritesheet.npg not found")
        self.sheet = SpriteSheet(sheet)

        self.bg_img = _load_here("background.npg", "background.png", "background.jpg")
        self.bg_scaled = None

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
        if self.net_enabled:
            self.mode = "versus"

        # state
        self.target = random.randint(MIN_N, MAX_N)
        self.tries_left = MAX_TRIES
        # Track guesses separately per side so you can't see the opponent's hints.
        self.guessed = {0: {}, 1: {}}  # idx -> n -> "low"|"high"|"correct"
        self.feedback = "Pick a number between 1 and 12."
        self.selected_idx = 0
        self.phase = "pick" if self.net_enabled else "play"
        self.my_secret = None
        self.remote_ready = False
        self.local_ready = False
        self.waiting_result = False
        self.turn_idx = 0  # which participant's turn (0 or 1)
        self.winner_id = None
        self.loser_id = None

        # timers
        self.turn_time_ms = 10000 if self.mode == "single" else 15000  # 15s per turn in multiplayer
        self.turn_left_ms = self.turn_time_ms

        # layout — move grid lower to show clock
        CHIP = 64; GAP = 10; cols = 6
        total_w = cols*CHIP + (cols-1)*GAP
        total_h = 2*CHIP + GAP
        left_x = self.w//2 - total_w//2

        # center disc first (so we can keep space below it)
        self.disc_base = self.sheet.get("disc_base")
        self.disc_ring = self.sheet.get("disc_ring")
        self.disc_rect = self.disc_base.get_rect(center=(self.w//2, int(self.h*0.38)))

        # feedback panel under disc (scaled once)
        panel_w = min(640, int(self.w*0.72))
        panel_h = 56
        self.info_panel = pygame.transform.smoothscale(self.sheet.get("info_panel"), (panel_w, panel_h))
        self.info_rect = self.info_panel.get_rect(midtop=(self.w//2, self.disc_rect.bottom + 16))

        # ensure grid starts below the panel
        min_grid_top = self.info_rect.bottom + 16
        default_top  = max(self.h - total_h - 120, int(self.h*0.58))
        top_y = max(min_grid_top, default_top)
        bot_y = top_y + CHIP + GAP

        self.num_rects = []
        n = MIN_N
        for y in (top_y, bot_y):
            for c in range(cols):
                r = pygame.Rect(left_x + c*(CHIP+GAP), y, CHIP, CHIP)
                self.num_rects.append((n, r)); n += 1

        # banners
        self.banner_kind = None
        self.banner_timer_ms = 0
        self.banner_win  = self.sheet.get("banner_win")
        self.banner_lose = self.sheet.get("banner_lose")
        self._completed = False

        # (versus prep)
        self.opponent_marks = {}
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    # ---------- logic ----------
    def _is_my_turn(self):
        if not self.net_enabled:
            return True
        return self.phase == "play" and self.turn_idx == self.local_idx

    def _pick_secret(self, value: int):
        if self.my_secret is not None or not (MIN_N <= value <= MAX_N):
            return
        self.my_secret = value
        self.local_ready = True
        self.feedback = "Waiting for opponent to pick a number..."
        if self.net_enabled:
            self._net_send_state(kind="ready", force=True)
        self._maybe_start_play()

    def _maybe_start_play(self):
        if self.phase != "pick":
            return
        if (self.local_ready and (not self.net_enabled or self.remote_ready)):
            self.phase = "play"
            self.turn_idx = 0  # idx0 starts
            self.turn_left_ms = self.turn_time_ms
            self.feedback = "Your turn! Guess the number." if self._is_my_turn() else "Opponent's turn."

    def _auto_pick(self):
        remaining = [n for n, _ in self.num_rects if n not in self.guessed]
        if not remaining:
            return
        n_sel, _ = self.num_rects[self.selected_idx]
        choice = n_sel if n_sel in remaining else random.choice(remaining)
        self._submit(choice)

    def _handle_hotkey_guess(self, val: int):
        if self.phase == "pick":
            self._pick_secret(val)
        elif self._is_my_turn():
            self._submit(val)

    def _submit(self, value: int):
        if not self.net_enabled:
            # single-player flow
            if not (MIN_N <= value <= MAX_N) or value in self.guessed[self.local_idx]:
                return
            if value == self.target:
                self.guessed[self.local_idx][value] = "correct"
                self.feedback = f"Correct! It was {self.target}."
                self.banner_kind, self.banner_timer_ms = "win", 4000
                return

            self.tries_left -= 1
            if self.tries_left <= 0:
                self.guessed[self.local_idx][value] = "low" if value > self.target else "high"
                self.feedback = f"Out of tries! It was {self.target}."
                self.banner_kind, self.banner_timer_ms = "lose", 4000
                return

            hint = "low" if value > self.target else "high"
            self.guessed[self.local_idx][value] = hint
            self.feedback = "Lower!" if hint == "low" else "Higher!"

            # nudge selection toward hint
            if hint == "high":
                for i, (n, _) in enumerate(self.num_rects):
                    if n > value and n not in self.guessed[self.local_idx]:
                        self.selected_idx = i
                        break
            else:
                for i in range(len(self.num_rects) - 1, -1, -1):
                    n, _ = self.num_rects[i]
                    if n < value and n not in self.guessed[self.local_idx]:
                        self.selected_idx = i
                        break

            self.turn_left_ms = self.turn_time_ms
            return

        # multiplayer flow
        if self.phase == "pick":
            self._pick_secret(value)
            return
        if self.waiting_result or not self._is_my_turn():
            return
        if not (MIN_N <= value <= MAX_N) or value in self.guessed[self.local_idx]:
            return
        # send guess to opponent; await result
        self.waiting_result = True
        self._net_send_state(kind="guess", force=True, guess=value)
        self.feedback = f"Guessed {value}, waiting..."

    def _compare_local(self, value: int):
        if value == self.target:
            return "correct"
        return "low" if value > self.target else "high"

    def _apply_guess_result(self, value: int, hint: str, winner=None, loser=None, toggle_turn: bool = True, store_idx=None):
        self.waiting_result = False
        # Store per-side: only store our own guesses locally; opponent guesses stay on their slot.
        if store_idx is None:
            store_idx = self.local_idx if self._is_my_turn() else self.remote_idx
        if store_idx is None:
            store_idx = 0
        self.guessed[store_idx][value] = hint
        if hint == "correct":
            self.banner_kind, self.banner_timer_ms = ("win", 4000) if (not self.net_enabled or self._is_my_turn()) else ("lose", 4000)
            self.feedback = f"{value} is correct!"
            self.winner_id = winner
            self.loser_id = loser
            if self.net_enabled and winner and loser:
                # ensure we keep track of victory
                self.banner_kind = "win" if winner == self.local_id else "lose"
            return

        self.feedback = "Lower!" if hint == "low" else "Higher!"
        # nudge selection toward hint
        if hint == "high":
            for i, (n, _) in enumerate(self.num_rects):
                if n > value and n not in self.guessed:
                    self.selected_idx = i
                    break
        else:
            for i in range(len(self.num_rects) - 1, -1, -1):
                n, _ = self.num_rects[i]
                if n < value and n not in self.guessed:
                    self.selected_idx = i
                    break
        if toggle_turn and self.net_enabled:
            self.turn_idx = 1 - self.turn_idx
        self.turn_left_ms = self.turn_time_ms
        self.feedback = "Your turn! Guess the number." if self._is_my_turn() else "Opponent's turn."

    # ---------- events ----------
    def handle_event(self, e):
        if self.banner_timer_ms > 0: return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            for i, (n, r) in enumerate(self.num_rects):
                if r.collidepoint(x, y) and n not in self.guessed:
                    self.selected_idx = i
                    if self.phase == "pick":
                        self._pick_secret(n)
                    elif self._is_my_turn():
                        self._submit(n)
                    break
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_RIGHT, pygame.K_d):   self.selected_idx = min(self.selected_idx+1, len(self.num_rects)-1)
            elif e.key in (pygame.K_LEFT,  pygame.K_a): self.selected_idx = max(self.selected_idx-1, 0)
            elif e.key in (pygame.K_UP,    pygame.K_w): self.selected_idx = max(self.selected_idx-6, 0)
            elif e.key in (pygame.K_DOWN,  pygame.K_s): self.selected_idx = min(self.selected_idx+6, len(self.num_rects)-1)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                n, _ = self.num_rects[self.selected_idx]
                if n not in self.guessed:
                    if self.phase == "pick":
                        self._pick_secret(n)
                    elif self._is_my_turn():
                        self._submit(n)
            elif pygame.K_1 <= e.key <= pygame.K_9: self._handle_hotkey_guess(e.key - pygame.K_0)
            elif e.key == pygame.K_0: self._handle_hotkey_guess(10)
            elif e.key in (pygame.K_MINUS, pygame.K_KP_MINUS): self._handle_hotkey_guess(11)
            elif e.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS): self._handle_hotkey_guess(12)
            elif e.key == pygame.K_ESCAPE:
                self._pause_game()

    # ---------- update/draw ----------
    def update(self, dt):
        # poll net early
        self._net_poll_actions(dt)
        ms = int(dt*1000) if dt < 10 else int(dt)
        if self.bg_img and (self.bg_scaled is None or self.bg_scaled.get_size() != (self.w, self.h)):
            self.bg_scaled = pygame.transform.smoothscale(self.bg_img, (self.w, self.h))

        if self.banner_timer_ms > 0:
            self.banner_timer_ms -= ms
            if self.banner_timer_ms <= 0:
                self._finalize()
            return

        if self.phase == "play" and self._is_my_turn() and not self.waiting_result:
            prev = self.turn_left_ms
            self.turn_left_ms = max(0, self.turn_left_ms - ms)
            if prev > 0 and self.turn_left_ms == 0:
                self._auto_pick()

    def _finalize(self, outcome=None):
        if self._completed:
            return
        if outcome is None:
            if not self.banner_kind:
                return
            outcome = "win" if self.banner_kind == "win" else "lose"
        self._completed = True
        if self.context:
            res = {"minigame": "guess", "outcome": outcome}
            if self.duel_id:
                res["duel_id"] = self.duel_id
            if self.winner_id:
                res["winner"] = self.winner_id
            if self.loser_id:
                res["loser"] = self.loser_id
            self.context.last_result = res
        if hasattr(self.manager, "pop"):
            self.manager.pop()
        if self.callback:
            self.callback(self.context)

    def forfeit_from_pause(self):
        self.feedback = "Forfeit. Returning to arena."
        self.banner_kind = "lose"
        self.banner_timer_ms = 0
        if self.net_enabled and self.remote_id:
            self.winner_id = self.remote_id
            self.loser_id = self.local_id
            self._net_send_state(
                kind="result",
                force=True,
                value=-1,
                hint="correct",
                turn=self.turn_idx,
                winner=self.winner_id,
                loser=self.loser_id,
            )
        self._finalize("forfeit")

    def _center_blit_text(self, surf, text, font, color, center):
        s = font.render(text, True, color); r = s.get_rect(center=center); surf.blit(s, r)

    def draw(self):
        # background
        if self.bg_scaled: self.screen.blit(self.bg_scaled, (0,0))
        else: self.screen.fill((15,17,26))

        # title
        if self.net_enabled:
            title_txt = "Guess 1–12  •  First to hit opponent's number wins"
        else:
            title_txt = f"Guess 1–12  •  Tries: {self.tries_left}"
        title = self.font_big.render(title_txt, True, (255,235,140))
        self.screen.blit(title, title.get_rect(midtop=(self.w//2, 22)))

        # center clock disc
        self.screen.blit(self.disc_base, self.disc_rect)

        # numeric countdown in disc + arc
        show_ms = self.turn_left_ms if self._is_my_turn() else 0
        pct = 0 if self.turn_time_ms == 0 else max(0.0, min(1.0, show_ms / self.turn_time_ms))
        if pct > 0:
            cx, cy = self.disc_rect.center; rad, thick = 60, 8
            start, end = -90, -90 + int(360*pct)
            pygame.draw.arc(self.screen, (255,230,110), (cx-rad, cy-rad, rad*2, rad*2),
                            math.radians(start), math.radians(end), thick)
            secs = max(0, math.ceil(show_ms/1000))
            self._center_blit_text(self.screen, str(secs), self.font_big, (255,235,140), self.disc_rect.center)

        self.screen.blit(self.disc_ring, self.disc_rect)

        # feedback PANEL (moved off clock)
        self.screen.blit(self.info_panel, self.info_rect)
        self._center_blit_text(self.screen, self.feedback, self.font_mid, (235,235,250), self.info_rect.center)

        # (optional versus top bar can go here later)

        # number chips (lower)
        for i, (n, r) in enumerate(self.num_rects):
            guessed = n in self.guessed[self.local_idx]
            chip = (self.sheet.get("chip_disabled") if guessed
                    else self.sheet.get("chip_hover") if i == self.selected_idx
                    else self.sheet.get("chip_norm"))
            self.screen.blit(chip, r)

            if guessed:
                mark = self.guessed[self.local_idx][n]
                overlay = "ov_ok" if mark == "correct" else ("ov_low" if mark=="low" else "ov_high")
                self.screen.blit(self.sheet.get(overlay), r)

            color = (240,240,255) if not guessed else (150,150,175)
            ns = self.font_tile.render(str(n), True, color)
            self.screen.blit(ns, ns.get_rect(center=r.center))

        # banner if active
        if self.banner_timer_ms > 0 and self.banner_kind:
            banner = self.sheet.get("banner_win" if self.banner_kind == "win" else "banner_lose")
            self.screen.blit(banner, banner.get_rect(center=(self.w//2, self.h//2)))

        # footer hint
        if self.phase == "pick":
            hint_text = "Pick your secret number.  Esc to pause."
        else:
            turn_txt = "Your turn" if self._is_my_turn() else "Opponent turn"
            hint_text = f"{turn_txt} • Click or arrows + Enter.  Esc to pause."
        hint = self.font_small.render(hint_text, True, (210,210,230))
        self.screen.blit(hint, hint.get_rect(midbottom=(self.w//2, self.h-16)))

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[Guess] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    # ---------- net helpers ----------
    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[Guess] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        payload = {"kind": kind}
        payload.update(extra or {})
        self._net_send_action(payload)

    def _apply_remote_action(self, action: dict, sender: str):
        if not action:
            return
        kind = action.get("kind")
        if kind == "ready":
            self.remote_ready = True
            self._maybe_start_play()
            return
        if kind == "guess":
            guess = action.get("guess")
            if guess is None or self.my_secret is None:
                return
            try:
                gval = int(guess)
            except Exception:
                return
            if gval in self.guessed[self.remote_idx]:
                return
            # evaluate against our secret
            if gval == self.my_secret:
                hint = "correct"
                winner = sender
                loser = self.local_id
            elif gval > self.my_secret:
                hint = "low"
                winner = loser = None
            else:
                hint = "high"
                winner = loser = None
            # apply locally (store opponent's guesses in their slot)
            self.guessed[self.remote_idx][gval] = hint
            if hint == "correct":
                self.banner_kind = "lose"
                self.banner_timer_ms = 4000
                self.feedback = f"Opponent guessed {gval}. You lose."
                self.winner_id = winner
                self.loser_id = loser
                self.turn_idx = self.remote_idx
            else:
                self.feedback = "Your turn! Guess the number."
                self.turn_idx = self.local_idx
                self.turn_left_ms = self.turn_time_ms
            # send result back
            self._net_send_state(
                kind="result",
                force=True,
                value=gval,
                hint=hint,
                turn=self.turn_idx,
                winner=winner,
                loser=loser,
            )
            return
        if kind == "result":
            val = action.get("value")
            hint = action.get("hint")
            if val is None or hint not in ("low", "high", "correct"):
                return
            try:
                ival = int(val)
            except Exception:
                return
            winner = action.get("winner")
            loser = action.get("loser")
            if winner:
                self.winner_id = winner
            if loser:
                self.loser_id = loser
            self.turn_idx = action.get("turn", self.turn_idx)
            self.waiting_result = False
            self._apply_guess_result(ival, hint, winner=winner, loser=loser, toggle_turn=False, store_idx=self.local_idx)
            # reset timer if we gained the turn
            if self._is_my_turn():
                self.turn_left_ms = self.turn_time_ms

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
            self._apply_remote_action(action, sender or "")

def launch(manager, context=None, callback=None, mode="single", **kwargs):
    print("[Guess] Launched minigame")
    return GuessScene(manager, context, callback, mode=mode, **kwargs)
