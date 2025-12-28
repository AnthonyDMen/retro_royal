# minigames/rps/game.py
# RPS — sprite version; buttons wide, big hands, no score line + pick history

import os, math, random, pygame
from typing import Dict, Tuple
from scene_manager import Scene
from game_context import GameContext
from resource_path import resource_path

MULTIPLAYER_ENABLED = True

CHOICES = ["Rock", "Paper", "Scissors"]
BEATS = {"Rock": "Scissors", "Scissors": "Paper", "Paper": "Rock"}

CELL = 64
HAND_SCALE = 2  # 64→128 (hands)
TALLY_SCALE = 2  # 64→128 (tallies)
COUNTDOWN_SCALE = 2  # 64→128 (3/2/1/GO)
MATCH_BANNER_SCALE = 3

# colors for history initials
HIST_COLOR = {
    "R": (235, 200, 160),  # rock
    "P": (180, 220, 255),  # paper
    "S": (255, 200, 200),  # scissors
}


def _try_load(paths):
    for p in paths:
        if os.path.exists(p):
            return pygame.image.load(p).convert_alpha()
    return pygame.image.load(paths[-1]).convert_alpha()


def _slice(sheet: pygame.Surface) -> Dict[str, pygame.Surface]:
    def cell(r, c):
        return sheet.subsurface(pygame.Rect(c * CELL, r * CELL, CELL, CELL))

    a = {}
    a["bg_a"] = cell(0, 0)
    a["bg_b"] = cell(0, 1)
    a["bg_c"] = cell(0, 2)
    a["bg_d"] = cell(0, 3)
    a["hand_idle"] = cell(1, 0)
    a["hand_rock"] = cell(1, 1)
    a["hand_paper"] = cell(1, 2)
    a["hand_scissors"] = cell(1, 3)
    a["tally_empty"] = cell(2, 0)
    a["tally_full"] = cell(2, 1)
    a["num_3"] = cell(2, 2)
    a["num_2"] = cell(2, 3)
    a["num_1"] = cell(3, 0)
    a["go"] = cell(3, 1)
    a["banner_win"] = cell(3, 2)
    a["banner_lose"] = cell(3, 3)
    return a


