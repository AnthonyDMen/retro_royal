# minigames/target_sprint/game.py
import math
import random
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable, List, Optional, Tuple, Dict

import pygame
from scene_manager import Scene
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

TITLE = "Target Sprint"
MINIGAME_ID = "target_sprint"
MULTIPLAYER_ENABLED = True

# =========================
# CONFIG (tunable constants)
# =========================
CONFIG = {
    "FPS_LOGIC": 60,  # fixed-step logic
    "BG_COLOR": (14, 14, 17),
    "LANE_COUNT": 6,
    "SAFE_MARGIN": 24,
    "WAVE_TIME": 15.0,
    "WAVE_BREAK": 3.0,
    # Radii (px)
    "RADII": {"L": 24, "M": 18, "S": 12, "GOLD": 16},
    # Scores
    "SCORES": {"L": 50, "M": 100, "S": 150, "GOLD": 250},
    # Time bonuses (seconds)
    "BONUS": {"GOLD_TIME": 1.5, "STREAK_EVERY": 5, "STREAK_TIME": 0.5},
    # Ammo / reload
    "MAG_SIZE": 8,
    "RESERVE_PER_WAVE": 40,
    "RELOAD_TIME": 0.70,
    # Waves
    "WAVE_COUNT": 3,
    "START_TIME": 10.0,  # legacy (kept for compatibility; overridden by WAVE_TIME)
    "TIME_ADD_PER_WAVE": 0.0,  # unused with fixed wave timer
    "QUOTAS": {1: 500, 2: 850, 3: 1250},
    # Spawn cadences (base seconds between spawns) and speed ranges (px/s)
    "WAVE_SPAWN": {
        1: {"cadence": (0.50, 0.70), "speed": (120, 160), "mix": {"L": 0.7, "M": 0.3, "S": 0.0},
            "paths": {"straight": 1.0, "diag": 0.0, "zig": 0.0}},
        2: {"cadence": (0.45, 0.60), "speed": (170, 220), "mix": {"L": 0.1, "M": 0.65, "S": 0.25},
            "paths": {"straight": 0.7, "diag": 0.2, "zig": 0.1}},
        3: {"cadence": (0.35, 0.50), "speed": (230, 300), "mix": {"L": 0.05, "M": 0.25, "S": 0.70},
            "paths": {"straight": 0.1, "diag": 0.6, "zig": 0.3}},
    },
    # Golden chance constraints
    "GOLDEN_PER_WAVE_MAX": 1,
    "GOLDEN_AS_SIZE": "M",  # treated as medium radius for hitbox
    # Zig/sine params
    "ZIG_A": (18, 28),       # amplitude range (px)
    "ZIG_W": (3.0, 5.0),     # angular speed range (rad/s)
    # Post result banner time
    "POST_RESULT_TIME": 3.5,     # how long to show WIN/LOSE before auto-pop (click to skip)
    "START_BANNER_TIME": 0.9,    # "WAVE 1" intro banner
    "INTER_WAVE_BREAK": 1.0,     # pause between waves (banner shown; timer paused)
    # --- NEW ---
    "SPAWN_OFFSET": 40,          # how far off-screen we spawn targets
    "OFFSCREEN_MARGIN": 60,      # how far beyond edges we cull targets
}

# ============
# Beep stubs
# ============
def beep_fire(): pass
def beep_hit(): pass
def beep_reload_start(): pass
def beep_reload_done(): pass

# ==================
# Deterministic RNG
# ==================
class TargetRNG:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
    def choice_w(self, pairs: List[Tuple[str, float]]) -> str:
        total = sum(w for _, w in pairs)
        roll = self.r.random() * total
        upto = 0.0
        for item, w in pairs:
            upto += w
            if roll <= upto:
                return item
        return pairs[-1][0]
    def uniform(self, a, b): return self.r.uniform(a, b)
    def randint(self, a, b): return self.r.randint(a, b)
    def random(self): return self.r.random()

# ===============
# Data structures
# ===============
@dataclass
class SpawnSpec:
    size_key: str  # "L", "M", "S"
    path: str      # "straight" | "diag" | "zig"
    lane: int
    speed: float
    golden: bool = False
    zigA: float = 0.0
    zigW: float = 0.0
    zigPhi: float = 0.0
    dir_sign: int = 1  # for diag: up/down

@dataclass
class SpawnEvent:
    t_spawn: float
    spec: SpawnSpec

