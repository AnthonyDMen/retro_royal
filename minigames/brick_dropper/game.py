# minigames/brick_dropper/game.py
import math
import random
import copy
import pygame
from scene_manager import Scene
from game_context import GameContext
from typing import Optional, Dict, Any, List

TITLE = "Brick Dropper Duel"
MINIGAME_ID = "brick_dropper"

# -------------------------
# Base Config (tweak-friendly)
# -------------------------
BASE_CFG = {
    # Field/grid
    "COLS": 12,  # horizontal columns per pane
    "BASE_WIDTH": 5,  # base row width (in blocks)
    "FINISH_HEIGHT": 12,  # reach this height to win
    # Motion curve (columns/sec) — faster & steeper
    "SPEED_BASE": 4.2,
    "SPEED_STEP": 0.45,
    "SPEED_NEAR_FINISH_BOOST": 0.80,
    # Visuals / HUD
    "BG": (15, 16, 22),
    "GRID": (30, 34, 46),
    "FIELD_BG": (18, 20, 28),
    "FIELD_BORDER": (70, 76, 96),
    "FALL_TIME": 0.80,  # seconds until fallers disappear
    "BANNER_TIME": 2.8,  # inter-round banner duration (longer to see result)
    "READY_COUNTDOWN": 3.0,  # seconds (3..2..1)
    "FPS": 60,
    # Color ramps
    "COLOR_BASE": (90, 200, 255),
    "COLOR_TOP": (255, 120, 100),
    # AI (level-scaled error in blocks; see AIPlayer)
    "AI_ERR_EASY": (2, 3),
    "AI_ERR_HARD": (0, 1),
    "AI_TOLERANCE": 0.18,  # blocks; how close to "target" before pulling the trigger
    "AI_REACT_MIN": 0.03,  # seconds reaction
    "AI_REACT_MAX": 0.12,
    "AI_BRAIN_FART_PCT": 0.03,  # small chance to be late/early on purpose
}

# Working CFG (mutated by difficulty)
CFG = copy.deepcopy(BASE_CFG)


# Fallback font loader if content_registry isn't available
def _fallback_fonts():
    pygame.font.init()
    big = pygame.font.SysFont(None, 42)
    mid = pygame.font.SysFont(None, 26)
    sml = pygame.font.SysFont(None, 18)
    return big, mid, sml


try:
    from content_registry import load_game_fonts  # type: ignore
except Exception:
    load_game_fonts = None


def _get_fonts():
    if load_game_fonts:
        try:
            return load_game_fonts()
        except Exception:
            pass
    return _fallback_fonts()


# -------------------------
# Helpers
# -------------------------
def clamp(v, a, b):
    return max(a, min(b, v))


def lerp(a, b, t):
    return a + (b - a) * t


def color_for_level(level, total=12, base=(90, 200, 255), top=(255, 120, 100)):
    t = clamp((level - 1) / max(1, total - 1), 0, 1)
    return (
        int(lerp(base[0], top[0], t)),
        int(lerp(base[1], top[1], t)),
        int(lerp(base[2], top[2], t)),
    )


def _normalize_difficulty(value):
    """
    Accepts strings or numbers and returns 'easy' | 'normal' | 'hard'.
    Numbers map as:
      <=0.5 -> easy, 0.51..1.5 -> normal, >1.5 -> hard.
    Strings accept aliases: e/0, n/1/m/medium, h/2.
    """
    try:
        if isinstance(value, str):
            s = value.strip().lower()
            if s in ("e", "easy", "0"):
                return "easy"
            if s in ("n", "normal", "1", "m", "medium"):
                return "normal"
            if s in ("h", "hard", "2"):
                return "hard"
        else:
            num = float(value)
            if num <= 0.5:
                return "easy"
            if num <= 1.5:
                return "normal"
            return "hard"
    except Exception:
        pass
    return "normal"