class RPSScene(Scene):
    def __init__(
        self,
        manager,
        context=None,
        callback=None,
        difficulty: float = 1.0,
        spritesheet_path: str = None,
        win_rewards=None,
        lose_rewards=None,
        duel_id=None,
        participants=None,
        multiplayer_client=None,
        local_player_id=None,
        **kwargs,
    ):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.minigame_id = "rps_duel"
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.win_rewards = (
            win_rewards if win_rewards is not None else {"gold": 15}
        )
        self.lose_rewards = lose_rewards if lose_rewards is not None else {}
        self._finalized = False
        self.forfeited = False

        self.round_font = pygame.font.SysFont(None, 48)
        self.font = pygame.font.SysFont(None, 36)
        self.small = pygame.font.SysFont(None, 24)

        default_sheet = resource_path("minigames", "rps_duel", "spritesheet.png")
        if spritesheet_path is None:
            spritesheet_path = default_sheet

        self.atlas = _slice(
            _try_load([
                spritesheet_path,
                default_sheet,
            ])
        )

        # pre-scale hand sprites (+ mirrored) for crisp pixels
        self.hand_cache: Dict[str, pygame.Surface] = {}
        self.hand_mirror_cache: Dict[str, pygame.Surface] = {}
        for k in ("hand_idle", "hand_rock", "hand_paper", "hand_scissors"):
            big = pygame.transform.scale(
                self.atlas[k], (CELL * HAND_SCALE, CELL * HAND_SCALE)
            )
            self.hand_cache[k] = big
            self.hand_mirror_cache[k] = pygame.transform.flip(big, True, False)

        # general scale cache (tallies, countdown, banners)
        self.scale_cache: Dict[Tuple[str, int], pygame.Surface] = {}

        def precache(key, s):
            self.scale_cache[(key, s)] = pygame.transform.scale(
                self.atlas[key], (CELL * s, CELL * s)
            )

        for key in (
            "tally_empty",
            "tally_full",
            "num_3",
            "num_2",
            "num_1",
            "go",
            "banner_win",
            "banner_lose",
        ):
            precache(
                key,
                (
                    TALLY_SCALE
                    if "tally" in key
                    else (
                        COUNTDOWN_SCALE
                        if key in ("num_3", "num_2", "num_1", "go")
                        else MATCH_BANNER_SCALE
                    )
                ),
            )

        # multiplayer meta
        flags = getattr(context, "flags", {}) if context else {}
        self.duel_id = duel_id or flags.get("duel_id")
        self.participants = participants or flags.get("participants")
        self.duel_client = multiplayer_client or flags.get("duel_client")
        self.local_id = local_player_id or flags.get("duel_local_id")
        self.is_multiplayer = bool(flags.get("multiplayer") or self.duel_id or self.duel_client)
        self.opponent_id = None
        if self.participants and self.local_id:
            for pid in self.participants:
                if pid != self.local_id:
                    self.opponent_id = pid
                    break

        # match state
        self.round = 1
        self.player_score = 0
        self.cpu_score = 0
        self.player_choice = None
        self.cpu_choice = None
        self.result = None
        self.match_result = None
        self.difficulty = difficulty
        self.mp_scores = {self.local_id: 0, self.opponent_id: 0} if self.is_multiplayer else None
        self.mp_round = 1
        self.pending_round = None  # holds choices/winner from server

        # NEW: history
        self.player_history = []  # list of 'R'/'P'/'S'
        self.cpu_history = []
        self.history_limit = 10

        # flow state
        self.state = "SELECT"  # SELECT → COUNTDOWN → REVEAL → ROUND_END → MATCH_END
        self.state_entered_at = pygame.time.get_ticks()
        self.select_duration_ms = 10_000
        self.countdown_step_ms = 320
        self.reveal_hold_ms = 1100
        self.round_end_hold_ms = 800
        self.match_end_hold_ms = 1500
        self.count_idx = 3
        self._forfeit_done = False
        self.choice_sent = False

        # ---------- layout ----------
        self.button_rects = self._buttons_rects()

        # tallies near the top
        self.tally_y = 120
        self.tally_sep = 14  # final px gap between the two tally icons

        # hands: keep them between tallies and buttons
        button_top = self.button_rects[0].top
        half_hand = (CELL * HAND_SCALE) // 2
        target_center_y = int(self.h * 0.60)
        anchor_y = min(target_center_y, button_top - half_hand - 12)
        anchor_y = max(anchor_y, self.tally_y + half_hand + 12)
        self.left_anchor = (int(self.w * 0.35), anchor_y)
        self.right_anchor = (int(self.w * 0.65), anchor_y)

    # ---------- events ----------
    def handle_event(self, e):
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if e.type == pygame.KEYDOWN:
            if self.state == "SELECT":
                if e.key in (pygame.K_1, pygame.K_KP1):
                    self._pick("Rock")
                elif e.key in (pygame.K_2, pygame.K_KP2):
                    self._pick("Paper")
                elif e.key in (pygame.K_3, pygame.K_KP3):
                    self._pick("Scissors")
            elif self.state in ("REVEAL", "ROUND_END", "MATCH_END"):
                self._maybe_advance(force=True)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "SELECT":
                for i, r in enumerate(self.button_rects):
                    if r.collidepoint(e.pos):
                        self._pick(CHOICES[i])
                        break
            elif self.state in ("REVEAL", "ROUND_END", "MATCH_END"):
                self._maybe_advance(force=True)

    # ---------- flow ----------
    def _pick(self, choice):
        self.player_choice = choice
        if self.is_multiplayer:
            self._send_choice()
            self._enter("WAIT")
            self.count_idx = 0
            return
        self.cpu_choice = random.choice(CHOICES)
        self._enter("COUNTDOWN")
        self.count_idx = 3

    def _auto_pick_if_needed(self):
        if self.player_choice is None:
            self.player_choice = random.choice(CHOICES)
        if not self.is_multiplayer and self.cpu_choice is None:
            self.cpu_choice = random.choice(CHOICES)

    def _compute_result(self):
        # compute and log history (once per round)
        if self.player_choice and self.cpu_choice:
            self._push_history(
                self.player_choice[0].upper(), self.cpu_choice[0].upper()
            )

        if self.player_choice == self.cpu_choice:
            self.result = "Tie"
            return
        if BEATS[self.player_choice] == self.cpu_choice:
            self.result = "Win"
            self.player_score += 1
        else:
            self.result = "Lose"
            self.cpu_score += 1

    def _push_history(self, p_init: str, c_init: str):
        self.player_history.append(p_init)
        self.cpu_history.append(c_init)
        if len(self.player_history) > self.history_limit:
            self.player_history.pop(0)
        if len(self.cpu_history) > self.history_limit:
            self.cpu_history.pop(0)

    def _next_round_or_finish(self):
        if self.player_score >= 2:
            self.match_result = "Win"
            self._enter("MATCH_END")
            return
        if self.cpu_score >= 2:
            self.match_result = "Lose"
            self._enter("MATCH_END")
            return
        # next round
        self.round += 1
        self.player_choice = self.cpu_choice = self.result = None
        self._enter("SELECT")

    def _finish(self, loss):
        self._finalize("lose" if loss else "win")

    def forfeit_from_pause(self):
        if self._forfeit_done:
            return
        self._forfeit_done = True
        self.match_result = "Lose"
        self.forfeited = True
        self._finalize("forfeit")

    def _enter(self, s):
        self.state, self.state_entered_at = s, pygame.time.get_ticks()
        if self.is_multiplayer:
            if s == "SELECT":
                self.player_choice = None
                self.cpu_choice = None
                self.result = None
                self.choice_sent = False

    def _elapsed(self):
        return pygame.time.get_ticks() - self.state_entered_at

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[RPS] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _maybe_advance(self, force=False):
        if self.state == "REVEAL":
            if force or self._elapsed() >= self.reveal_hold_ms:
                self._enter("ROUND_END")
        elif self.state == "ROUND_END":
            if force or self._elapsed() >= self.round_end_hold_ms:
                if self.match_result:
                    self._enter("MATCH_END")
                else:
                    # In multiplayer, don't advance without a resolved result.
                    if self.is_multiplayer and self.result is None:
                        return
                    self._next_round_or_finish()
        elif self.state == "MATCH_END":
            if force or self._elapsed() >= self.match_end_hold_ms:
                self._finish(loss=(self.match_result == "Lose"))

    # ---------- draw helpers ----------
    def _buttons_rects(self):
        margin = 32
        bw = max(260, self.w - margin * 2)
        bw = int(self.w * 0.80) if bw > int(self.w * 0.80) else bw
        bh, gap = 58, 14
        bottom_margin = 32
        top = self.h - (bh * 3 + gap * 2) - bottom_margin
        cx = self.w // 2 - bw // 2
        return [pygame.Rect(cx, top + i * (bh + gap), bw, bh) for i in range(3)]

    def _draw_buttons(self):
        for i, r in enumerate(self.button_rects):
            base = (40, 44, 66) if self.state == "SELECT" else (24, 26, 38)
            pygame.draw.rect(self.screen, base, r, border_radius=12)
            pygame.draw.rect(self.screen, (180, 180, 220), r, 2, border_radius=12)
            label = self.font.render(f"{i+1}. {CHOICES[i]}", True, (230, 230, 240))
            self.screen.blit(label, label.get_rect(center=r.center))

    def _tile_background(self):
        tiles = [
            self.atlas["bg_a"],
            self.atlas["bg_b"],
            self.atlas["bg_c"],
            self.atlas["bg_d"],
        ]
        for y in range(0, self.h, CELL):
            for x in range(0, self.w, CELL):
                self.screen.blit(tiles[((x // CELL) + (y // CELL)) % 4], (x, y))

    def _draw_hands(self):
        def key_for(ch):
            return "hand_idle" if not ch else f"hand_{ch.lower()}"

        left_key = key_for(self.player_choice)
        right_key = key_for(self.cpu_choice)
        bob = 0
        if self.state == "COUNTDOWN":
            t = pygame.time.get_ticks() / 1000.0
            bob = int(6 * math.sin(2 * math.pi * 2.0 * t))
            alt = int((t * 6) % 2)
            left_key = right_key = "hand_idle" if alt == 0 else "hand_rock"
        if self.is_multiplayer and self.cpu_choice is None and self.state != "COUNTDOWN":
            right_key = "hand_idle"
        L = self.hand_cache[left_key]
        R = self.hand_mirror_cache[right_key]
        self.screen.blit(
            L, L.get_rect(center=(self.left_anchor[0], self.left_anchor[1] + bob))
        )
        self.screen.blit(
            R, R.get_rect(center=(self.right_anchor[0], self.right_anchor[1] + bob))
        )

    def _scaled(self, key: str, s: int) -> pygame.Surface:
        if s == 1:
            return self.atlas[key]
        surf = self.scale_cache.get((key, s))
        if surf is None:
            surf = pygame.transform.scale(self.atlas[key], (CELL * s, CELL * s))
            self.scale_cache[(key, s)] = surf
        return surf

    def _draw_tallies_top(self):
        left_cx, right_cx = int(self.w * 0.28), int(self.w * 0.72)

        def pair(cx, y, wins):
            k0 = "tally_full" if 0 < wins else "tally_empty"
            k1 = "tally_full" if 1 < wins else "tally_empty"
            s0 = self._scaled(k0, TALLY_SCALE)
            s1 = self._scaled(k1, TALLY_SCALE)
            w = s0.get_width()
            sep = self.tally_sep
            x0 = cx - (w // 2 + sep // 2)
            x1 = cx + (w // 2 + sep // 2)
            self.screen.blit(s0, s0.get_rect(center=(x0, y)))
            self.screen.blit(s1, s1.get_rect(center=(x1, y)))

        pair(left_cx, self.tally_y, self.player_score)
        pair(right_cx, self.tally_y, self.cpu_score)

    def _draw_history(self):
        """Vertical list of last N picks on each side (initials R/P/S)."""
        y0 = self.tally_y + int(CELL * (TALLY_SCALE * 0.55))  # just under tallies
        step = self.small.get_height() + 6
        # left (player)
        x_left = 24
        for i, ch in enumerate(self.player_history[-self.history_limit :]):
            c = HIST_COLOR.get(ch, (230, 230, 240))
            s = self.small.render(ch, True, c)
            # shadow for readability
            self.screen.blit(
                self.small.render(ch, True, (0, 0, 0)), (x_left + 1, y0 + i * step + 1)
            )
            self.screen.blit(s, (x_left, y0 + i * step))
        # right (CPU)
        x_right = self.w - 24
        for i, ch in enumerate(self.cpu_history[-self.history_limit :]):
            c = HIST_COLOR.get(ch, (230, 230, 240))
            s = self.small.render(ch, True, c)
            sh = self.small.render(ch, True, (0, 0, 0))
            rect = s.get_rect(topright=(x_right, y0 + i * step))
            self.screen.blit(sh, rect.move(1, 1))
            self.screen.blit(s, rect)

    def _draw_center_countdown(self):
        if self.state == "COUNTDOWN":
            key = f"num_{self.count_idx}" if self.count_idx in (3, 2, 1) else "go"
            spr = self._scaled(key, COUNTDOWN_SCALE)
            self.screen.blit(spr, spr.get_rect(center=(self.w // 2, self.h // 2 - 60)))

    def _draw_pie_timer(self, progress: float, center: Tuple[int, int], radius: int):
        if progress <= 0:
            return
        progress = max(0.0, min(1.0, progress))
        steps = max(8, int(64 * progress))
        angle = progress * 2 * math.pi
        pts = [center]
        for i in range(steps + 1):
            a = -math.pi / 2 + (i / steps) * angle
            x = center[0] + int(math.cos(a) * radius)
            y = center[1] + int(math.sin(a) * radius)
            pts.append((x, y))
        s = pygame.Surface((radius * 2 + 2, radius * 2 + 2), pygame.SRCALPHA)
        pygame.draw.polygon(
            s,
            (255, 255, 255, 42),
            [
                (px - center[0] + radius + 1, py - center[1] + radius + 1)
                for (px, py) in pts
            ],
        )
        self.screen.blit(s, (center[0] - radius - 1, center[1] - radius - 1))

    def _update_state_machine(self):
        now = pygame.time.get_ticks()
        if self.state == "SELECT":
            if now - self.state_entered_at >= self.select_duration_ms:
                self._auto_pick_if_needed()
                if self.is_multiplayer:
                    self._send_choice()
                    self._enter("WAIT")
                    self.count_idx = 0
                else:
                    self._enter("COUNTDOWN")
                    self.count_idx = 3
        elif self.state == "COUNTDOWN":
            step = (now - self.state_entered_at) // self.countdown_step_ms
            if step <= 3:
                self.count_idx = 3 - int(step)
            if step >= 4:
                if not self.is_multiplayer:
                    self._compute_result()
                    self._enter("REVEAL")
                else:
                    self.count_idx = 0
                    if not self.pending_round:
                        return
                    self._apply_mp_round(self.pending_round)
                    self.pending_round = None
                    self._enter("REVEAL")
        elif self.state == "WAIT":
            # Hold until both picks arrive.
            if self.pending_round:
                # Start full countdown once we have the round payload.
                self.state = "COUNTDOWN"
                self.state_entered_at = pygame.time.get_ticks()
                self.count_idx = 3
        elif self.state in ("REVEAL", "ROUND_END", "MATCH_END"):
            self._maybe_advance()

    def update(self, dt):
        if self.context:
            self.context.add_playtime(dt)
        if self.is_multiplayer:
            self._poll_mp_rounds()

    # ---------- frame ----------
    def draw(self):
        self._update_state_machine()
        self._tile_background()

        # Round header (no score line; tallies + history are the info)
        rnd_label = "Pick your move" if self.is_multiplayer else f"Round {self.round}"
        rnd = self.round_font.render(rnd_label, True, (255, 235, 140))
        self.screen.blit(rnd, rnd.get_rect(center=(self.w // 2, 54)))

        self._draw_tallies_top()
        # History is shown for both SP/MP; multiplayer uses opponent choices instead of CPU.
        self._draw_history()
        if not self.is_multiplayer:
            self._draw_hands()
            self._draw_center_countdown()
        else:
            # Multiplayer: show hands; only show countdown during reveal/round end to mirror SP pacing.
            if self.state in ("COUNTDOWN", "WAIT", "REVEAL", "ROUND_END", "MATCH_END"):
                self._draw_hands()
                if self.state in ("COUNTDOWN", "REVEAL", "ROUND_END", "MATCH_END"):
                    self._draw_center_countdown()

        if self.state == "SELECT":
            self._draw_pie_timer(
                self._elapsed() / self.select_duration_ms,
                center=(self.w // 2, self.h // 2 - 120),
                radius=46,
            )

        # REVEAL banners (sided)
        if self.state == "REVEAL" and self.result and self.result != "Tie":
            dim = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 120))
            self.screen.blit(dim, (0, 0))
            win, lose = self.atlas["banner_win"], self.atlas["banner_lose"]
            if self.result == "Win":
                self.screen.blit(
                    win,
                    win.get_rect(
                        center=(self.left_anchor[0], self.left_anchor[1] - 150)
                    ),
                )
                self.screen.blit(
                    lose,
                    lose.get_rect(
                        center=(self.right_anchor[0], self.right_anchor[1] - 150)
                    ),
                )
            else:
                self.screen.blit(
                    lose,
                    lose.get_rect(
                        center=(self.left_anchor[0], self.left_anchor[1] - 150)
                    ),
                )
                self.screen.blit(
                    win,
                    win.get_rect(
                        center=(self.right_anchor[0], self.right_anchor[1] - 150)
                    ),
                )

        # MATCH_END banner (center, big) then return to arena
        if self.state == "MATCH_END" and self.match_result:
            dim = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 160))
            self.screen.blit(dim, (0, 0))
            key = "banner_win" if self.match_result == "Win" else "banner_lose"
            spr = self._scaled(key, MATCH_BANNER_SCALE)
            self.screen.blit(spr, spr.get_rect(center=(self.w // 2, self.h // 2 - 20)))
            msg = self.round_font.render(
                f"You {self.match_result}!", True, (255, 235, 140)
            )
            self.screen.blit(msg, msg.get_rect(center=(self.w // 2, self.h // 2 + 70)))

        self._draw_buttons()
        hint_msg = (
            "Pick Rock/Paper/Scissors • best of 3 vs player"
            if self.is_multiplayer
            else ("Click or press 1/2/3 • Esc to pause" if self.state == "SELECT" else "Click / key to skip")
        )
        hint = self.small.render(hint_msg, True, (200, 200, 210))
        self.screen.blit(hint, hint.get_rect(center=(self.w // 2, self.h - 18)))
    def _result_payload(self, outcome):
        if self.is_multiplayer:
            return {
                "choice": self.player_choice,
                "duel_id": self.duel_id,
                "outcome": outcome,
                "scores": {"player": self.player_score, "opponent": self.cpu_score},
            }
        return {
            "rounds_played": self.round,
            "player_score": self.player_score,
            "cpu_score": self.cpu_score,
            "history": {
                "player": list(self.player_history[-self.history_limit :]),
                "cpu": list(self.cpu_history[-self.history_limit :]),
            },
            "forfeit": self.forfeited,
            "rewards": self.win_rewards if outcome == "win" else self.lose_rewards,
        }

    def _finalize(self, outcome):
        if self._finalized:
            return
        self._finalized = True
        if self.context is None:
            self.context = GameContext()
        payload = self._result_payload(outcome)
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": payload,
            "choice": payload.get("choice"),
        }
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[RPS] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[RPS] Callback error: {exc}")

    # ---------- multiplayer helpers ----------
    def _send_choice(self):
        if not self.duel_client or not self.duel_id or not self.player_choice or self.choice_sent:
            return
        try:
            self.duel_client.send_duel_choice(self.duel_id, self.player_choice)
            self.choice_sent = True
        except Exception as exc:
            print(f"[RPS] Failed to send duel choice: {exc}")

    def _poll_mp_rounds(self):
        if not self.duel_client:
            return
        if getattr(self.duel_client, "last_duel_round", None):
            payload = self.duel_client.last_duel_round
            self.duel_client.last_duel_round = None
            if payload.get("duel_id") != self.duel_id:
                return
            self.pending_round = payload

    def _apply_mp_round(self, payload):
        choices = payload.get("choices") or {}
        # Map opponent choice to cpu slot.
        if self.local_id and self.opponent_id:
            self.player_choice = choices.get(self.local_id, self.player_choice)
            self.cpu_choice = choices.get(self.opponent_id, self.cpu_choice)
        else:
            # fallback
            vals = list(choices.values())
            if vals:
                self.cpu_choice = vals[0]
        winner_pid = payload.get("winner")
        if winner_pid is None:
            self.result = "Tie"
        elif winner_pid == self.local_id:
            self.result = "Win"
            self.player_score += 1
        else:
            self.result = "Lose"
            self.cpu_score += 1
        # Record history for UI (initials).
        if self.player_choice and self.cpu_choice:
            try:
                self._push_history(self.player_choice[0].upper(), self.cpu_choice[0].upper())
            except Exception:
                pass
        scores = payload.get("scores") or {}
        if self.local_id in scores:
            self.player_score = scores.get(self.local_id, self.player_score)
        if self.opponent_id in scores:
            self.cpu_score = scores.get(self.opponent_id, self.cpu_score)
        if self.player_score >= 2:
            self.match_result = "Win"
        elif self.cpu_score >= 2:
            self.match_result = "Lose"
        # Reveal will be triggered after countdown finishes.


def launch(manager, context=None, callback=None, **kwargs):
    return RPSScene(manager, context, callback, **kwargs)