@dataclass
class Target:
    tid: int
    size_key: str
    value: int
    pos: pygame.Vector2
    vel: pygame.Vector2
    radius: float
    path: str
    params: dict
    spawn_time: float
    golden: bool = False
    alive: bool = True
    def update(self, dt: float, bounds: pygame.Rect, t_now: float):
        if not self.alive:
            return
        # Always move by current velocity
        self.pos += self.vel * dt

        if self.path == "zig":
            # sinusoidal vertical offset around lane center
            A = self.params["A"]; w = self.params["w"]; phi = self.params["phi"]
            lane_y = self.params["lane_y"]; t0 = self.spawn_time
            self.pos.y = lane_y + A * math.sin(w * (t_now - t0) + phi)

        # Kill if off-screen (use generous margin)
        m = CONFIG["OFFSCREEN_MARGIN"]
        if (self.pos.x < -m or self.pos.x > bounds.width + m or
            self.pos.y < -m or self.pos.y > bounds.height + m):
            self.alive = False

@dataclass
class Spark:
    pos: Tuple[float, float]
    t0: float
    life: float = 0.18

@dataclass
class GameState:
    wave: int = 1
    time_left: float = CONFIG["WAVE_TIME"]
    score: int = 0
    quota_accum: int = 0  # legacy; unused for wave progression
    mag: int = CONFIG["MAG_SIZE"]
    reserve: int = CONFIG["RESERVE_PER_WAVE"]
    reloading: bool = False
    reload_t0: float = 0.0
    streak: int = 0
    shots_fired: int = 0
    hits: int = 0
    banner_text: Optional[str] = None
    banner_t0: float = 0.0
    ended: bool = False
    result: Optional[str] = None  # "win" | "lose" | None
    golden_spawned_this_wave: int = 0

# ==================
# Spawn plan builder
# ==================
def _weighted_choice(rng: TargetRNG, weight_map: dict) -> str:
    return rng.choice_w(list(weight_map.items()))

def lane_y_from_index(idx: int, bounds: pygame.Rect) -> float:
    top = CONFIG["SAFE_MARGIN"]
    bottom = bounds.height - CONFIG["SAFE_MARGIN"]
    if CONFIG["LANE_COUNT"] <= 1:
        return bounds.centery
    frac = idx / (CONFIG["LANE_COUNT"] - 1)
    return top + frac * (bottom - top)

def make_spawn_plan(wave_id: int, seed: int, bounds: pygame.Rect) -> Iterable[SpawnEvent]:
    cfg = CONFIG["WAVE_SPAWN"][wave_id]
    rng = TargetRNG(seed + wave_id * 9176 + 1337)
    cadence_min, cadence_max = cfg["cadence"]
    speed_min, speed_max = cfg["speed"]
    mix = cfg["mix"]; paths = cfg["paths"]

    plan: List[SpawnEvent] = []
    t = 0.0
    golden_used = False

    for _ in range(220):  # plenty of targets for any sane wave length
        cadence = rng.uniform(cadence_min, cadence_max); t += cadence
        size_key = _weighted_choice(rng, mix)
        is_golden = False
        if not golden_used and rng.random() < 0.07:
            is_golden = True; golden_used = True
        path_key = _weighted_choice(rng, paths)
        lane = rng.randint(0, CONFIG["LANE_COUNT"] - 1)
        speed = rng.uniform(speed_min, speed_max)
        if path_key == "straight":
            spec = SpawnSpec(size_key=size_key, path="straight", lane=lane, speed=speed, golden=is_golden)
        elif path_key == "diag":
            dir_sign = 1 if rng.random() < 0.5 else -1
            spec = SpawnSpec(size_key=size_key, path="diag", lane=lane, speed=speed, golden=is_golden, dir_sign=dir_sign)
        else:
            A = rng.uniform(*CONFIG["ZIG_A"]); w = rng.uniform(*CONFIG["ZIG_W"]); phi = rng.uniform(0, math.tau)
            spec = SpawnSpec(size_key=size_key, path="zig", lane=lane, speed=speed, golden=is_golden, zigA=A, zigW=w, zigPhi=phi)
        plan.append(SpawnEvent(t_spawn=t, spec=spec))
    return plan