# -------------------------
# Core objects
# -------------------------
class Row:
    def __init__(self, cols, width, left, level, dir_sign, speed_cps, color):
        self.cols = cols
        self.width = width
        self.left = float(left)
        self.level = level
        self.dir = dir_sign
        self.speed = speed_cps  # columns per second
        self.color = color
        self.prev_left = float(left)

    def update(self, dt):
        self.prev_left = self.left
        self.left += self.dir * self.speed * dt
        # bounce on walls
        if self.left < 0:
            self.left = -self.left
            self.dir *= -1
        if self.left + self.width > self.cols:
            over = (self.left + self.width) - self.cols
            self.left = self.cols - self.width - over
            self.dir *= -1

    def int_left(self):
        return int(round(self.left))


class Faller:
    def __init__(self, left_block, width_blocks, level, ttl, color):
        self.left = float(left_block)
        self.width = width_blocks
        self.y = float(level)  # start at level (grid coords; 0 floor-line)
        self.vy = 10.0  # grid blocks per second (visual)
        self.ttl = ttl
        self.color = color
        self.alive = True

    def update(self, dt):
        self.y += self.vy * dt
        self.ttl -= dt
        if self.ttl <= 0:
            self.alive = False


class StackWindow:
    def __init__(self, rect, cols, base_w, finish_h, player_name="Player"):
        self.rect = rect
        self.cols = cols
        self.finish_h = finish_h
        self.player_name = player_name

        self.base_w = base_w
        self.static_rows = []  # list of (left,width,level,color)
        self.row = None  # current moving Row
        self.fallers = []  # animated scraps

        self.finished = False
        self.finish_width = 0
        self.crashed = False

        self.reset_round()

    def pack_state(self) -> Dict[str, Any]:
        row_state = None
        if self.row:
            row_state = {
                "left": self.row.left,
                "width": self.row.width,
                "level": self.row.level,
                "dir": self.row.dir,
                "speed": self.row.speed,
                "color": self.row.color,
                "prev_left": self.row.prev_left,
            }
        return {
            "static_rows": list(self.static_rows),
            "row": row_state,
            "finished": self.finished,
            "finish_width": self.finish_width,
            "crashed": self.crashed,
        }

    def apply_state(self, state: Dict[str, Any]):
        self.static_rows = [tuple(s) for s in state.get("static_rows", [])]
        self.fallers = []  # drop transient debris to simplify syncing
        self.finished = bool(state.get("finished", False))
        self.crashed = bool(state.get("crashed", False))
        self.finish_width = int(state.get("finish_width", self.finish_width))
        row_state = state.get("row")
        if row_state:
            self.row = Row(
                self.cols,
                row_state.get("width", self.base_w),
                row_state.get("left", 0.0),
                row_state.get("level", 1),
                row_state.get("dir", 1),
                row_state.get("speed", self.speed_for_level(1)),
                row_state.get("color", color_for_level(1, self.finish_h, CFG["COLOR_BASE"], CFG["COLOR_TOP"])),
            )
            self.row.prev_left = float(row_state.get("prev_left", self.row.left))
        else:
            self.row = None

    def level_now(self):
        return len(self.static_rows) + 1

    def speed_for_level(self, level):
        sp = CFG["SPEED_BASE"] + (level - 1) * CFG["SPEED_STEP"]
        if level >= max(10, self.finish_h - 2):
            sp += CFG["SPEED_NEAR_FINISH_BOOST"]
        return sp

    def reset_round(self):
        self.static_rows.clear()
        self.fallers.clear()
        # Initialize base row at level 1
        left = (self.cols - self.base_w) // 2
        base_color = color_for_level(
            1, self.finish_h, CFG["COLOR_BASE"], CFG["COLOR_TOP"]
        )
        self.static_rows.append((left, self.base_w, 1, base_color))
        # Spawn moving row (level 2)
        dir_sign = 1 if random.random() < 0.5 else -1
        c = color_for_level(2, self.finish_h, CFG["COLOR_BASE"], CFG["COLOR_TOP"])
        self.row = Row(
            self.cols,
            self.base_w,
            left=0 if dir_sign > 0 else (self.cols - self.base_w),
            level=2,
            dir_sign=dir_sign,
            speed_cps=self.speed_for_level(2),
            color=c,
        )
        self.finished = False
        self.finish_width = 0
        self.crashed = False

    def lock(self):
        """Stop the current row, compute overlap with last static row, spawn next or finish/crash."""
        if not self.row or self.finished or self.crashed:
            return

        r = self.row
        last_left, last_w, _, _ = self.static_rows[-1]
        L = int(round(r.left))
        R = L + r.width
        L2 = last_left
        R2 = last_left + last_w

        overlap_L = max(L, L2)
        overlap_R = min(R, R2)
        overlap = max(0, overlap_R - overlap_L)

        # Make fallers for scraps (visual)
        if L < L2:
            w = max(0, min(r.width, L2 - L))
            if w > 0:
                self.fallers.append(Faller(L, w, r.level, CFG["FALL_TIME"], r.color))
        if R > R2:
            w = max(0, min(r.width, R - R2))
            if w > 0:
                self.fallers.append(Faller(R2, w, r.level, CFG["FALL_TIME"], r.color))

        if overlap <= 0:
            self.crashed = True
            return

        placed_left = overlap_L
        placed_w = overlap
        placed_color = r.color
        self.static_rows.append((placed_left, placed_w, r.level, placed_color))

        if r.level >= self.finish_h:
            self.finished = True
            self.finish_width = placed_w
            return

        # spawn next row
        next_level = r.level + 1
        next_dir = -r.dir
        next_color = color_for_level(
            next_level, self.finish_h, CFG["COLOR_BASE"], CFG["COLOR_TOP"]
        )
        start_left = 0 if next_dir > 0 else (self.cols - placed_w)
        self.row = Row(
            self.cols,
            placed_w,
            start_left,
            next_level,
            next_dir,
            self.speed_for_level(next_level),
            next_color,
        )

    def update(self, dt):
        # Update fallers
        for f in self.fallers:
            f.update(dt)
        self.fallers = [f for f in self.fallers if f.alive]

        # Move row
        if self.row and not (self.finished or self.crashed):
            self.row.update(dt)

    def draw(self, surf, cell, fonts, show_hud=True, is_player=True):
        big, mid, sml = fonts
        x, y, w, h = self.rect
        # Pane background
        pygame.draw.rect(surf, CFG["FIELD_BG"], self.rect, border_radius=10)
        pygame.draw.rect(surf, CFG["FIELD_BORDER"], self.rect, 2, border_radius=10)

        # Draw grid
        for c in range(CFG["COLS"] + 1):
            X = x + 10 + c * cell
            pygame.draw.line(
                surf,
                CFG["GRID"],
                (X, y + 10),
                (X, y + 10 + CFG["FINISH_HEIGHT"] * cell),
            )
        for r in range(CFG["FINISH_HEIGHT"] + 1):
            Y = y + 10 + r * cell
            pygame.draw.line(
                surf, CFG["GRID"], (x + 10, Y), (x + 10 + CFG["COLS"] * cell, Y)
            )

        # Finish line marker (top of play area)
        Yf = y + 10
        pygame.draw.line(
            surf, (255, 240, 120), (x + 10, Yf), (x + 10 + CFG["COLS"] * cell, Yf), 2
        )

        # Draw static rows
        for left, w_blocks, level, color in self.static_rows:
            rx = x + 10 + left * cell
            ry = y + 10 + (CFG["FINISH_HEIGHT"] - level) * cell
            rect = pygame.Rect(rx, ry, w_blocks * cell, cell)
            pygame.draw.rect(surf, color, rect, border_radius=6)
            pygame.draw.rect(surf, (0, 0, 0), rect, 1, border_radius=6)

        # Draw moving row
        if self.row and not (self.finished or self.crashed):
            r = self.row
            rx = x + 10 + int(round(r.left)) * cell
            ry = y + 10 + (CFG["FINISH_HEIGHT"] - r.level) * cell
            rect = pygame.Rect(rx, ry, r.width * cell, cell)
            pygame.draw.rect(surf, r.color, rect, border_radius=6)
            pygame.draw.rect(surf, (0, 0, 0), rect, 1, border_radius=6)

        # Draw fallers
        for f in self.fallers:
            rx = x + 10 + int(round(f.left)) * cell
            ry = y + 10 + int(round(CFG["FINISH_HEIGHT"] - f.y)) * cell
            rect = pygame.Rect(rx, ry, f.width * cell, cell)
            alpha = int(255 * clamp(f.ttl / CFG["FALL_TIME"], 0, 1))
            fall_surf = pygame.Surface(rect.size, pygame.SRCALPHA)
            fall_surf.fill((*f.color, alpha))
            surf.blit(fall_surf, rect.topleft)
            pygame.draw.rect(surf, (0, 0, 0, alpha), rect, 1)

        if show_hud:
            now = self.level_now()
            label = f"{self.player_name} - Row {now}/{CFG['FINISH_HEIGHT']}"
            t = mid.render(label, True, (230, 235, 245))
            surf.blit(t, (x + 14, y + h - t.get_height() - 10))


