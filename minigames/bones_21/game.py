# Bones to 21 — 1v1 single-die race to exactly 21
# Timing spinner: immediate stop + slower default speed
#
# Changes vs last canvas version:
#  • Spinner cycles strictly 1→2→3→4→5→6→1…
#  • Second press stops *immediately* (no settle animation) and locks that face
#  • Slower default speed (tunable below)
#  • Keeps: 1s silent pause after each roll, history ribbons, enlarged die, end banner

import pygame, random
from pathlib import Path
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner
from resource_path import resource_path

TITLE = "Bones to 21"

# ===================== TUNABLES =====================
SPINNER_FRAME_MS = 100  # face advance tick (higher = slower/easier)
NPC_STOP_MS_RANGE = (260, 560)  # NPC delay to press STOP after starting (ms)
TURN_PAUSE_MS = 1000  # silent pause after each roll (ms)
DICE_SCALE = 3  # 1..4 works well with current art
# ===================================================

# --- Sheet config ---
FW = FH = 32
COLS = 4  # grid width

# Dice face indices on spritesheet
FACE_IDX = {
    1: 0 + 0 * COLS,
    2: 1 + 0 * COLS,
    3: 2 + 0 * COLS,
    4: 3 + 0 * COLS,
    5: 0 + 1 * COLS,
    6: 1 + 1 * COLS,
}

# Score track & beads
S_IDX = {
    "track_L": 0 + 3 * COLS,
    "track_mid": 1 + 3 * COLS,
    "track_R": 2 + 3 * COLS,
    "tick_sm": 3 + 3 * COLS,
    "tick_big": 0 + 4 * COLS,
    "bead_p1": 1 + 4 * COLS,
    "bead_p1sq": 2 + 4 * COLS,
    "bead_p2": 3 + 4 * COLS,
    "bead_p2sq": 0 + 5 * COLS,
}

TRACK_STOPS = 22  # 0..21
TRACK_X0, TRACK_STEP = 64, 24
TRACK_Y_P1, TRACK_Y_P2 = 230, 270

# Other timings
NPC_THINK_MS = (260, 420)
BUST_FLASH_MS = 160

# Dice history drawing (ribbons)
HIST_TILE = 24
HIST_SPACING = 4
HIST_MAX = 10
# relative to background (640x360)
RIB_TR_Y = 64  # Player 1 ribbon (top-right)
RIB_TR_RIGHT_MARGIN = 40  # distance from right edge
RIB_BL_Y = 312  # Player 2 ribbon (bottom-left)
RIB_BL_LEFT_MARGIN = 40  # distance from left edge


# ---------- robust asset lookup ----------
def _find_file(filename: str) -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        Path(resource_path("minigames", "bones_21", filename)),
        Path.cwd() / filename,  # current working dir
        here.parent / filename,  # minigames/bones_21/
        here.parent.parent / filename,  # minigames/
        here.parent.parent.parent / filename,  # project root
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _try_load(path: Path | None):
    try:
        if path and path.exists():
            return pygame.image.load(str(path)).convert_alpha()
    except Exception:
        pass
    return None


class _Sheet:
    def __init__(self, surf, fw, fh, cols):
        self.surf, self.fw, self.fh, self.cols = surf, fw, fh, cols

    def frame(self, idx):
        if not self.surf:
            return None
        c, r = idx % self.cols, idx // self.cols
        rect = pygame.Rect(c * self.fw, r * self.fh, self.fw, self.fh)
        sub = pygame.Surface((self.fw, self.fh), pygame.SRCALPHA)
        sub.blit(self.surf, (0, 0), rect)
        return sub