# =========
# Spawner
# =========
class Spawner:
    def __init__(self, plan: List[SpawnEvent]):
        self.plan = sorted(plan, key=lambda e: e.t_spawn)
        self.i = 0
        self.next_id = 1
    def emit_until(self, t_wave: float, bounds: pygame.Rect) -> List[Target]:
        emitted: List[Target] = []
        while self.i < len(self.plan) and self.plan[self.i].t_spawn <= t_wave:
            ev = self.plan[self.i]; self.i += 1
            spec = ev.spec
            lane_y = lane_y_from_index(spec.lane, bounds)
            speed = spec.speed
            direction = -1 if (self.i % 2 == 0) else 1  # deterministic
            m = CONFIG["SPAWN_OFFSET"]
            if spec.path == "straight":
                x0 = -m if direction == 1 else bounds.width + m
                y0 = lane_y
                vx, vy = direction * speed, 0.0
                params = {"lane_y": lane_y}
                base_v = pygame.Vector2(vx, vy)
            elif spec.path == "diag":
                x0 = -m if direction == 1 else bounds.width + m
                y0 = lane_y
                vx = direction * speed * 0.9; vy = spec.dir_sign * speed * 0.35
                params = {"lane_y": lane_y}
                base_v = pygame.Vector2(vx, vy)
            else:
                x0 = -m if direction == 1 else bounds.width + m
                y0 = lane_y
                vx = direction * speed * 0.9
                params = {"lane_y": lane_y, "A": spec.zigA, "w": spec.zigW, "phi": spec.zigPhi}
                base_v = pygame.Vector2(vx, 0.0)

            # Golden enforcement
            size_key = "GOLD" if spec.golden else spec.size_key
            radius = CONFIG["RADII"]["GOLD"] if spec.golden else CONFIG["RADII"][spec.size_key]
            value = CONFIG["SCORES"][size_key]

            t = Target(
                tid=self.next_id, size_key=size_key, value=value,
                pos=pygame.Vector2(x0, y0), vel=base_v, radius=radius,
                path=spec.path, params=params, spawn_time=0.0, golden=spec.golden
            )
            self.next_id += 1
            emitted.append(t)
        return emitted

# ============
# Crosshair & Input queue
# ============
class Crosshair:
    def __init__(self):
        self.pos = pygame.Vector2(0, 0)
        self.shoot_requests: List[Tuple[float, Tuple[float, float]]] = []
        self.reload_requests: List[float] = []
    def enqueue_shot(self, t: float, pos: Tuple[float, float]): self.shoot_requests.append((t, pos))
    def enqueue_reload(self, t: float): self.reload_requests.append(t)
    def pop_shots(self): out = self.shoot_requests; self.shoot_requests = []; return out
    def pop_reloads(self): out = self.reload_requests; self.reload_requests = []; return out

