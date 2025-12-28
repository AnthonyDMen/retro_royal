# --- Throw (toss) into circle, before bounce/roll ---
THROW_MS_RANGE   = (220, 320)   # per-die toss duration
THROW_ARC_PX     = 60           # arc peak (px) above straight line

# --- Sorting animation (tray re-order) ---
SORT_ANIM_MS     = 220          # per-die ease time into sorted slot
# ===== UI safety margin =====
SAFE_MARGIN = 8  # clamp UI so it never renders outside the screen
# minigames/poker_dice/game.py
# Dice Duel — 1v1 best-of-3, turn-based, fast/random rolls inside circle, trays top/bottom.
# Assets: keep in same folder as this file:
#   spritesheet.png  (6x4 grid, 32x32 cells)
#   background.png   (generic background for all minigames)

import os, random, math, time
import pygame
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner
from resource_path import resource_path

TITLE = "Dice Duel (Hold ’n’ Roll)"
MINIGAME_ID = "poker_dice"
MULTIPLAYER_ENABLED = True

# --- Asset paths (relative) ---
HERE = resource_path("minigames", "poker_dice")
SPRITESHEET_PATH = os.path.join(HERE, "spritesheet.png")
BACKGROUND_PATH  = os.path.join(HERE, "background.png")

# --- Spritesheet map (your layout) ---
CELL = 32
SHEET_COLS, SHEET_ROWS = 6, 4
# row 0: faces 1..6; row 1: roll frames 0..5
FACE = [(0, c, 1, 1) for c in range(6)]
ROLL = [(1, c, 1, 1) for c in range(6)]
# row 2: small banners & tally I (col3)
BANNER_SMALL = {"win": (2, 0, 1, 1), "lose": (2, 1, 1, 1), "tie": (2, 2, 1, 1)}
TALLY_I = (2, 3, 1, 1)
# row 3: big banners (2 cells wide each)
BANNER_BIG = {"win": (3, 0, 2, 1), "lose": (3, 2, 2, 1), "tie": (3, 4, 2, 1)}


# --- NEW/UPDATED TUNING ---
DICE_COUNT = 5
MAX_REROLLS = 2  # per side (in addition to the initial roll)
ROUND_WIN_TARGET = 2  # best-of-3
ROLL_ANIM_MS_RANGE = (1100, 2000)  # each die picks a random duration
ROLL_STAGGER_MAX_MS = 120  # a tiny offset so they don't sync
EASE_TO_TRAY_MS = 300
BANNER_MS = 1100
SETTLE_PAUSE_MS = 1300          # how long dice rest in the circle before sliding to trays
TALLY_SCALE = 4                 # 4x bigger “I” tallies
SIDE_BANNER_MS = 1100           # how long to show per-side small banners
PLAYER_SLOT_SQUEEZE = 0.72   # portion of tray width used for slots (lower = tighter)
NPC_SLOT_SQUEEZE    = 0.80   # tighten NPC tray too
TRAY_DIE_SCALE      = 0.92   # scale for dice drawn in trays (both sides)

TALLY_SCALE         = 3      # 3x bigger tallies
BANNER_SCALE_SMALL  = 3      # 3x small 'won/lost/tie' banners
BANNER_SCALE_BIG    = 3      # 3x big end-of-round/match banners

SORT_ANIM_MS = 300  # duration of player sort animation in ms

# --- Layout aligned to background.png ---
# Circle (center-left area, not overlapping trays)
ROLL_CX, ROLL_CY = 0.342, 0.469
ROLL_R           = 0.245   # radius as fraction of min(screen_w, screen_h)

# Trays (centered horizontally with generous margins for labels)
NPC_TRAY    = (0.055, 0.060, 0.575, 0.110)   # x, y, w, h
PLAYER_TRAY = (0.055, 0.800, 0.575, 0.110)

# Slot spacing (tight so dice sit nicely inside the trays)
PLAYER_SLOT_SQUEEZE = 0.72
NPC_SLOT_SQUEEZE    = 0.80
TRAY_DIE_SCALE      = 0.92  # draw dice slightly smaller when resting in a tray

# Right rules panel geometry (matches the background art)
UI_PANEL_W        = 360   # width of the dark panel on the right
UI_PANEL_MARGIN   = 26    # panel inset from the window edges
PANEL_INNER_PAD   = 24    # safe inner padding inside the panel

# Buttons (BANK above ROLL) – size only
BUTTON_H          = 36    # slightly shorter so it fits comfortably
BUTTON_GAP_Y      = 10    # vertical gap between the two buttons
BUTTON_RADIUS     = 8
BUTTON_WIDTH_FRAC = 0.75  # **smaller**: 78% of inner panel width (tweak 0.76–0.82)

# Controls
KEYS_HOLD = [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5]
KEY_ROLL = pygame.K_SPACE
KEY_BANK = pygame.K_RETURN

# --- DISPLAY & SORT ---
NPC_TALLY_ON_LEFT = True     # put NPC score on left of top tray
SORT_ORDER = "asc"           # "asc" or "desc" for tray dice order
HAND_LABEL_OFFSET = (8, -24) # pixels offset from player tray top-left for hand text

# --- UI NUDGES ---
TALLY_NUDGE = (-24, 0)         # x,y shift for tallies; (-) pushes left
HAND_LABEL_SCALE = 1.35        # 1.0 = same size; 1.35 ~ 35% bigger
HAND_LABEL_PLAYER_ABOVE = True # place player's hand text above their tray
HAND_LABEL_NPC_ABOVE    = False# place NPC's hand text below their tray
HAND_LABEL_Y_PAD = 6          # gap from tray edge to text
HAND_LABEL_NUDGE_X = 0        # extra horizontal nudge for centering fine-tune