class Bones21Scene(Scene):
    DICE_SCALE = DICE_SCALE  # expose tunable to methods using self.DICE_SCALE

    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.big, self.font, self.small = load_game_fonts()
        self.minigame_id = "bones_21"

        self.w, self.h = manager.size
        self.screen = manager.screen
        self.view = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self.bg_pos = (0, 0)

        # assets
        bg_path = kwargs.get("bg_path")
        sheet_path = kwargs.get("sheet_path")
        bg_file = Path(bg_path) if bg_path else _find_file("background.png")
        sheet_file = Path(sheet_path) if sheet_path else _find_file("spritesheet.png")
        self.bg_img = _try_load(bg_file)
        self.sheet = _Sheet(_try_load(sheet_file), FW, FH, COLS)
        self._asset_note = []
        if not self.bg_img:
            self._asset_note.append("NO background.png (vector fallback)")
        if not self.sheet.surf:
            self._asset_note.append("NO spritesheet.png (vector fallback)")
        print("[Bones21] background:", bg_file)
        print("[Bones21] spritesheet:", sheet_file)

        banner_titles = {
            "win": "Bones 21 Cleared!",
            "lose": "Bones 21 Failed",
        }
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", 2.5)),
            titles=banner_titles,
        )
        self.pending_outcome = None
        self._completed = False
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
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.is_multiplayer = self.net_enabled
        self.labels = ["You" if i == self.local_idx else "Opponent" for i in range(2)]

        # game state
        self.turn = 0
        self.tot = [0, 0]
        self.rolls_done = 0
        # Spinner state (no settle logic)
        self.rolling = False  # spinner running
        self.roll_acc = 0.0  # accumulator for spinner ticks (ms)
        self.spinner_face = 1  # current visible face while spinning
        # Result for last completed roll
        self.roll_result = None

        self.bust_flash_until = 0
        self.finished = False
        self.winner = None

        # pause state
        self.turn_pausing = False
        self.turn_pause_until = 0

        # history
        self.hist = [[], []]

        # NPC cadence
        self.npc_next = pygame.time.get_ticks() + random.randint(*NPC_THINK_MS)
        self.npc_stop_at = 0  # when NPC will press stop
        if self.net_enabled and self.participants:
            rng = random.Random(f"bones21-start-{self.duel_id}")
            self.turn = rng.choice([0, 1])
        else:
            self.turn = 0
        # Send initial state so both sides start aligned.
        if self.net_enabled:
            self._net_send_state("init", force=True)

    # ---------- flow helpers ----------
    def _local_turn(self):
        return (not self.net_enabled) or self.turn == self.local_idx

    def _net_send_action(self, action: dict):
        if not self.net_enabled or not self.net_client or not action:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": action})
        except Exception as exc:
            print(f"[Bones21] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", extra=None, force=False):
        if not self.net_enabled:
            return
        if not force and not self._local_turn():
            return
        payload = {
            "kind": kind,
            "turn": self.turn,
            "tot": list(self.tot),
            "rolls_done": self.rolls_done,
            "rolling": self.rolling,
            "roll_result": self.roll_result,
            "spinner_face": self.spinner_face,
            "turn_pausing": self.turn_pausing,
            "pause_ms": max(0, int(self.turn_pause_until - pygame.time.get_ticks())) if self.turn_pausing else 0,
            "finished": self.finished,
            "winner_idx": self.winner,
            "hist": [list(h) for h in self.hist],
        }
        if self.bust_flash_until:
            payload["bust_ms"] = max(0, int(self.bust_flash_until - pygame.time.get_ticks()))
        if extra:
            payload.update(extra)
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
            self._net_apply_state(action)

    def _net_apply_state(self, action: dict):
        if not action:
            return
        kind = action.get("kind", "state")
        try:
            self.turn = int(action.get("turn", self.turn))
        except Exception:
            pass
        tot = action.get("tot")
        if isinstance(tot, list) and len(tot) >= 2:
            try:
                self.tot = [int(tot[0]), int(tot[1])]
            except Exception:
                self.tot = list(self.tot)
        try:
            self.rolls_done = int(action.get("rolls_done", self.rolls_done))
        except Exception:
            pass
        self.rolling = bool(action.get("rolling", False))
        try:
            self.spinner_face = int(action.get("spinner_face", self.spinner_face))
        except Exception:
            pass
        self.roll_result = action.get("roll_result", self.roll_result)
        self.turn_pausing = bool(action.get("turn_pausing", False))
        pause_ms = int(action.get("pause_ms", 0) or 0)
        if self.turn_pausing and pause_ms > 0:
            self.turn_pause_until = pygame.time.get_ticks() + pause_ms
        else:
            self.turn_pause_until = pygame.time.get_ticks()
        bust_ms = int(action.get("bust_ms", 0) or 0)
        if bust_ms:
            self.bust_flash_until = pygame.time.get_ticks() + bust_ms
        hist = action.get("hist")
        if isinstance(hist, list) and len(hist) == 2:
            self.hist = [list(hist[0] or []), list(hist[1] or [])]
        winner_idx = action.get("winner_idx")
        if winner_idx is not None:
            self.winner = winner_idx
            if not self.pending_outcome:
                outcome = "win" if winner_idx == self.local_idx else "lose"
                subtitle = f"{self.tot[0]} - {self.tot[1]}"
                self._queue_outcome(outcome, subtitle=subtitle, winner_idx=winner_idx, remote_trigger=True)
        if action.get("finished"):
            self.finished = True

    def _start_roll(self):
        if (
            self.finished
            or self.rolling
            or self.rolls_done >= 2
            or self.turn_pausing
            or self.pending_outcome
        ):
            return
        if self.net_enabled and not self._local_turn():
            return
        self.rolling = True
        self.roll_acc = 0.0
        # randomize starting face so phase is varied
        self.spinner_face = random.randint(1, 6)
        self.roll_result = None
        self._net_send_state("start")

    def _press_stop(self):
        if not self.rolling:
            return
        if self.net_enabled and not self._local_turn():
            return
        # immediate stop on the current face
        self.rolling = False
        self._end_roll(self.spinner_face)

    def _end_roll(self, value):
        now = pygame.time.get_ticks()
        self.roll_result = value
        # record history
        self.hist[self.turn].append(value)
        if len(self.hist[self.turn]) > HIST_MAX:
            self.hist[self.turn] = self.hist[self.turn][-HIST_MAX:]

        # apply score
        self.tot[self.turn] += value
        if self.tot[self.turn] == 21:
            self._finish(self.turn)
            return
        if self.tot[self.turn] > 21:
            self.tot[self.turn] = 11
            self.rolls_done = 2  # end turn immediately
            self.bust_flash_until = now + BUST_FLASH_MS

        # roll bookkeeping
        self.rolls_done += 1
        if not self.finished:
            self.turn_pausing = True
            self.turn_pause_until = now + TURN_PAUSE_MS
        self._net_send_state("roll", {"pause_ms": TURN_PAUSE_MS})

    def _next_turn(self):
        self.rolls_done = 0
        self.turn = 1 - self.turn
        self.turn_pausing = False
        self.turn_pause_until = 0
        if self.net_enabled:
            # Force-send even though it's now the opponent's turn.
            self._net_send_state(
                "turn",
                {
                    "turn": self.turn,
                    "rolls_done": self.rolls_done,
                    "turn_pausing": False,
                    "pause_ms": 0,
                },
                force=True,
            )
        else:
            if self.turn == 1:
                now = pygame.time.get_ticks()
                self.npc_next = now + random.randint(*NPC_THINK_MS)

    def _queue_outcome(self, outcome, subtitle="", winner_idx=None, remote_trigger=False):
        if self.pending_outcome:
            return
        self.finished = True
        self.pending_outcome = outcome
        if winner_idx is not None:
            self.winner = winner_idx
        if not subtitle:
            subtitle = f"{self.tot[0]} - {self.tot[1]}"
        self.banner.show(outcome, subtitle=subtitle)
        if self.net_enabled and not remote_trigger:
            extra = {"winner_idx": self.winner}
            self._net_send_state("finish", extra, force=True)

    def _finish(self, winner_idx):
        if self.pending_outcome:
            return
        self.winner = winner_idx
        outcome = "win" if winner_idx == self.local_idx else "lose"
        self._queue_outcome(outcome, winner_idx=winner_idx)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[Bones21] Pause menu unavailable: {exc}")
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
            "score": {"player": self.tot[self.local_idx], "opponent": self.tot[self.remote_idx]},
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.participants and self.winner is not None and len(self.participants) >= 2:
            try:
                win_pid = self.participants[self.winner]
                lose_pid = [p for p in self.participants if p != win_pid][0]
                result["winner"] = win_pid
                result["loser"] = lose_pid
            except Exception:
                pass
        self.context.last_result = result
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[Bones21] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[Bones21] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self.winner = self.remote_idx
        self._queue_outcome("lose", subtitle="Forfeit", winner_idx=self.winner)

    # ---------- Scene API ----------
    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return
        if self.finished or self.turn_pausing:
            return
        if event.type == pygame.KEYDOWN and self._local_turn():
            if event.key in (pygame.K_SPACE, pygame.K_RETURN):
                # toggle start/stop on player input
                if not self.rolling and self.rolls_done < 2:
                    self._start_roll()
                elif self.rolling:
                    self._press_stop()

    def update(self, dt):
        self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        now = pygame.time.get_ticks()

        # after-roll pause handling
        if self.turn_pausing and not self.finished:
            if now >= self.turn_pause_until:
                self.turn_pausing = False
                if self.net_enabled:
                    if self._local_turn() and self.rolls_done >= 2:
                        self._next_turn()
                else:
                    if self.rolls_done >= 2:
                        self._next_turn()
                    else:
                        if self.turn == 1:
                            self.npc_next = now + random.randint(*NPC_THINK_MS)
            return
        # Multiplayer safety: if we've finished our two rolls and aren't paused, push turn forward.
        if (
            self.net_enabled
            and self._local_turn()
            and not self.finished
            and not self.rolling
            and self.rolls_done >= 2
        ):
            self._next_turn()

        # NPC automation
        if not self.net_enabled and (not self.finished and self.turn == 1):
            if not self.rolling and self.rolls_done < 2 and now >= self.npc_next:
                self._start_roll()
                self.npc_stop_at = now + random.randint(*NPC_STOP_MS_RANGE)
            elif self.rolling and now >= self.npc_stop_at:
                self._press_stop()

        # Spinner update: strict loop 1→2→…→6→1 while rolling
        if self.rolling:
            # accumulate in milliseconds, advance by whole steps using divmod to avoid loops
            self.roll_acc += dt * 1000.0
            steps, self.roll_acc = divmod(self.roll_acc, SPINNER_FRAME_MS)
            if steps:
                self.spinner_face = ((self.spinner_face - 1 + int(steps)) % 6) + 1

    # ---------- rendering ----------
    def _draw_background(self):
        self.view.fill((12, 14, 18))
        self.bg_pos = (0, 0)
        if self.bg_img:
            rect = self.bg_img.get_rect(center=(self.w // 2, self.h // 2))
            self.bg_pos = (rect.left, rect.top)
            self.view.blit(self.bg_img, rect.topleft)
        else:
            for i in range(0, self.w, 32):
                pygame.draw.line(self.view, (16, 18, 26), (i, 0), (i, self.h), 1)

    def _draw_track(self, y):
        ox, oy = self.bg_pos if self.bg_img else (0, 0)
        x = TRACK_X0
        if self.sheet.surf:
            self.view.blit(
                self.sheet.frame(S_IDX["track_L"]), (ox + x - 16, oy + y - 16)
            )
            for i in range(TRACK_STOPS):
                self.view.blit(
                    self.sheet.frame(S_IDX["track_mid"]), (ox + x - 16, oy + y - 16)
                )
                tick = "tick_big" if i % 5 == 0 else "tick_sm"
                self.view.blit(
                    self.sheet.frame(S_IDX[tick]), (ox + x - 16, oy + y - 16)
                )
                x += TRACK_STEP
            self.view.blit(
                self.sheet.frame(S_IDX["track_R"]), (ox + x - 16, oy + y - 16)
            )
            return
        x = TRACK_X0
        for i in range(TRACK_STOPS):
            pygame.draw.rect(
                self.view,
                (60, 70, 85),
                (ox + x - 8, oy + y - 10, 16, 20),
                border_radius=6,
            )
            if i % 5 == 0:
                pygame.draw.rect(
                    self.view,
                    (200, 220, 255),
                    (ox + x - 1, oy + y - 8, 2, 16),
                    1,
                    border_radius=2,
                )
            x += TRACK_STEP

    def _draw_bead(self, who, y):
        ox, oy = self.bg_pos if self.bg_img else (0, 0)
        t = max(0, min(21, self.tot[who]))
        x = TRACK_X0 + t * TRACK_STEP
        squash = t % 5 == 0
        if self.sheet.surf:
            idx = (
                ("bead_p1sq" if squash else "bead_p1")
                if who == 0
                else ("bead_p2sq" if squash else "bead_p2")
            )
            self.view.blit(self.sheet.frame(S_IDX[idx]), (ox + x - 16, oy + y - 16))
        else:
            col = (255, 215, 120) if who == 0 else (120, 200, 255)
            if squash:
                pygame.draw.ellipse(self.view, col, (ox + x - 10, oy + y - 8, 20, 16))
            else:
                pygame.draw.circle(self.view, col, (ox + x, oy + y), 8)

    def _dice_center(self):
        if self.bg_img:
            ox, oy = self.bg_pos
            return ox + 320, oy + 150
        return self.w // 2, 150

    def _draw_dice(self, cx=None, cy=None):
        if cx is None or cy is None:
            cx, cy = self._dice_center()
        if self.sheet.surf:
            face = self.spinner_face if self.rolling else (self.roll_result or 1)
            frm = self.sheet.frame(FACE_IDX.get(face, FACE_IDX[1]))
            if self.DICE_SCALE != 1:
                frm = pygame.transform.scale(
                    frm, (FW * self.DICE_SCALE, FH * self.DICE_SCALE)
                )
            self.view.blit(frm, frm.get_rect(center=(cx, cy)))
            return
        size = int(36 * self.DICE_SCALE)
        pygame.draw.rect(
            self.view,
            (235, 235, 245),
            (cx - size // 2, cy - size // 2, size, size),
            border_radius=6,
        )
        pygame.draw.rect(
            self.view,
            (40, 45, 60),
            (cx - size // 2, cy - size // 2, size, size),
            2,
            border_radius=6,
        )
        val = (
            self.spinner_face
            if self.rolling
            else (self.roll_result if self.roll_result is not None else 1)
        )
        rng = random.Random(1234 + val)
        for _ in range(val):
            pygame.draw.circle(
                self.view,
                (40, 45, 60),
                (cx + rng.randint(-8, 8), cy + rng.randint(-8, 8)),
                max(3, self.DICE_SCALE),
            )

    def _draw_history(self):
        if not self.sheet.surf:
            return
        ox, oy = self.bg_pos if self.bg_img else (0, 0)
        # Player 1 — top-right, newest on RIGHT, drawn right->left
        vals1 = list(self.hist[0])[-HIST_MAX:]
        x = ox + 640 - RIB_TR_RIGHT_MARGIN - HIST_TILE
        y = oy + RIB_TR_Y
        for v in reversed(vals1):
            frm = self.sheet.frame(FACE_IDX.get(v, FACE_IDX[1]))
            if frm:
                tile = pygame.transform.scale(frm, (HIST_TILE, HIST_TILE))
                self.view.blit(tile, (x, y))
            x -= HIST_TILE + HIST_SPACING
        # Player 2 — bottom-left, newest on LEFT, drawn left->right
        vals2 = list(self.hist[1])[-HIST_MAX:]
        x = ox + RIB_BL_LEFT_MARGIN
        y = oy + RIB_BL_Y
        for v in reversed(vals2):
            frm = self.sheet.frame(FACE_IDX.get(v, FACE_IDX[1]))
            if frm:
                tile = pygame.transform.scale(frm, (HIST_TILE, HIST_TILE))
                self.view.blit(tile, (x, y))
            x += HIST_TILE + HIST_SPACING

    def draw(self):
        self._draw_background()

        # Title + HUD
        title = self.big.render(TITLE, True, (245, 235, 160))
        self.view.blit(title, (self.w // 2 - title.get_width() // 2, 36))
        pturn = (
            self.labels[self.turn]
            if self.is_multiplayer
            else ("YOU" if self.turn == 0 else "NPC")
        )
        # Dynamic hint for player
        hint = None
        if self._local_turn() and not self.turn_pausing and self.rolls_done < 2:
            if not self.rolling:
                hint = "SPACE to START"
            else:
                hint = "SPACE to STOP"
        info_text = f"Turn: {pturn}  •  Rolls this turn: {self.rolls_done}/2"
        if hint:
            info_text += f"  •  {hint}"
        info = self.font.render(info_text, True, (225, 230, 240))
        self.view.blit(info, (self.w // 2 - info.get_width() // 2, 76))

        # Bust flash
        if pygame.time.get_ticks() < self.bust_flash_until:
            flash = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            flash.fill((180, 40, 40, 80))
            self.view.blit(flash, (0, 0))

        # Tracks + beads
        self._draw_track(TRACK_Y_P1)
        self._draw_track(TRACK_Y_P2)
        self._draw_bead(0, TRACK_Y_P1)
        self._draw_bead(1, TRACK_Y_P2)

        # Labels: Player 1 (left) and Player 2 (right) on the SAME Y
        ox, oy = self.bg_pos if self.bg_img else (0, 0)
        label0 = self.labels[0] if self.is_multiplayer else "Player 1"
        label1 = self.labels[1] if self.is_multiplayer else "Player 2"
        p1_lbl = self.font.render(f"{label0}: {self.tot[0]}", True, (255, 230, 140))
        p2_lbl = self.font.render(f"{label1}: {self.tot[1]}", True, (150, 210, 255))
        label_y = oy + TRACK_Y_P1 - 40
        self.view.blit(p1_lbl, (ox + 24, label_y))
        self.view.blit(p2_lbl, (ox + 640 - 24 - p2_lbl.get_width(), label_y))

        # small badges '1' and '2' by the track starts
        for who, y, label in [(0, TRACK_Y_P1, "1"), (1, TRACK_Y_P2, "2")]:
            badge = pygame.Surface((20, 20), pygame.SRCALPHA)
            pygame.draw.rect(badge, (24, 28, 40), (0, 0, 20, 20), border_radius=6)
            pygame.draw.rect(badge, (80, 90, 120), (0, 0, 20, 20), 2, border_radius=6)
            txt = self.small.render(
                label, True, (255, 230, 160) if who == 0 else (180, 220, 255)
            )
            badge.blit(txt, (10 - txt.get_width() // 2, 10 - txt.get_height() // 2))
            self.view.blit(badge, (ox + TRACK_X0 - 28, oy + y - 10))

        # Dice and history ribbons
        self._draw_dice()
        self._draw_history()

        if self.pending_outcome:
            self.banner.draw(self.view, self.big, self.small, (self.w, self.h))

        if self._asset_note:
            note = self.small.render(
                " | ".join(self._asset_note), True, (255, 120, 120)
            )
            self.view.blit(note, (12, 8))

        self.screen.blit(self.view, (0, 0))


def launch(manager, context, callback, **kwargs):
    return Bones21Scene(manager, context, callback, **kwargs)