# =================
# Scene wrapper
# =================
class TargetSprintScene(Scene):
    def __init__(self, manager, context, callback, difficulty: float = 1.0, seed: int = 12345, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.difficulty = float(difficulty)
        self.flags = getattr(self.context, "flags", {}) if self.context else {}
        self.seed = int(kwargs.get("seed", self.flags.get("seed", seed)))

        # Multiplayer plumbing
        self.duel_id = kwargs.get("duel_id") or self.flags.get("duel_id")
        self.participants = kwargs.get("participants") or self.flags.get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or self.flags.get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or self.flags.get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 - self.local_idx
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.is_authority = not self.net_enabled or self.local_idx == 0
        seed_src = self.duel_id or self.seed
        try:
            self.seed = int(seed_src, 16)
        except Exception:
            try:
                self.seed = int(seed_src)
            except Exception:
                self.seed = int(seed)
        self.net_interval = 1.0 / 30.0
        self.net_last = 0.0

        self.screen = manager.screen
        self.w, self.h = manager.size
        self.bounds = self.screen.get_rect()
        self.minigame_id = "target_sprint"
        self.banner = EndBanner(
            duration=CONFIG["POST_RESULT_TIME"] + 3.0,
            titles={
                "win": "Target Sprint Cleared!",
                "lose": "Target Sprint Failed",
                "forfeit": "Target Sprint Forfeit",
            },
        )
        self.pending_outcome: Optional[str] = None
        self.pending_payload: dict = {}
        self._completed = False
        pygame.font.init()
        self.banner_font_big = pygame.font.SysFont(None, 48)
        self.banner_font_small = pygame.font.SysFont(None, 28)

        # Renderer (external file; fallback if missing)
        self.renderer = self._get_renderer(self.screen)
        self.banner_font_big = getattr(self.renderer, "big", self.banner_font_big)
        self.banner_font_small = getattr(self.renderer, "font", self.banner_font_small)

        # Fixed-step setup
        self.dt_fixed = 1.0 / CONFIG["FPS_LOGIC"]
        self.accumulator = 0.0

        # Sim state
        self.gs = GameState()
        self.cross = Crosshair()
        self.targets: List[Target] = []
        self.sparks: List[Spark] = []
        self.t_wave = 0.0
        self.t_sim = 0.0
        self.scores = [0, 0]
        self.wave_start_score = 0
        self.pending_actions: List[Dict] = []

        self.streak_badge_text = None
        self.streak_badge_t0 = 0.0
        self.streak_badge_life = 0.6

        self.paused = False
        self.pause_until = 0.0

        # Show Wave 1 intro banner and pause briefly
        self._banner("WAVE 1", pause=CONFIG["START_BANNER_TIME"])

        # Spawner for wave 1
        self.plan = make_spawn_plan(1, self.seed, self.bounds)
        self.spawner = Spawner(self.plan)
        self.gs.time_left = CONFIG["WAVE_TIME"]

        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ---- engine hooks ----
    def handle_event(self, event):
        # Allow mouse move always
        if event.type == pygame.MOUSEMOTION:
            self.cross.pos.update(*event.pos)

        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.paused:
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.cross.enqueue_shot(self.t_wave, event.pos); self.cross.pos.update(*event.pos)
            if self.net_enabled and not self.is_authority:
                self._net_send_action({"kind": "shot", "pos": event.pos, "t": self.t_wave})
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                self.cross.enqueue_reload(self.t_wave)
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "reload", "t": self.t_wave})

    def update(self, dt):
        # normalize dt (runner might pass ms)
        if dt > 1.0:
            dt = dt / 1000.0

        if self.net_enabled:
            self._net_poll_actions(float(dt))

        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        # accumulate for fixed-step sim
        self.accumulator += dt

        while self.accumulator >= self.dt_fixed and not self.gs.ended:
            self.accumulator -= self.dt_fixed

            # If a banner/break is active, advance only absolute time;
            # DO NOT advance wave-time or timer; no spawns/logic during pause.
            if self.paused and self.t_sim < self.pause_until:
                self.t_sim += self.dt_fixed
                # keep banner visible; nothing else
                continue
            elif self.paused and self.t_sim >= self.pause_until:
                self.paused = False  # resume play

            self._step_sim(self.dt_fixed)

        if self.is_authority and self.net_enabled and not self.gs.ended:
            self._net_send_state()

    def draw(self):
        self.renderer.draw_background()

        # streak badge expiry
        if self.streak_badge_text and (self.t_sim - self.streak_badge_t0) > self.streak_badge_life:
            self.streak_badge_text = None

        reload_frac = 0.0
        if self.gs.reloading:
            reload_frac = min(1.0, max(0.0, (self.t_wave - self.gs.reload_t0) / CONFIG["RELOAD_TIME"]))

        # Decide if the banner should be visible this frame
        banner_visible = False
        if self.gs.banner_text:
            if self.paused and (self.t_sim < self.pause_until):
                banner_visible = True  # keep on-screen for the whole break
            elif (self.t_sim - self.gs.banner_t0) < 0.9:
                banner_visible = True  # short flash banners

        gs_view = SimpleNamespace(
            time_left=self.gs.time_left,
            score=self.gs.score,
            wave=self.gs.wave,
            mag=self.gs.mag,
            reserve=self.gs.reserve,
            is_reloading=self.gs.reloading,
            reload_frac=reload_frac,
            streak=self.gs.streak,
            banner_text=self.gs.banner_text if banner_visible else None,
            streak_badge=self.streak_badge_text,
        )
        local_score = self.scores[self.local_idx]
        wave_score = local_score - self.wave_start_score
        target = CONFIG["QUOTAS"].get(self.gs.wave, 0)
        quota_text = f"Wave {self.gs.wave}/{CONFIG['WAVE_COUNT']} • Score {wave_score}/{target} • {self.gs.time_left:0.1f}s left"
        acc = None
        if self.gs.shots_fired > 0:
            pct = (self.gs.hits / self.gs.shots_fired) * 100.0
            acc = (self.gs.shots_fired, self.gs.hits, pct)

        # Scoreboard (local vs opponent)
        if self.net_enabled:
            local_score = self.scores[self.local_idx]
            opp_score = self.scores[self.remote_idx] if len(self.scores) > self.remote_idx else 0
            score_text = f"You: {local_score:,}   Opp: {opp_score:,}"
            tscore = self.banner_font_small.render(score_text, True, (235, 235, 240))
            self.screen.blit(tscore, (12, 36))

        # targets & sparks
        self.renderer.draw_targets([t for t in self.targets if t.alive], self.t_sim)
        self.renderer.draw_sparks(self.sparks, self.t_sim)
        self.renderer.draw_hud(gs_view, (self.gs.wave, CONFIG["WAVE_COUNT"], self.gs.quota_accum, CONFIG["QUOTAS"][self.gs.wave]), acc, quota_text)
        self.renderer.draw_crosshair(self.cross.pos)
        if self.pending_outcome:
            self.banner.draw(self.screen, self.banner_font_big, self.banner_font_small, (self.w, self.h))

    # ---- internals ----
    def _step_sim(self, dt_fixed: float):
        self.t_sim += dt_fixed
        self.t_wave += dt_fixed

        # Non-authority: keep sim time progressing for visuals, but rely on host decisions.
        # We still advance timers/spawns so target IDs line up with host.

        # spawn
        for t in self.spawner.emit_until(self.t_wave, self.bounds):
            t.spawn_time = self.t_wave
            if t.golden:
                if self.gs.golden_spawned_this_wave < CONFIG["GOLDEN_PER_WAVE_MAX"]:
                    self.gs.golden_spawned_this_wave += 1
                else:
                    # demote extra golden to medium
                    t.golden = False; t.size_key = "M"
                    t.radius = CONFIG["RADII"]["M"]; t.value = CONFIG["SCORES"]["M"]
            self.targets.append(t)

        # reload requests
        for _t in self.cross.pop_reloads():
            if (not self.gs.reloading) and self.gs.mag < CONFIG["MAG_SIZE"] and self.gs.reserve > 0:
                self.gs.reloading = True; self.gs.reload_t0 = self.t_wave; beep_reload_start()

        # reload progress
        if self.gs.reloading:
            if (self.t_wave - self.gs.reload_t0) >= CONFIG["RELOAD_TIME"]:
                need = CONFIG["MAG_SIZE"] - self.gs.mag
                take = min(need, self.gs.reserve)
                self.gs.mag += take; self.gs.reserve -= take
                self.gs.reloading = False; beep_reload_done()

        # shots
        for t_fire, mpos in self.cross.pop_shots():
            if self.net_enabled and not self.is_authority:
                # Non-host players fire: consume ammo client-side for feel; host will reconcile.
                if self.gs.reloading or self.gs.mag <= 0:
                    continue
                self.gs.mag = max(0, self.gs.mag - 1)
                self.gs.shots_fired += 1
                beep_fire()
                continue

            if self.gs.reloading or self.gs.mag <= 0:
                continue
            self.gs.mag -= 1; self.gs.shots_fired += 1; beep_fire()
            self._handle_shot(self.local_idx, mpos)
            # auto-reload on empty mag
            if self.gs.mag == 0 and self.gs.reserve > 0 and not self.gs.reloading:
                self.gs.reloading = True; self.gs.reload_t0 = self.t_wave; beep_reload_start()

        # update targets
        for tgt in self.targets:
            tgt.update(dt_fixed, self.bounds, self.t_wave)

        # cull sparks
        self.sparks = [s for s in self.sparks if (self.t_sim - s.t0) < s.life]

        # timer
        if self.is_authority or not self.net_enabled:
            self.gs.time_left -= dt_fixed
            if self.gs.time_left <= 0:
                self._finish_wave()

    def _banner(self, text: str, pause: float = 0.0):
        self.gs.banner_text = text
        self.gs.banner_t0 = self.t_sim
        if pause > 0.0:
            self.paused = True
            self.pause_until = self.t_sim + float(pause)

    def _end(self, result: str, banner: str):
        if self.pending_outcome:
            return
        self.gs.ended = True
        self.gs.result = result
        self.pending_outcome = result
        payload = {
            "wave": self.gs.wave,
            "scores": self.scores,
            "quota_accum": self.gs.quota_accum,
            "time_left": round(self.gs.time_left, 2),
            "winner": None,
            "loser": None,
        }
        if self.net_enabled:
            if self.scores[self.local_idx] > self.scores[self.remote_idx]:
                payload["winner"] = self.local_id
                payload["loser"] = self.remote_id
            elif self.scores[self.remote_idx] > self.scores[self.local_idx]:
                payload["winner"] = self.remote_id
                payload["loser"] = self.local_id
            # tie -> no winner, rematch handled upstream
        self.pending_payload = payload
        if self.net_enabled and self.is_authority:
            outcome = "win" if payload["winner"] == self.local_id else "lose"
            self._net_send_action({"kind": "finish", "winner": payload["winner"], "loser": payload["loser"], "outcome": outcome})
        self.banner.show(result, subtitle=banner)

    def _finish_wave(self):
        # Clamp timer and advance or finish based on wave count.
        self.gs.time_left = 0.0
        local_score_total = self.scores[self.local_idx]
        wave_score = local_score_total - self.wave_start_score
        target = CONFIG["QUOTAS"].get(self.gs.wave, 0)

        if not self.net_enabled:
            # Solo: fail immediately if quota not reached for this wave.
            if wave_score < target:
                self._end("lose", f"Wave {self.gs.wave} quota {wave_score}/{target}")
                return

        if self.gs.wave < CONFIG["WAVE_COUNT"]:
            # Show quick intermission banner with scores.
            opp_score = self.scores[self.remote_idx] if len(self.scores) > self.remote_idx else 0
            if self.net_enabled:
                self._banner(f"Wave {self.gs.wave} done • You {local_score_total} | Opp {opp_score}", pause=CONFIG["WAVE_BREAK"])
            else:
                self._banner(f"Wave {self.gs.wave} cleared • Score {wave_score}/{target}", pause=CONFIG["WAVE_BREAK"])
            self.gs.wave += 1
            self.gs.time_left = CONFIG["WAVE_TIME"]
            self.gs.golden_spawned_this_wave = 0
            self.gs.mag = CONFIG["MAG_SIZE"]; self.gs.reserve = CONFIG["RESERVE_PER_WAVE"]
            self.gs.reloading = False; self.gs.reload_t0 = 0.0
            self.t_wave = 0.0
            self.plan = make_spawn_plan(self.gs.wave, self.seed, self.bounds)
            self.spawner = Spawner(self.plan)
            self.targets = []
            self.wave_start_score = self.scores[self.local_idx]
            if self.net_enabled and self.is_authority:
                self._net_send_state(force=True)
        else:
            if self.net_enabled:
                # Decide winner by score for multiplayer
                if self.scores[self.local_idx] > self.scores[self.remote_idx]:
                    outcome = "win"
                elif self.scores[self.remote_idx] > self.scores[self.local_idx]:
                    outcome = "lose"
                else:
                    outcome = "lose"
                banner = "WIN!" if outcome == "win" else "Score Battle"
                self._end(outcome, banner)
            else:
                # Solo: all quotas met.
                self._end("win", "All waves cleared")

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[TargetSprint] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome: str):
        if self._completed:
            return
        self._completed = True
        self.pending_outcome = None
        if self.context is None:
            self.context = GameContext()
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[TargetSprint] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[TargetSprint] Callback error: {exc}")

    # ---- gameplay helpers ----
    def _handle_shot(self, side_idx: int, mpos: Tuple[float, float]):
        mx, my = mpos
        hit_target = None
        best_value = -1
        for tgt in self.targets:
            if not tgt.alive:
                continue
            dx = tgt.pos.x - mx
            dy = tgt.pos.y - my
            if dx * dx + dy * dy <= (tgt.radius * tgt.radius):
                if tgt.value > best_value:
                    best_value = tgt.value
                    hit_target = tgt
        if hit_target:
            hit_target.alive = False
            self.gs.hits += 1
            self.scores[side_idx] += hit_target.value
            self.gs.score = self.scores[self.local_idx]
            self.gs.quota_accum += hit_target.value
            self.gs.streak += 1
            beep_hit()
            if self.gs.streak % CONFIG["BONUS"]["STREAK_EVERY"] == 0:
                self.streak_badge_text = f"×{self.gs.streak}!"
                self.streak_badge_t0 = self.t_sim
            self.sparks.append(Spark(pos=(mx, my), t0=self.t_sim))
            if self.net_enabled and self.is_authority:
                self._net_send_action(
                    {
                        "kind": "hit",
                        "tid": hit_target.tid,
                        "killer": side_idx,
                        "score_add": hit_target.value,
                        "scores": self.scores,
                        "quota": self.gs.quota_accum,
                        "time_left": self.gs.time_left,
                        "wave": self.gs.wave,
                        "t_wave": self.t_wave,
                        "t_sim": self.t_sim,
                        "streak": self.gs.streak,
                        "wave_plan_idx": self.spawner.i,
                        "next_tid": self.spawner.next_id,
                    }
                )
                self._net_send_state(force=True)
        else:
            self.gs.streak = 0

    # ---- networking ----
    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[TargetSprint] send failed: {exc}")

    def _pack_state(self):
        return {
            "wave": self.gs.wave,
            "quota": self.gs.quota_accum,
            "time_left": self.gs.time_left,
            "mag": self.gs.mag,
            "reserve": self.gs.reserve,
            "reloading": self.gs.reloading,
            "reload_t0": self.gs.reload_t0,
            "scores": self.scores,
            "t_wave": self.t_wave,
            "t_sim": self.t_sim,
            "streak": self.gs.streak,
            "banner": self.gs.banner_text,
            "banner_t0": self.gs.banner_t0,
            "paused": self.paused,
            "pause_until": self.pause_until,
            "wave_plan_idx": self.spawner.i,
            "next_tid": self.spawner.next_id,
        }

    def _apply_state(self, st: dict):
        if not st:
            return
        new_wave = int(st.get("wave", self.gs.wave))
        if new_wave != self.gs.wave:
            self.gs.wave = new_wave
            self.plan = make_spawn_plan(self.gs.wave, self.seed, self.bounds)
            self.spawner = Spawner(self.plan)
            self.targets = []
        self.gs.quota_accum = int(st.get("quota", self.gs.quota_accum))
        try:
            self.gs.time_left = float(st.get("time_left", self.gs.time_left))
        except Exception:
            pass
        self.gs.mag = int(st.get("mag", self.gs.mag))
        self.gs.reserve = int(st.get("reserve", self.gs.reserve))
        self.gs.reloading = bool(st.get("reloading", self.gs.reloading))
        self.gs.reload_t0 = float(st.get("reload_t0", self.gs.reload_t0))
        self.scores = list(st.get("scores", self.scores))
        if self.scores and len(self.scores) > self.local_idx:
            self.gs.score = self.scores[self.local_idx]
        self.t_wave = float(st.get("t_wave", self.t_wave))
        self.t_sim = float(st.get("t_sim", self.t_sim))
        self.gs.streak = int(st.get("streak", self.gs.streak))
        self.gs.banner_text = st.get("banner", self.gs.banner_text)
        self.gs.banner_t0 = float(st.get("banner_t0", self.gs.banner_t0))
        self.paused = bool(st.get("paused", self.paused))
        self.pause_until = float(st.get("pause_until", self.pause_until))
        self.spawner.i = int(st.get("wave_plan_idx", self.spawner.i))
        try:
            self.spawner.next_id = int(st.get("next_tid", self.spawner.next_id))
        except Exception:
            pass

    def _net_send_state(self, force=False):
        if not self.net_enabled or not self.is_authority:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
        self._net_send_action({"kind": "state", "state": self._pack_state()})

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            if msg.get("from") == self.local_id:
                continue
            self._apply_remote_action(msg.get("action") or {})

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "shot" and self.is_authority:
            pos = action.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                self._handle_shot(self.remote_idx, tuple(pos))
            return
        if kind == "reload" and self.is_authority:
            # remote reload just mirrors local logic; no extra handling needed
            return
        if kind == "hit":
            tid = action.get("tid")
            killer = int(action.get("killer", self.remote_idx))
            self.scores = list(action.get("scores", self.scores))
            self.gs.quota_accum = int(action.get("quota", self.gs.quota_accum))
            try:
                self.gs.time_left = float(action.get("time_left", self.gs.time_left))
            except Exception:
                pass
            self.gs.streak = int(action.get("streak", self.gs.streak))
            if self.scores and len(self.scores) > self.local_idx:
                self.gs.score = self.scores[self.local_idx]
            try:
                self.t_wave = float(action.get("t_wave", self.t_wave))
                self.t_sim = float(action.get("t_sim", self.t_sim))
            except Exception:
                pass
            if tid:
                found = False
                for tgt in self.targets:
                    if tgt.tid == tid:
                        tgt.alive = False
                        found = True
                        break
                if not found:
                    # Rebuild target stream to host's position, then apply removal.
                    act_wave = int(action.get("wave", self.gs.wave))
                    if act_wave != self.gs.wave:
                        self.gs.wave = act_wave
                        self.plan = make_spawn_plan(self.gs.wave, self.seed, self.bounds)
                        self.spawner = Spawner(self.plan)
                    wp_idx = int(action.get("wave_plan_idx", self.spawner.i))
                    next_tid = int(action.get("next_tid", self.spawner.next_id or 1))
                    self.spawner.i = wp_idx
                    self.spawner.next_id = next_tid
                    self.targets = []
                    for t in self.spawner.emit_until(self.t_wave, self.bounds):
                        t.spawn_time = self.t_wave
                        self.targets.append(t)
                    for tgt in self.targets:
                        if tgt.tid == tid:
                            tgt.alive = False
                            break
            return
        if kind == "finish":
            outcome = action.get("outcome")
            win = action.get("winner")
            lose = action.get("loser")
            mapped = outcome
            if win == self.local_id:
                mapped = "win"
            elif lose == self.local_id:
                mapped = "lose"
            self.pending_payload = {"winner": win, "loser": lose, "scores": self.scores}
            self.pending_outcome = mapped
            self.gs.ended = True


    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self.pending_payload = {"reason": "forfeit"}
        self.pending_outcome = "forfeit"
        self.banner.show("forfeit", subtitle="Forfeit")

    # ---- renderer loader ----
    def _get_renderer(self, screen):
        # Prefer external renderer (graphics.py); fallback to simple inline renderer
        try:
            from .graphics import VectorRenderer  # type: ignore
            return VectorRenderer(screen, CONFIG)
        except Exception:
            return _FallbackRenderer(screen, CONFIG)