class AIPlayer:
    """Times stop presses with configurable variance and small random errors."""

    def __init__(self, window: StackWindow, difficulty_seed=0.5):
        self.win = window
        self.target_left = None
        self.trigger_in = 0.0
        self.brainfart = False
        self.diff_seed = difficulty_seed
        self.last_level_seen = self.win.level_now()

    def _error_range_for_level(self, level):
        lo_e, hi_e = CFG["AI_ERR_EASY"]
        lo_h, hi_h = CFG["AI_ERR_HARD"]
        t = clamp((level - 1) / max(1, CFG["FINISH_HEIGHT"] - 1), 0, 1)
        min_err = int(round(lerp(lo_e, lo_h, t)))
        max_err = int(round(lerp(hi_e, hi_h, t)))
        if max_err < min_err:
            max_err = min_err
        return (min_err, max_err)

    def _pick_target_for_current_row(self):
        r = self.win.row
        if not r:
            return
        last_left, last_w, _, _ = self.win.static_rows[-1]
        ideal_left = last_left + (last_w - r.width) / 2.0

        emin, emax = self._error_range_for_level(r.level)
        off_blocks = random.randint(emin, emax)
        if off_blocks != 0:
            off_blocks *= 1 if random.random() < 0.5 else -1

        target = clamp(ideal_left + off_blocks, 0, self.win.cols - r.width)
        self.target_left = target
        self.trigger_in = random.uniform(CFG["AI_REACT_MIN"], CFG["AI_REACT_MAX"])
        self.brainfart = random.random() < CFG["AI_BRAIN_FART_PCT"]

    def think(self, dt):
        if not self.win.row or self.win.finished or self.win.crashed:
            return False

        r = self.win.row
        if self.last_level_seen != r.level or self.target_left is None:
            self.last_level_seen = r.level
            self._pick_target_for_current_row()

        crossed = False
        tol = CFG["AI_TOLERANCE"]
        if r.dir > 0:
            crossed = r.prev_left <= self.target_left <= r.left + tol
        else:
            crossed = r.prev_left >= self.target_left >= r.left - tol

        if crossed:
            self.trigger_in -= dt
            if self.brainfart:
                self.trigger_in -= dt * random.uniform(0.1, 0.3)
            if self.trigger_in <= 0:
                return True
        return False