# --- helpers ---
def _slice(sheet, r, c, sw=1, sh=1):
    rect = pygame.Rect(c * CELL, r * CELL, CELL * sw, CELL * sh)
    surf = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    surf.blit(sheet, (0, 0), rect)
    return surf


def _point_in_circle(cx, cy, r):
    t = 2 * math.pi * random.random()
    u = random.random()
    return cx + r * math.sqrt(u) * math.cos(t), cy + r * math.sqrt(u) * math.sin(t)


def _ease_out_quad(t):
    return 1 - (1 - t) * (1 - t)


def _lerp(a, b, t):
    return a + (b - a) * t


def _rank(vals):
    from collections import Counter

    cnt = Counter(vals)
    s = sorted(vals)
    small = s == [1, 2, 3, 4, 5]
    large = s == [2, 3, 4, 5, 6]
    counts_sorted = sorted(cnt.values(), reverse=True)
    by_val_desc = sorted(
        ((v, n) for v, n in cnt.items()), key=lambda x: (x[1], x[0]), reverse=True
    )
    faces_desc = sorted(vals, reverse=True)
    if counts_sorted == [5]:
        rid, lab = 8, "Five of a Kind"
    elif counts_sorted == [4, 1]:
        rid, lab = 7, "Four of a Kind"
    elif counts_sorted == [3, 2]:
        rid, lab = 6, "Full House"
    elif large:
        rid, lab = 5, "Large Straight"
    elif small:
        rid, lab = 4, "Small Straight"
    elif counts_sorted == [3, 1, 1]:
        rid, lab = 3, "Three of a Kind"
    elif counts_sorted == [2, 2, 1]:
        rid, lab = 2, "Two Pair"
    elif counts_sorted == [2, 1, 1, 1]:
        rid, lab = 1, "One Pair"
    else:
        rid, lab = 0, "High Die"
    tb = (rid, tuple((n, v) for v, n in by_val_desc), tuple(faces_desc))
    return rid, tb, lab


def _ai_holds(vals, rerolls_left, difficulty=1.0):
    """Simple, snappy heuristic."""
    from collections import Counter

    cnt = Counter(vals)
    holds = [False] * len(vals)
    for v, n in cnt.items():
        if n >= 3:
            for i, x in enumerate(vals):
                if x == v:
                    holds[i] = True
            return holds
    # pair
    pv = next((v for v, n in cnt.items() if n == 2), None)
    if pv:
        for i, x in enumerate(vals):
            if x == pv:
                holds[i] = True
    # straights (light)
    if difficulty >= 1.0:
        want_small = {1, 2, 3, 4}
        want_large = {3, 4, 5, 6}
        keep = set()
        for v in sorted(set(vals)):
            if v in want_small or v in want_large:
                keep.add(v)
        for i, x in enumerate(vals):
            if x in keep:
                holds[i] = True
    if not any(holds):
        hi = max(vals)
        for i, x in enumerate(vals):
            if x == hi:
                holds[i] = True
                break
    return holds


# ---------- MP deterministic helpers ----------
def _seed_rng(base: int, round_idx: int, side: str, roll_no: int, die_idx: int, salt: str = ""):
    return random.Random(f"{base}-{round_idx}-{side}-{roll_no}-{die_idx}-{salt}")