# =======================
# Minimal fallback renderer
# =======================
class _FallbackRenderer:
    def __init__(self, screen, config):
        self.screen = screen; self.cfg = config
        pygame.font.init()
        self.font = pygame.font.SysFont("consolas", 20)
        self.big = pygame.font.SysFont("consolas", 36, bold=True)
    def draw_background(self):
        self.screen.fill(self.cfg["BG_COLOR"])
        w, h = self.screen.get_size(); color = (30, 30, 36)
        for i in range(self.cfg["LANE_COUNT"]):
            y = lane_y_from_index(i, self.screen.get_rect())
            pygame.draw.line(self.screen, color, (0, y), (w, y), 1)
    def draw_targets(self, targets, t_now):
        c = {"L": (102,194,255), "M": (125,255,122), "S": (255,122,122), "GOLD": (255,216,77)}
        for t in targets:
            if not t.alive: continue
            color = c["GOLD"] if (t.golden or t.size_key=="GOLD") else c[t.size_key]
            pygame.draw.circle(self.screen, color, (int(t.pos.x), int(t.pos.y)), int(t.radius))
            pygame.draw.circle(self.screen, (240,240,245), (int(t.pos.x), int(t.pos.y)), int(t.radius), 2)
    def draw_crosshair(self, pos):
        x, y = int(pos.x), int(pos.y)
        pygame.draw.circle(self.screen, (230,230,238), (x, y), 10, 1)
        pygame.draw.line(self.screen, (230,230,238), (x-12,y), (x+12,y), 1)
        pygame.draw.line(self.screen, (230,230,238), (x,y-12), (x,y+12), 1)
    def draw_sparks(self, sparks, t_now):
        for s in sparks:
            age = t_now - s.t0; a = max(0.0, 1.0 - age / s.life)
            if a <= 0: continue
            x, y = s.pos
            for i in range(8):
                ang = i * (math.tau / 8); r0 = 4; r1 = 10 * a
                x0 = x + r0 * math.cos(ang); y0 = y + r0 * math.sin(ang)
                x1 = x + r1 * math.cos(ang); y1 = y + r1 * math.sin(ang)
                pygame.draw.line(self.screen, (255,255,255), (x0,y0), (x1,y1), 2)
    def draw_hud(self, gs_view, wave_info, accuracy, quota_text):
        hud=(230,230,238); dim=(150,150,160)
        t=max(0.0, gs_view.time_left); m=int(t//60); s=int(t%60); cs=int((t-int(t))*100)
        timer=f"{m:02d}:{s:02d}.{cs:02d}"; self._text(timer,10,8,hud)
        text=f"{gs_view.score:,}  •  {quota_text}"
        self._text(text,self.screen.get_width()-12,8,hud,right=True)
        ammo=f"{gs_view.mag}/{gs_view.reserve}"
        self._text(ammo,self.screen.get_width()-12,self.screen.get_height()-26,hud,right=True)
        if gs_view.is_reloading:
            frac=max(0.0,min(1.0,gs_view.reload_frac)); w=140; h=8
            x=self.screen.get_width()-w-12; y=self.screen.get_height()-12-h
            pygame.draw.rect(self.screen, dim,(x,y,w,h),1)
            pygame.draw.rect(self.screen,(200,200,240),(x+1,y+1,int((w-2)*frac),h-2))
        if accuracy:
            shots,hits,pct=accuracy
            self._text(f"{hits}/{shots} • {pct:.0f}%", self.screen.get_width()-12,32,dim,right=True)
        if gs_view.banner_text:
            surf=self.big.render(gs_view.banner_text,True,hud)
            self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width()//2,60)))
        if gs_view.streak_badge:
            surf=self.big.render(gs_view.streak_badge,True,hud)
            self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width()//2,self.screen.get_height()//2)))
        # banner overlay handled by parent scene
    def _text(self, s,x,y,color,right=False):
        surf=self.font.render(s,True,color); rect=surf.get_rect()
        rect.topright=(x,y) if right else (x,y); self.screen.blit(surf, rect)

def launch(manager, context, callback, **kwargs):
    return TargetSprintScene(manager, context, callback, **kwargs)