# -------------------------
# HUD & Banners (circles instead of Unicode bullets)
# -------------------------
def _draw_pips(
    surface,
    center_x,
    center_y,
    score,
    needed,
    filled_color=(240, 245, 255),
    empty_color=(120, 130, 150),
):
    r = 7
    gap = 18
    total_w = (needed - 1) * gap
    start_x = center_x - total_w // 2
    for i in range(needed):
        cx = start_x + i * gap
        if i < score:
            pygame.draw.circle(surface, filled_color, (cx, center_y), r)
            pygame.draw.circle(surface, (30, 30, 40), (cx, center_y), r, 1)
        else:
            pygame.draw.circle(surface, (0, 0, 0), (cx, center_y), r)  # background fill
            pygame.draw.circle(surface, empty_color, (cx, center_y), r, 2)


def _draw_match_hud(
    screen, fonts, left_name, right_name, left_score, right_score, needed, w, h
):
    big, mid, sml = fonts
    cy = 26
    # Left label + pips
    left_label = mid.render(left_name, True, (230, 235, 245))
    right_label = mid.render(right_name, True, (230, 235, 245))
    left_x = w // 2 - 140
    right_x = w // 2 + 140
    screen.blit(
        left_label, (left_x - left_label.get_width() // 2, cy - left_label.get_height())
    )
    screen.blit(
        right_label,
        (right_x - right_label.get_width() // 2, cy - right_label.get_height()),
    )
    _draw_pips(screen, left_x, cy + 6, left_score, needed)
    _draw_pips(screen, right_x, cy + 6, right_score, needed)


def _draw_result_banner(screen, fonts, w, h, title_text, p_score, n_score, target_wins, p_name="YOU", o_name="Opponent"):
    big, mid, sml = fonts
    overlay = pygame.Surface((w, h), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))
    title = big.render(title_text, True, (255, 240, 160))
    screen.blit(title, title.get_rect(center=(w // 2, h // 2 - 28)))
    # Score pips centered under title
    _draw_pips(screen, w // 2 - 80, h // 2 + 18, p_score, target_wins)
    _draw_pips(screen, w // 2 + 80, h // 2 + 18, n_score, target_wins)
    you = sml.render(p_name, True, (230, 235, 245))
    opp = sml.render(o_name, True, (230, 235, 245))
    screen.blit(you, you.get_rect(center=(w // 2 - 80, h // 2 + 38 + you.get_height())))
    screen.blit(opp, opp.get_rect(center=(w // 2 + 80, h // 2 + 38 + opp.get_height())))


def _ready_countdown(screen, fonts, w, h, secs):
    big, mid, sml = fonts
    n = int(math.ceil(secs))
    t = big.render(str(n), True, (255, 255, 255))
    overlay = pygame.Surface((w, h), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 120))
    screen.blit(overlay, (0, 0))
    screen.blit(t, t.get_rect(center=(w // 2, h // 2)))


def _apply_difficulty(difficulty: str):
    """Mutate working CFG based on difficulty profile."""
    difficulty = (str(difficulty) or "normal").lower()
    global CFG
    CFG = copy.deepcopy(BASE_CFG)

    if difficulty == "easy":
        CFG["AI_ERR_EASY"] = (3, 4)
        CFG["AI_ERR_HARD"] = (1, 2)
        CFG["AI_BRAIN_FART_PCT"] = 0.06
        CFG["AI_TOLERANCE"] = 0.20
    elif difficulty == "hard":
        CFG["AI_ERR_EASY"] = (1, 2)
        CFG["AI_ERR_HARD"] = (0, 1)
        CFG["AI_BRAIN_FART_PCT"] = 0.01
        CFG["AI_TOLERANCE"] = 0.14
    else:
        pass


class BrickDropperScene(Scene):
    """Scene wrapper so the duel plugs into the arena/tournament controller."""

    def __init__(self, manager, context=None, callback=None, difficulty=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.screen = getattr(manager, "screen", None)
        if self.screen is None:
            raise RuntimeError("BrickDropperScene requires an active display surface.")
        self.w, self.h = getattr(manager, "size", self.screen.get_size())

        raw_diff = difficulty
        if raw_diff is None:
            raw_diff = kwargs.get("difficulty", getattr(manager, "difficulty", "normal"))
        self.difficulty = _normalize_difficulty(raw_diff)
        _apply_difficulty(self.difficulty)

        self.font_big, self.font_mid, self.font_small = _get_fonts()
        self.hint_text = self.font_small.render(
            "Space / Enter / Click to stop - Esc to pause", True, (205, 210, 220)
        )

        # Geometry setup
        pad = 16
        gutter = 18
        pane_w = (self.w - (pad * 2) - gutter) // 2
        pane_h = self.h - pad * 2 - 40
        self.left_rect = pygame.Rect(pad, pad + 34, pane_w, pane_h)
        self.right_rect = pygame.Rect(pad + pane_w + gutter, pad + 34, pane_w, pane_h)
        cell_w = (pane_w - 20) // CFG["COLS"]
        cell_h = (pane_h - 20) // CFG["FINISH_HEIGHT"]
        self.cell = max(8, min(cell_w, cell_h))

        # Match + round tracking
        self.target_wins = 2
        self.p_score = 0  # local player's score
        self.n_score = 0  # opponent score
        self.round_idx = 1
        self.rounds_played = 0
        self.last_round_was_tie = False

        # Timers/state
        self.phase = "READY"
        self.ready_timer = CFG["READY_COUNTDOWN"]
        self.banner_timer = 0.0
        self.banner_text = ""
        self.banner_subtitle = ""
        self.pending_outcome = None
        self.pending_payload = {}
        self.forfeited = False
        self._completed = False
        self.net_sync_timer = 0.0
        self.minigame_id = MINIGAME_ID
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
        self.labels = ["You", "Opponent"]

        # Actors
        self.left_win = None
        self.right_win = None
        self.ai = None
        self._start_round(send_net=True)

    # ---- Round helpers ----
    def _start_round(self, send_net: bool = False):
        self.left_win = StackWindow(
            self.left_rect,
            CFG["COLS"],
            CFG["BASE_WIDTH"],
            CFG["FINISH_HEIGHT"],
            self.labels[0],
        )
        self.right_win = StackWindow(
            self.right_rect,
            CFG["COLS"],
            CFG["BASE_WIDTH"],
            CFG["FINISH_HEIGHT"],
            self.labels[1],
        )
        self.ai = None if self.net_enabled else AIPlayer(self.right_win)
        self.phase = "READY"
        self.ready_timer = CFG["READY_COUNTDOWN"]
        self.banner_timer = 0.0
        self.banner_text = ""
        self.banner_subtitle = ""
        if self.net_enabled and send_net:
            self._net_send_state(kind="init", force=True)

    def _complete_round(self, result: str):
        """Apply post-round scoring and queue the cooldown banner."""
        self.rounds_played += 1
        self.last_round_was_tie = result == "tie"
        if result == "player":
            self.p_score += 1
            self.banner_text = f"Round {self.round_idx} — You Win!"
        elif result == "npc":
            self.n_score += 1
            self.banner_text = f"Round {self.round_idx} — You Lose!"
        else:
            self.banner_text = f"Round {self.round_idx} — Tie (Replay)"
        self.banner_timer = CFG["BANNER_TIME"]
        self.banner_subtitle = ""
        self.phase = "COOLDOWN"
        if self.net_enabled:
            self._net_send_state(kind="round_end", force=True)

    def _check_round_end(self):
        left_done = self.left_win.finished or self.left_win.crashed
        right_done = self.right_win.finished or self.right_win.crashed
        if not (left_done or right_done):
            return
        result = None
        if self.left_win.crashed and not self.right_win.crashed:
            result = "npc"
        elif self.right_win.crashed and not self.left_win.crashed:
            result = "player"
        elif self.left_win.crashed and self.right_win.crashed:
            result = "tie"
        elif self.left_win.finished and self.right_win.finished:
            if self.left_win.finish_width > self.right_win.finish_width:
                result = "player"
            elif self.right_win.finish_width > self.left_win.finish_width:
                result = "npc"
            else:
                result = "tie"
        elif self.left_win.finished:
            result = "player"
        elif self.right_win.finished:
            result = "npc"
        if result is None:
            result = "tie"
        self._complete_round(result)

    # ---- Networking helpers ----
    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[BrickDropper] Failed to send action: {exc}")

    def _net_send_state(self, kind: str = "state", force: bool = False):
        if not self.net_enabled:
            return
        state = {
            "kind": kind,
            "from": self.local_id,
            "p_score": self.p_score,
            "n_score": self.n_score,
            "round_idx": self.round_idx,
            "rounds_played": self.rounds_played,
            "target_wins": self.target_wins,
            "phase": self.phase,
            "ready_timer": self.ready_timer,
            "banner_timer": self.banner_timer,
            "banner_text": self.banner_text,
            "banner_subtitle": self.banner_subtitle,
            "pending_outcome": self.pending_outcome,
            "last_round_was_tie": self.last_round_was_tie,
            "forfeited": self.forfeited,
            "board": self.left_win.pack_state(),
        }
        if self.pending_payload:
            state["payload"] = self.pending_payload
        if kind == "finish" and self.pending_outcome:
            state["outcome"] = self.pending_outcome
        self._net_send_action(state)

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

    def _net_apply_state(self, action: Dict[str, Any]):
        if not action:
            return
        board_state = action.get("board")
        if board_state:
            self.right_win.apply_state(board_state)
        # Sync opponent score from their local perspective.
        try:
            self.n_score = max(self.n_score, int(action.get("p_score", self.n_score)))
        except Exception:
            pass
        self.round_idx = max(self.round_idx, int(action.get("round_idx", self.round_idx)))
        self.rounds_played = max(self.rounds_played, int(action.get("rounds_played", self.rounds_played)))
        if action.get("phase") == "MATCH_END" and action.get("pending_outcome"):
            # Remote already ended; mirror outcome from our perspective.
            outcome_remote = action.get("pending_outcome")
            if outcome_remote == "win":
                mapped = "lose"
            elif outcome_remote == "lose":
                mapped = "win"
            else:
                mapped = outcome_remote
            subtitle = action.get("banner_subtitle", "")
            self.pending_payload = action.get("payload", {}) or {}
            self._set_match_result("npc", subtitle=subtitle, forced_outcome=mapped)
        # If both boards are done after applying remote state, resolve round locally.
        if self.phase == "PLAY":
            self._check_round_end()

    # ---- State transitions ----
    def _set_match_result(self, winner: str, subtitle: str = "", forced_outcome=None):
        if self.pending_outcome:
            return
        if winner == "player":
            outcome = forced_outcome or "win"
            title = "Victory!"
            if not subtitle:
                subtitle = "You out-stacked the convoy"
        elif winner == "npc":
            outcome = forced_outcome or "lose"
            title = "Defeat"
            if not subtitle:
                subtitle = "Opponent reached the summit"
        else:
            outcome = forced_outcome or "forfeit"
            title = "Match Forfeit"
            if not subtitle:
                subtitle = "You left the duel"
        self.pending_outcome = outcome
        self.banner_text = title
        self.banner_subtitle = subtitle
        self.banner_timer = CFG["BANNER_TIME"]
        self.phase = "MATCH_END"
        if not self.pending_payload:
            self._capture_payload(outcome)
        if self.net_enabled:
            self._net_send_state(kind="finish", force=True)

    def _capture_payload(self, outcome):
        payload = {
            "outcome": outcome,
            "player_score": self.p_score,
            "npc_score": self.n_score,
            "rounds_played": self.rounds_played,
            "difficulty": self.difficulty,
            "forfeit": self.forfeited,
        }
        if self.duel_id:
            payload["duel_id"] = self.duel_id
        self.pending_payload = payload

    # ---- Scene API ----
    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner_timer = 0
                self._finalize(self.pending_outcome)
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._pause_game()
                return
            if self.phase == "PLAY" and event.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.left_win.lock()
                if self.net_enabled:
                    self._net_send_state(kind="lock")
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and self.phase == "PLAY":
                self.left_win.lock()
                if self.net_enabled:
                    self._net_send_state(kind="lock")

    def update(self, dt):
        self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner_timer > 0:
                self.banner_timer -= dt
                if self.banner_timer <= 0:
                    self._finalize(self.pending_outcome)
            else:
                self._finalize(self.pending_outcome)
            return

        if self.phase == "READY":
            self.ready_timer -= dt
            if self.ready_timer <= 0:
                self.phase = "PLAY"
                if self.net_enabled:
                    self._net_send_state(kind="play", force=True)
        elif self.phase == "PLAY":
            if self.net_enabled:
                self.net_sync_timer += dt
                if self.net_sync_timer >= 0.12:
                    self.net_sync_timer = 0.0
                    self._net_send_state(kind="state")
            if self.ai and self.ai.think(dt):
                self.right_win.lock()
                if self.net_enabled:
                    self._net_send_state(kind="lock")
            self.left_win.update(dt)
            self.right_win.update(dt)
            self._check_round_end()
        elif self.phase == "COOLDOWN":
            self.banner_timer -= dt
            if self.banner_timer <= 0:
                if self.p_score >= self.target_wins or self.n_score >= self.target_wins:
                    winner = "player" if self.p_score > self.n_score else "npc"
                    self._set_match_result(winner)
                else:
                    if not self.last_round_was_tie:
                        self.round_idx += 1
                    self._start_round(send_net=self.net_enabled)
        # MATCH_END is handled by pending_outcome timer

    def draw(self):
        screen = self.screen
        screen.fill(CFG["BG"])
        fonts = (self.font_big, self.font_mid, self.font_small)
        left_label = "YOU"
        right_label = "Opponent" if self.net_enabled else "NPC"
        _draw_match_hud(
            screen, fonts, left_label, right_label, self.p_score, self.n_score, self.target_wins, self.w, self.h
        )
        self.left_win.draw(screen, self.cell, fonts, is_player=True)
        self.right_win.draw(screen, self.cell, fonts, is_player=False)

        if self.phase == "READY":
            _ready_countdown(screen, fonts, self.w, self.h, self.ready_timer)
        if self.phase in ("COOLDOWN", "MATCH_END") or self.pending_outcome:
            subtitle = getattr(self, "banner_subtitle", "")
            _draw_result_banner(
                screen,
                fonts,
                self.w,
                self.h,
                self.banner_text,
                self.p_score,
                self.n_score,
                self.target_wins,
                left_label,
                right_label,
            )
            if subtitle:
                sub_font = self.font_small
                surf = sub_font.render(subtitle, True, (240, 240, 240))
                screen.blit(surf, surf.get_rect(center=(self.w // 2, self.h // 2 + 80)))

        screen.blit(
            self.hint_text, (self.w // 2 - self.hint_text.get_width() // 2, self.h - 20)
        )

    # ---- Pause / finalize helpers ----
    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[BrickDropper] Pause menu unavailable: {exc}")
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
                winner = self.local_id
                loser = self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner = self.remote_id
                loser = self.local_id
            if winner:
                self.context.last_result["winner"] = winner
            if loser:
                self.context.last_result["loser"] = loser
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[BrickDropper] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[BrickDropper] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self.forfeited = True
        self.pending_payload = {
            "reason": "forfeit",
            "difficulty": self.difficulty,
            "player_score": self.p_score,
            "npc_score": self.n_score,
            "rounds_played": self.rounds_played,
            "forfeit": True,
        }
        self._set_match_result("npc", subtitle="Forfeit accepted", forced_outcome="forfeit")


def launch(manager, context=None, callback=None, **kwargs):
    return BrickDropperScene(manager, context, callback, **kwargs)