# --- Scene ---
class PokerDiceScene(Scene):
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
        self.pending_outcome = None
        self._completed = False
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", 2.5)),
            titles={
                "win": "Dice Duel Cleared!",
                "lose": "Dice Duel Failed",
                "forfeit": "Dice Duel Forfeit",
            },
        )
        self.sheet = pygame.image.load(SPRITESHEET_PATH).convert_alpha()
        self.bg = pygame.image.load(BACKGROUND_PATH).convert_alpha()

        # slice sprites
        self.faces = [_slice(self.sheet, *FACE[i]) for i in range(6)]
        self.roll_frames = [_slice(self.sheet, *ROLL[i]) for i in range(6)]
        self.banner_small = {
            k: _slice(self.sheet, *rc) for k, rc in BANNER_SMALL.items()
        }
        self.banner_big = {k: _slice(self.sheet, *rc) for k, rc in BANNER_BIG.items()}
        self.tally_I = _slice(self.sheet, *TALLY_I)

        # layout px
        self.cx = int(self.w * ROLL_CX)
        self.cy = int(self.h * ROLL_CY)
        self.cr = int(min(self.w, self.h) * ROLL_R)

        def tray(rectp):
            x, y, w, h = rectp
            return pygame.Rect(
                int(self.w * x), int(self.h * y), int(self.w * w), int(self.h * h)
            )

        self.player_tray = tray(PLAYER_TRAY)
        self.npc_tray = tray(NPC_TRAY)

        # slots (keep dice well inside tray)
        def slots(rect, squeeze=1.0):
            """Evenly spaced slot centers using only a portion of rect.width."""
            effective_w = rect.width * squeeze
            start_x = rect.centerx - effective_w / 2
            gap = effective_w / (DICE_COUNT - 1)
            y = rect.centery
            return [pygame.Vector2(start_x + i*gap, y) for i in range(DICE_COUNT)]

        self.player_slots = slots(self.player_tray, squeeze=PLAYER_SLOT_SQUEEZE)
        self.npc_slots    = slots(self.npc_tray,    squeeze=NPC_SLOT_SQUEEZE)
        # Scaled assets (nearest-neighbor)
        self.tally_big = pygame.transform.scale(
            self.tally_I,
            (self.tally_I.get_width() * TALLY_SCALE, self.tally_I.get_height() * TALLY_SCALE)
        )
        self.banner_small_scaled = {
            k: pygame.transform.scale(b, (b.get_width() * BANNER_SCALE_SMALL, b.get_height() * BANNER_SCALE_SMALL))
            for k, b in self.banner_small.items()
        }
        self.banner_big_scaled = {
            k: pygame.transform.scale(b, (b.get_width() * BANNER_SCALE_BIG, b.get_height() * BANNER_SCALE_BIG))
            for k, b in self.banner_big.items()
        }

        # dice state
        self.player = [
            {
                "val": 1,
                "held": False,
                "state": "tray",
                "pos": [self.player_slots[i].x, self.player_slots[i].y],
            }
            for i in range(DICE_COUNT)
        ]
        self.npc = [
            {
                "val": 1,
                "held": False,
                "state": "tray",
                "pos": [self.npc_slots[i].x, self.npc_slots[i].y],
            }
            for i in range(DICE_COUNT)
        ]

        # per-side roll counters (0 means not rolled yet this round)
        self.rolls_p = 0
        self.rolls_n = 0
        self.bank_p = False
        self.bank_n = False

        self.pwins, self.nwins = 0, 0
        self.round_banner = None
        self.round_banner_until = 0
        self.round_pause_until = 0  # for inter-round pause

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
        self.turn_idx = 0  # host starts
        self.turn = "player" if self.turn_idx == self.local_idx else "npc"
        self.easing_side = None  # "player" or "npc" when we are easing that side's dice
        self.rng_seed = int.from_bytes((self.duel_id or "poker").encode("utf-8"), "little", signed=False) & 0xFFFFFFFF
        self.round_idx = 0
        self.last_net = 0.0
        self.net_interval = 0.08
        self.turn_time = 20.0
        self.turn_time_left = self.turn_time
        self.state = "player_select" if self.turn == "player" else "npc_wait"
        self.pending_winner = None
        self.pending_loser = None
        self.remote_animating = False
        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    # UI: on-screen BANK (above) and ROLL (below) buttons, both wide and inside the right panel
        self._rebuild_panel_buttons()

    def _rebuild_panel_buttons(self):
        """
        Dynamically calculate and set the rects for the BANK and ROLL buttons so they are always
        centered and sized perfectly within the right rules panel, never overflowing, and stacked
        at the bottom of the panel. Uses the constants for panel geometry and button sizing.
        """
        panel_left = self.w - UI_PANEL_W - UI_PANEL_MARGIN
        panel_top = UI_PANEL_MARGIN
        panel_inner_left = panel_left + PANEL_INNER_PAD
        panel_inner_right = panel_left + UI_PANEL_W - PANEL_INNER_PAD
        panel_inner_w = panel_inner_right - panel_inner_left
        btn_w = int(panel_inner_w * BUTTON_WIDTH_FRAC)
        btn_x = panel_inner_left + (panel_inner_w - btn_w) // 2 + 50  # shift right by 10px
        btn_y_roll = self.h - UI_PANEL_MARGIN - BUTTON_H
        btn_y_bank = btn_y_roll - BUTTON_H - BUTTON_GAP_Y
        self.bank_btn = pygame.Rect(btn_x, btn_y_bank, btn_w, BUTTON_H)
        self.roll_btn = pygame.Rect(btn_x, btn_y_roll, btn_w, BUTTON_H)

    # -------- input --------
    def handle_event(self, e):
        if self.pending_outcome:
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.net_enabled and self.turn != "player":
            return
        if self.state != "player_select":
            return

        if e.type == pygame.KEYDOWN:
            if e.key in KEYS_HOLD and self.rolls_p > 0:
                idx = KEYS_HOLD.index(e.key)
                if 0 <= idx < DICE_COUNT:
                    self.player[idx]["held"] = not self.player[idx]["held"]
                    if self.net_enabled:
                        self._net_send_state(kind="hold", idx=idx, held=self.player[idx]["held"])
            elif e.key == KEY_ROLL:
                self._player_roll()
            elif e.key == KEY_BANK and self.rolls_p > 0:
                self.bank_p = True
                if self.net_enabled:
                    self._net_send_state(kind="bank", force=True)
                self._advance_after_player()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            mx, my = e.pos
            # BANK button (only after at least one roll)
            if self.bank_btn.collidepoint(mx, my) and self.rolls_p > 0 and not self.bank_p:
                self.bank_p = True
                if self.net_enabled:
                    self._net_send_state(kind="bank", force=True)
                self._advance_after_player()
                return
            # ROLL button
            if self.roll_btn.collidepoint(mx, my):
                self._player_roll()
                return
            # click player's dice to toggle holds
            if self.rolls_p > 0:
                for i, slot in enumerate(self.player_slots):
                    r = pygame.Rect(
                        int(slot.x - CELL // 2), int(slot.y - CELL // 2), CELL, CELL
                    )
                    if r.collidepoint(mx, my):
                        self.player[i]["held"] = not self.player[i]["held"]
                        if self.net_enabled:
                            self._net_send_state(kind="hold", idx=i, held=self.player[i]["held"])
                        break

    # -------- player / npc turns --------
    def _player_roll(self):
        # guard: have turns left?
        if self.rolls_p > MAX_REROLLS:  # already did initial + 2 re-rolls
            return
        if self.net_enabled and self.turn != "player":
            return
        # mark dice to roll (initial: all; re-roll: non-held)
        now = pygame.time.get_ticks()
        any_to_throw = False
        for i, d in enumerate(self.player):
            if self.rolls_p == 0 or not d["held"]:
                any_to_throw = True
                end_x, end_y = _point_in_circle(self.cx, self.cy, self.cr - 12)
                start = (self.player_slots[i].x, self.player_slots[i].y)
                d["state"] = "throw"
                d["target"] = random.randint(1, 6)
                d["throw_start"] = start
                d["throw_end"] = (end_x, end_y)
                dur = random.randint(*THROW_MS_RANGE)
                d["t_throw0"] = now
                d["t_throw1"] = now + dur
                d["throw_arc"] = THROW_ARC_PX + random.randint(-12, 12)
                d["pos"] = [start[0], start[1]]
        if not any_to_throw and self.rolls_p > 0:
            self.bank_p = True
            self._advance_after_player()
            return
        self.state = "player_throw"
        self.easing_side = "player"
        if self.net_enabled:
            self._net_send_state(kind="roll")

    def _npc_roll(self):
        if self.net_enabled:
            return
        if self.rolls_n > MAX_REROLLS:
            return self._advance_after_npc()
        now = pygame.time.get_ticks()
        if self.rolls_n == 0:
            any_to_throw = True
            for i, d in enumerate(self.npc):
                end_x, end_y = _point_in_circle(self.cx, self.cy, self.cr - 12)
                start = (self.npc_slots[i].x, self.npc_slots[i].y)
                d["state"] = "throw"
                d["target"] = random.randint(1, 6)
                d["throw_start"] = start
                d["throw_end"] = (end_x, end_y)
                dur = random.randint(*THROW_MS_RANGE)
                d["t_throw0"] = now
                d["t_throw1"] = now + dur
                d["throw_arc"] = THROW_ARC_PX + random.randint(-12, 12)
                d["pos"] = [start[0], start[1]]
        else:
            vals = [d["val"] for d in self.npc]
            holds = _ai_holds(
                vals,
                MAX_REROLLS - max(0, self.rolls_n - 1),
                difficulty=self.difficulty,
            )
            any_to_throw = False
            for i, d in enumerate(self.npc):
                if not holds[i]:
                    any_to_throw = True
                    end_x, end_y = _point_in_circle(self.cx, self.cy, self.cr - 12)
                    start = (self.npc_slots[i].x, self.npc_slots[i].y)
                    d["state"] = "throw"
                    d["target"] = random.randint(1, 6)
                    d["throw_start"] = start
                    d["throw_end"] = (end_x, end_y)
                    dur = random.randint(*THROW_MS_RANGE)
                    d["t_throw0"] = now
                    d["t_throw1"] = now + dur
                    d["throw_arc"] = THROW_ARC_PX + random.randint(-12, 12)
                    d["pos"] = [start[0], start[1]]
            # Check hand strength for early banking
            rank, _, _ = _rank(vals)
            if rank >= 6 and self.rolls_n > 0:
                self.bank_n = True
                return self._advance_after_npc()
        if not any_to_throw and self.rolls_n > 0:
            self.bank_n = True
            return self._advance_after_npc()
        self.state = "npc_throw"
        self.easing_side = "npc"
    def _update_throw(self, dt_ms):
        now = pygame.time.get_ticks()
        side = self.player if self.state == "player_throw" else self.npc
        done = True
        for d in side:
            if d.get("state") != "throw":
                continue
            done = False
            t0, t1 = d["t_throw0"], d["t_throw1"]
            t = 1.0 if now >= t1 else (now - t0) / max(1, (t1 - t0))
            x0, y0 = d["throw_start"]
            x1, y1 = d["throw_end"]
            x = _lerp(x0, x1, t)
            y = _lerp(y0, y1, t)
            arc = d.get("throw_arc", THROW_ARC_PX)
            y -= arc * (1 - (2*t - 1)**2)
            d["pos"] = [x, y]
            if t >= 1.0:
                d["state"]  = "rolling"
                d["t_start"] = now + random.randint(0, ROLL_STAGGER_MAX_MS)
                d["t_end"]   = d["t_start"] + random.randint(*ROLL_ANIM_MS_RANGE)
                d["vel"]     = [random.uniform(-90,90), random.uniform(-60,60)]
        if done:
            self.state = "player_rolling" if self.easing_side == "player" else "npc_rolling"

    def _start_sort_anim(self, side_name):
        side  = self.player if side_name == "player" else self.npc
        slots = self.player_slots if side_name == "player" else self.npc_slots
        order = sorted(range(len(side)), key=lambda i: side[i]["val"])
        now = pygame.time.get_ticks()
        for target_index, src_i in enumerate(order):
            d = side[src_i]
            d["state_sort"] = True
            d["sort_from"]  = (d["pos"][0], d["pos"][1])
            d["sort_to"]    = (slots[target_index].x, slots[target_index].y)
            d["t_sort0"]    = now
            d["t_sort1"]    = now + SORT_ANIM_MS
        self.state = "sorting_player" if side_name == "player" else "sorting_npc"
        self.sorting_side = side_name

    def _update_sorting(self, dt_ms):
        now = pygame.time.get_ticks()
        side_name = self.sorting_side
        side = self.player if side_name == "player" else self.npc
        done = True
        for d in side:
            if not d.get("state_sort"):
                continue
            done = False
            t0, t1 = d["t_sort0"], d["t_sort1"]
            t = 1.0 if now >= t1 else (now - t0) / max(1, (t1 - t0))
            e = _ease_out_quad(min(1.0, max(0.0, t)))
            sx, sy = d["sort_from"]
            tx, ty = d["sort_to"]
            d["pos"][0] = _lerp(sx, tx, e)
            d["pos"][1] = _lerp(sy, ty, e)
            if t >= 1.0:
                d["pos"] = [tx, ty]
                d["state_sort"] = False
                d["state"] = "tray"
        if done:
            if side_name == "player":
                self.player = sorted(self.player, key=lambda dd: dd["val"])
                self.rolls_p += 1
                self._advance_after_player()
                if self.net_enabled:
                    self._net_send_state(kind="state", force=True)
            else:
                self.npc = sorted(self.npc, key=lambda dd: dd["val"])
                self.rolls_n += 1
                self._advance_after_npc()

    def _advance_after_player(self):
        # After player finishes / banks, check if both are done
        p_done = self.bank_p or (self.rolls_p > MAX_REROLLS)
        n_done = self.bank_n or (self.rolls_n > MAX_REROLLS)
        if p_done and n_done and self.rolls_p > 0 and self.rolls_n > 0:
            return self._resolve_round()
        # If NPC is not done, let them take a turn
        if not n_done:
            self.turn_idx = 1 - self.local_idx  # handoff to remote
            self.turn = "npc"
            self.state = "npc_wait"
            if self.net_enabled:
                self._net_send_state(kind="turn", force=True)
            else:
                self._npc_roll()
        else:
            # If NPC is done but player isn't, go back to player (shouldn't happen here, but for symmetry)
            if not p_done:
                self.state = "player_select"
        self.turn_time_left = self.turn_time

    def _advance_after_npc(self):
        # Check end-of-round conditions
        p_done = self.bank_p or (self.rolls_p > MAX_REROLLS)
        n_done = self.bank_n or (self.rolls_n > MAX_REROLLS)
        if p_done and n_done and self.rolls_p > 0 and self.rolls_n > 0:
            return self._resolve_round()
        # If player is not done, let them take a turn
        if not p_done:
            self.turn_idx = self.local_idx
            self.turn = "player"
            self.state = "player_select"
            if self.net_enabled:
                self._net_send_state(kind="turn", force=True)
        else:
            # If player is done but NPC isn't, let NPC continue (shouldn't happen here, but for symmetry)
            if not n_done:
                self.state = "npc_wait"
                if not self.net_enabled:
                    self._npc_roll()
        self.turn_time_left = self.turn_time

    def _queue_outcome(self, outcome, subtitle=None):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        if subtitle is None:
            subtitle = f"{self.pwins} - {self.nwins}"
        self.banner.show(outcome, subtitle=subtitle)
        if self.net_enabled:
            if outcome == "win":
                self.pending_winner = self.local_id
                self.pending_loser = self.remote_id
            elif outcome in ("lose", "forfeit"):
                self.pending_winner = self.remote_id
                self.pending_loser = self.local_id
            else:
                self.pending_winner = self.pending_loser = None

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[PokerDice] Pause menu unavailable: {exc}")
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
            "rounds": {"player": self.pwins, "opponent": self.nwins},
        }
        if self.net_enabled:
            self.context.last_result["winner"] = getattr(self, "pending_winner", None)
            self.context.last_result["loser"] = getattr(self, "pending_loser", None)
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[PokerDice] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[PokerDice] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self._queue_outcome("forfeit", subtitle="Forfeit")

    # -------- net helpers --------
    def _build_side_snapshot(self, side_name):
        side = self.player if side_name == "player" else self.npc
        rolls = self.rolls_p if side_name == "player" else self.rolls_n
        bank = self.bank_p if side_name == "player" else self.bank_n
        return {
            "dice": [{"val": d["val"], "held": d["held"]} for d in side],
            "rolls": rolls,
            "bank": bank,
        }

    def _net_send_action(self, payload):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[PokerDice] net send failed: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        now = time.perf_counter()
        if not force and (now - self.last_net) < self.net_interval:
            return
        self.last_net = now
        payload = {
            "kind": kind,
            "turn": self.turn_idx,
            "round": self.round_idx,
            "player": self._build_side_snapshot("player"),  # sender's local side
            "bank_p": self.bank_p,
            "bank_n": self.bank_n,
            "pwins": self.pwins,
            "nwins": self.nwins,
        }
        payload.update(extra or {})
        self._net_send_action(payload)

    def _net_poll_actions(self, dt):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            if msg.get("from") == self.local_id:
                continue
            action = msg.get("action") or {}
            self._apply_remote_action(action, sender=msg.get("from"))

    def _apply_remote_action(self, action, sender=None):
        if not action:
            return
        kind = action.get("kind")
        if kind == "hold":
            idx = action.get("idx")
            held = action.get("held")
            if idx is not None and 0 <= idx < DICE_COUNT:
                try:
                    self.npc[idx]["held"] = bool(held)
                except Exception:
                    pass
            return
        if kind == "round_reset":
            self.round_idx = action.get("round", self.round_idx)
            return
        if kind == "round_reset":
            self.round_idx = action.get("round", self.round_idx)
            return
        if kind == "result":
            outcome = action.get("outcome")
            # outcome is from sender's perspective; map to local
            if outcome == "win":
                self.nwins += 1
                p_round, n_round = "lose", "win"
            elif outcome == "lose":
                self.pwins += 1
                p_round, n_round = "win", "lose"
            else:
                p_round = n_round = "tie"
            now = pygame.time.get_ticks()
            self.round_banner_player = p_round
            self.round_banner_npc = n_round
            self.side_banner_until = now + SIDE_BANNER_MS
            self.round_banner = p_round
            self.round_banner_until = now + BANNER_MS
            self.state = "result"
            self.round_pause_until = now + 2000
            return
        if kind in ("state", "roll", "bank"):
            snap = action.get("player") or {}
            self._apply_snapshot("npc", snap)
            # remote banks correspond to npc side here
            self.bank_n = snap.get("bank", self.bank_n)
            # Play their roll/bank animation locally, then hand turn back.
            if kind == "roll":
                self._begin_remote_roll_anim()
            else:
                # bank or state update without roll animation
                self.turn_idx = self.local_idx
                self.turn = "player"
                # If remote finished their turn, check round end
                p_done = self.bank_p or (self.rolls_p > MAX_REROLLS)
                n_done = self.bank_n or (self.rolls_n > MAX_REROLLS)
                if p_done and n_done and self.rolls_p > 0 and self.rolls_n > 0:
                    self._resolve_round()
                else:
                    self.state = "player_select"
                self.turn_time_left = self.turn_time
            return

    def _apply_snapshot(self, side_name, snap):
        if not snap:
            return
        side = self.player if side_name == "player" else self.npc
        slots = self.player_slots if side_name == "player" else self.npc_slots
        dice = snap.get("dice") or []
        for i in range(min(DICE_COUNT, len(dice))):
            d = side[i]
            d["val"] = int(dice[i].get("val", d["val"]))
            d["held"] = bool(dice[i].get("held", d["held"]))
            d["pos"] = [slots[i].x, slots[i].y]
            d["state"] = "tray"
        rolls = snap.get("rolls")
        if rolls is not None:
            if side_name == "player":
                self.rolls_p = int(rolls)
            else:
                self.rolls_n = int(rolls)
        bank = snap.get("bank")
        if bank is not None:
            if side_name == "player":
                self.bank_p = bool(bank)
            else:
                self.bank_n = bool(bank)

    def _begin_remote_roll_anim(self):
        """Animate the opponent's roll locally so turns don't overlap."""
        now = pygame.time.get_ticks()
        self.easing_side = "npc"
        self.state = "npc_throw"
        self.turn = "npc"
        self.turn_idx = 1 - self.local_idx
        self.remote_animating = True
        rng = random.Random(f"{self.rng_seed}-{self.round_idx}-npc-{self.rolls_n}")
        for i, d in enumerate(self.npc):
            start = (self.npc_slots[i].x, self.npc_slots[i].y)
            end_x, end_y = _point_in_circle(self.cx, self.cy, self.cr - 12)
            d["state"] = "throw"
            d["target"] = d["val"]
            d["throw_start"] = start
            d["throw_end"] = (end_x, end_y)
            dur = rng.randint(*THROW_MS_RANGE)
            d["t_throw0"] = now
            d["t_throw1"] = now + dur
            d["throw_arc"] = THROW_ARC_PX + rng.randint(-12, 12)
            d["pos"] = [start[0], start[1]]
        # turn timer not used during remote anim
        self.turn_time_left = self.turn_time

    # -------- update loops --------
    def update(self, dt):
        self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        dt_ms = int(dt * 1000) if isinstance(dt, float) else dt
        if self.state in ("player_rolling", "npc_rolling"):
            self._update_roll(dt_ms)
        elif self.state in ("player_throw", "npc_throw"):
            self._update_throw(dt_ms)
        elif self.state == "easing":
            self._update_ease(dt_ms)
        elif self.state in ("sorting_player", "sorting_npc"):
            self._update_sorting(dt_ms)
        elif self.state == "result":
            now = pygame.time.get_ticks()
            # Wait for both the banner and the pause to finish
            if now >= self.round_banner_until and now >= self.round_pause_until:
                if self.pwins >= ROUND_WIN_TARGET or self.nwins >= ROUND_WIN_TARGET:
                    outcome = "win" if self.pwins > self.nwins else "lose"
                    self._queue_outcome(outcome)
                    return
                # next round
                self._reset_round()
                self.state = "player_select"
        # When remote anim completes sorting, hand turn back to player.
        if self.net_enabled and self.remote_animating:
            if self.state == "player_select" and self.turn == "player":
                self.remote_animating = False

    def _update_roll(self, dt_ms):
        now = pygame.time.get_ticks()
        gravity = 60.0
        damp = 0.96
        done = True

        sides = []
        if self.state == "player_rolling":
            sides = [self.player]
        elif self.state == "npc_rolling":
            sides = [self.npc]

        for side in sides:
            for d in side:
                if d["state"] != "rolling":
                    continue
                done = False
                if now < d["t_start"]:  # stagger
                    continue
                d["vel"][1] += gravity * (dt_ms / 1000.0)
                d["pos"][0] += d["vel"][0] * (dt_ms / 1000.0)
                d["pos"][1] += d["vel"][1] * (dt_ms / 1000.0)
                # circle bounce
                dx = d["pos"][0] - self.cx
                dy = d["pos"][1] - self.cy
                dist = math.hypot(dx, dy)
                if dist > self.cr - 8:
                    nx, ny = dx / (dist + 1e-6), dy / (dist + 1e-6)
                    vdotn = d["vel"][0] * nx + d["vel"][1] * ny
                    d["vel"][0] -= 2 * vdotn * nx
                    d["vel"][1] -= 2 * vdotn * ny
                    d["vel"][0] *= damp
                    d["vel"][1] *= damp
                    d["pos"][0] = self.cx + nx * (self.cr - 8)
                    d["pos"][1] = self.cy + ny * (self.cr - 8)
                if now >= d["t_end"]:
                    d["state"] = "easing"
                    d["val"] = d["target"]
                    # delay easing so dice rest inside the circle
                    d["settle_start"] = now + SETTLE_PAUSE_MS
                    # target tray slot
                    slots = self.player_slots if side is self.player else self.npc_slots
                    idx = (self.player if side is self.player else self.npc).index(d)
                    d["slot_target"] = (slots[idx].x, slots[idx].y)

        if done:
            self.state = "easing"

    def _sort_tray(self, side_name, animate=False):
        """Sort dice by value and snap or animate them to slots in that order."""
        if side_name == "player":
            side, slots = self.player, self.player_slots
        else:
            side, slots = self.npc, self.npc_slots
        reverse = (SORT_ORDER == "desc")
        # Get sorted order
        sorted_dice = sorted(side, key=lambda d: d["val"], reverse=reverse)
        if animate and side_name == "player":
            # Store animation info for each die
            now = pygame.time.get_ticks()
            for i, d in enumerate(sorted_dice):
                d["sort_from"] = list(d["pos"])
                d["sort_to"] = [slots[i].x, slots[i].y]
                d["sort_start"] = now
                d["sort_end"] = now + SORT_ANIM_MS
            self._player_sorting = True
            self._player_sort_start = now
            self._player_sort_end = now + SORT_ANIM_MS
            self._player_sort_order = sorted_dice.copy()
        else:
            for i, d in enumerate(sorted_dice):
                d["pos"] = [slots[i].x, slots[i].y]
        # Actually reorder the list
        side[:] = sorted_dice

    def _update_ease(self, dt_ms):
        now = pygame.time.get_ticks()
        side = self.player if self.easing_side == "player" else self.npc
        finished = True

        for d in side:
            if d.get("state") != "easing":
                continue
            finished = False
            t = (now - d["settle_start"]) / max(1, EASE_TO_TRAY_MS)
            if t >= 1.0:
                d["pos"][0], d["pos"][1] = d["slot_target"]
                d["state"] = "tray"
            else:
                tt = max(0.0, min(1.0, t))  # clamp so we don't move during settle pause
                e = _ease_out_quad(tt)
                d["pos"][0] = _lerp(d["pos"][0], d["slot_target"][0], e)
                d["pos"][1] = _lerp(d["pos"][1], d["slot_target"][1], e)

        if finished:
            # start animated sorting for whichever side we just eased
            self._start_sort_anim(self.easing_side)
            return

    # -------- round/match --------
    def _resolve_round(self):
        p_vals = [d["val"] for d in self.player]
        n_vals = [d["val"] for d in self.npc]
        p_rank, p_tb, _ = _rank(p_vals)
        n_rank, n_tb, _ = _rank(n_vals)
        outcome = "tie"
        if p_tb > n_tb:
            outcome = "win"
            self.pwins += 1
        elif n_tb > p_tb:
            outcome = "lose"
            self.nwins += 1
        self.round_banner_player = outcome                 # "win"/"lose"/"tie"
        self.round_banner_npc    = "tie"
        if outcome == "win":
            self.round_banner_npc = "lose"
        elif outcome == "lose":
            self.round_banner_npc = "win"
        self.side_banner_until = pygame.time.get_ticks() + SIDE_BANNER_MS
        self.round_banner = outcome
        self.round_banner_until = pygame.time.get_ticks() + BANNER_MS
        self.state = "result"
        self.round_pause_until = pygame.time.get_ticks() + 2000  # 2 seconds pause between rounds
        if self.net_enabled:
            self._net_send_state(kind="result", force=True, outcome=outcome)

    def _reset_round(self):
        self.player = [
            {
                "val": 1,
                "held": False,
                "state": "tray",
                "pos": [self.player_slots[i].x, self.player_slots[i].y],
            }
            for i in range(DICE_COUNT)
        ]
        self.npc = [
            {
                "val": 1,
                "held": False,
                "state": "tray",
                "pos": [self.npc_slots[i].x, self.npc_slots[i].y],
            }
            for i in range(DICE_COUNT)
        ]
        self.rolls_p = self.rolls_n = 0
        self.bank_p = self.bank_n = False
        self.round_banner = None
        self.round_idx += 1
        # host starts each round
        self.turn_idx = 0
        self.turn = "player" if self.local_idx == 0 else "npc"
        self.state = "player_select" if self.turn == "player" else "npc_wait"
        self.turn_time_left = self.turn_time
        if self.net_enabled:
            self._net_send_state(kind="round_reset", force=True)

    # -------- render --------
    def draw(self):
        # background
        bg = pygame.transform.smoothscale(self.bg, (self.w, self.h))
        self.screen.blit(bg, (0, 0))

        # circle guide (comment out if your bg already shows it strongly)
        # pygame.draw.circle(self.screen, (120,0,0), (self.cx,self.cy), self.cr, 2)

        now = pygame.time.get_ticks()

        def draw_die(val, pos, rolling, in_tray=False):
            # treat both throw and rolling as spinning
            surf = (
                self.roll_frames[(now // 80) % len(self.roll_frames)]
                if rolling else self.faces[val - 1]
            )
            if in_tray:
                w = int(surf.get_width() * TRAY_DIE_SCALE)
                h = int(surf.get_height() * TRAY_DIE_SCALE)
                surf = pygame.transform.scale(surf, (w, h))
            self.screen.blit(
                surf,
                (
                    int(pos[0] - surf.get_width() // 2),
                    int(pos[1] - surf.get_height() // 2),
                ),
            )

        # NPC dice first (so player's can render over if needed)
        for d in self.npc:
            draw_die(d["val"], d["pos"], rolling=(d["state"] in ("throw", "rolling")), in_tray=(d["state"]=="tray"))
        for d in self.player:
            draw_die(d["val"], d["pos"], rolling=(d["state"] in ("throw", "rolling")), in_tray=(d["state"]=="tray"))
            if d["held"] and self.rolls_p > 0 and d["state"] == "tray":
                pygame.draw.rect(
                    self.screen,
                    (230, 190, 40),
                    pygame.Rect(
                        int(d["pos"][0] - CELL // 2),
                        int(d["pos"][1] - CELL // 2),
                        CELL,
                        CELL,
                    ),
                    2,
                )


        # ===== Banner + Tally rendering (safe & clamped) =====

        # pick the right small-banner atlas (scaled if you have it)
        _banner_small = getattr(self, "banner_small_scaled", None) or self.banner_small

        # helper: place a small banner near a tray, on-screen
        def _banner_at_tray(surf, tray_rect, prefer_above=True):
            # try above/below, then clamp to screen bounds
            above_y = tray_rect.top - surf.get_height() - 6
            below_y = tray_rect.bottom + 6
            y = above_y if (prefer_above and above_y >= SAFE_MARGIN) else below_y
            y = max(SAFE_MARGIN, min(y, self.h - surf.get_height() - SAFE_MARGIN))
            x = tray_rect.centerx - surf.get_width() // 2
            x = max(SAFE_MARGIN, min(x, self.w - surf.get_width() - SAFE_MARGIN))
            self.screen.blit(surf, (x, y))

        # helper: draw up to 2 tally markers to the LEFT of a tray, clamped
        def _blit_tallies_left(win_count, tray_rect):
            # use big tally sprite if present, else fallback to base tally cell
            tally_surf = getattr(self, "tally_big", None) or self.tally_I
            w, h = tally_surf.get_width(), tally_surf.get_height()
            # base left position and clamp
            x = tray_rect.left - w - 8
            x = max(SAFE_MARGIN, min(x, self.w - w - SAFE_MARGIN))
            y = tray_rect.centery - h // 2
            y = max(SAFE_MARGIN, min(y, self.h - h - SAFE_MARGIN))
            # show up to 2 markers (best-of-3 uses max 2 wins)
            count = min(int(win_count), 2)
            for i in range(count):
                self.screen.blit(tally_surf, (x - i*(w + 6), y))  # stack leftwards

        # --- draw per-side result banners during the result hold ---
        now = pygame.time.get_ticks()
        if self.state == "result" and now < getattr(self, "side_banner_until", 0):
            # Player: prefer ABOVE bottom tray; NPC: prefer BELOW top tray
            _banner_at_tray(_banner_small[self.round_banner_player], self.player_tray, prefer_above=True)
            _banner_at_tray(_banner_small[self.round_banner_npc],    self.npc_tray,    prefer_above=False)

        # --- draw tallies (both sides) to the LEFT of their trays ---
        _blit_tallies_left(self.pwins, self.player_tray)
        _blit_tallies_left(self.nwins, self.npc_tray)

        # ===== end banners + tallies =====

        # HUD
        hud = self.small
        p_left = max(0, MAX_REROLLS - max(0, self.rolls_p - 1))
        n_left = max(0, MAX_REROLLS - max(0, self.rolls_n - 1))
        txt = f"Your rerolls left: {p_left}   Opponent rerolls left: {n_left}    [1–5] hold  [SPACE] roll  [ENTER] bank"
        self._shadow_text(txt, (16, self.h - 26), (255, 255, 255))

        # On-screen BANK (above) and ROLL (below) buttons (only in player_select)
        if self.state == "player_select":
            # compute enabled states
            roll_enabled = (self.rolls_p <= MAX_REROLLS) and (not self.bank_p)
            bank_enabled = (self.rolls_p > 0) and (not self.bank_p)

            # BANK (top)
            col = (160, 110, 30) if bank_enabled else (80, 80, 80)
            pygame.draw.rect(self.screen, col, self.bank_btn, border_radius=8)
            pygame.draw.rect(self.screen, (255,255,255), self.bank_btn, 2, border_radius=8)
            label = "BANK" if bank_enabled else "BANK (disabled)"
            self._shadow_text(label, (self.bank_btn.x + 18, self.bank_btn.y + 10), (255,255,255))

            # ROLL (bottom)
            col = (30, 160, 90) if roll_enabled else (80, 80, 80)
            pygame.draw.rect(self.screen, col, self.roll_btn, border_radius=8)
            pygame.draw.rect(self.screen, (255,255,255), self.roll_btn, 2, border_radius=8)
            label = "ROLL" if roll_enabled else "ROLL (disabled)"
            self._shadow_text(label, (self.roll_btn.x + 18, self.roll_btn.y + 10), (255,255,255))

        # match end big banner overlay
        if self.state == "result" and (
            self.pwins >= ROUND_WIN_TARGET or self.nwins >= ROUND_WIN_TARGET
        ):
            key = (
                "win"
                if self.pwins > self.nwins
                else "lose" if self.nwins > self.pwins else "tie"
            )
            b = self.banner_big_scaled[key]
            self.screen.blit(
                b, (self.cx - b.get_width() // 2, self.cy - b.get_height() // 2)
            )

        # Player hand label (centered near player's tray)
        _, _, p_label = _rank([d["val"] for d in self.player])
        # NPC hand label (centered near NPC tray)
        _, _, n_label = _rank([d["val"] for d in self.npc])
        self._draw_hand_label(p_label, self.player_tray, above=True)
        self._draw_hand_label(n_label, self.npc_tray,    above=False)

        if self.pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.w, self.h))

    def _shadow_text(self, text, pos, color):
        s = self.small.render(text, True, (0, 0, 0))
        self.screen.blit(s, (pos[0] + 1, pos[1] + 1))
        self.screen.blit(self.small.render(text, True, color), pos)

    def _blit_shadow_surf(self, surf, pos):
        x, y = pos
        self.screen.blit(surf, (x+1, y+1))
        self.screen.blit(surf, (x, y))

    def _draw_hand_label(self, label_text, tray_rect, above=True):
        base = self.small.render(f"Hand: {label_text}", True, (255,255,255))
        if HAND_LABEL_SCALE != 1.0:
            w = int(base.get_width() * HAND_LABEL_SCALE)
            h = int(base.get_height() * HAND_LABEL_SCALE)
            base = pygame.transform.scale(base, (w, h))
        x = tray_rect.centerx - base.get_width() // 2 + HAND_LABEL_NUDGE_X
        if above:
            y = tray_rect.top - HAND_LABEL_Y_PAD - base.get_height()
        else:
            y = tray_rect.bottom + HAND_LABEL_Y_PAD
        self.screen.blit(base, (x+1, y+1))
        self.screen.blit(base, (x, y))

    def blit_tallies(self, win_count, tray_rect, left=True, nudge=(0,0)):
        gap = 8
        surf = self.tally_big
        w, h = surf.get_width(), surf.get_height()
        if left:
            x0 = tray_rect.left - w - 8 + nudge[0]
        else:
            x0 = tray_rect.right + 8 + nudge[0]
        y = tray_rect.centery - h // 2 + nudge[1]
        for i in range(win_count):
            self.screen.blit(surf, (x0 + i * (w + gap), y))

def launch(manager, context, callback, **kwargs):
    return PokerDiceScene(manager, context, callback, **kwargs)
