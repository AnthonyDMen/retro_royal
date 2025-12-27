# minigames/vector_tanks/game.py
import math
import random
import time
from typing import List, Tuple, Optional, Dict, Any

import pygame
from pygame.math import Vector2 as V2
from scene_manager import Scene
from game_context import GameContext

TITLE = "Vector Tanks Duel"
MINIGAME_ID = "vector_tanks"
MULTIPLAYER_ENABLED = True

from .graphics import (
    draw_field,
    draw_obstacles,
    draw_tank,
    draw_shells,
    draw_hud,
    draw_banner,
    draw_particles,
    draw_tank_hp_pips,
    draw_minimap,
)

# ----------------------------
# Basic tuning
# ----------------------------
FPS = 60
FIXED_DT = 1.0 / 60.0
BG_COLOR = (8, 8, 10)

# World (three sections inside a walled arena)
WORLD_W, WORLD_H = 3200, 2000
FIELD_MARGIN = 48

# Perimeter / clearance
PERIMETER_RING = 140  # clear ring between outer and inner wall
EDGE_CLEAR = 72  # extra clearance inside inner wall for obstacles

# Colors
PLAYER_COLOR = (0, 220, 220)
NPC_COLOR = (220, 0, 220)
SHELL_COLOR = (245, 245, 245)
GRID_COLOR = (40, 44, 56)

# Tank dimensions (world px)
HULL_W, HULL_H = 56, 52
TURRET_W, TURRET_H = 34, 26
BARREL_LEN = 32

# Tank dynamics (slow accel, low momentum)
MAX_SPEED_FWD = 180.0
MAX_SPEED_REV = 120.0
ACCEL_FWD = 150.0
ACCEL_REV = 120.0
COAST_DECEL = 320.0
BRAKE_FORCE = 640.0
TURN_RATE = 2.4
TURN_AT_SPEED_FACTOR = 0.65

# Firing
SHELL_SPEED = 520.0
SHELL_RADIUS = 4
SHELL_LIFETIME = 1.8
PLAYER_RELOAD_S = 2.9
NPC_RELOAD_S = 3.0 # frequent NPC fire
NPC_REACT_MIN_S = 0.15
NPC_REACT_MAX_S = 0.30
NPC_FIRE_SPREAD = math.radians(2.2)  # tighter aim
NPC_FIRE_ALIGN_DOT = 0.98  # must face within ~11.5°

# Camera
ZOOM_LEVELS = [1.4, 1.1, 0.9]  # closest → farthest
DEFAULT_ZOOM_INDEX = 0
CAM_FOLLOW_LERP = 0.2

# Rounds / scoring
WINS_TO_TAKE_MATCH = 3  # best-of-5
# Rear-hit: projectile direction must align with tank forward & impact on back-half
REAR_DIR_DOT_THRESH = 0.70
BANNER_TIME_ROUND = 2.0
BANNER_TIME_END = 2.0

# Startup guard
BOOT_QUIT_GRACE_S = 0.6


# --------------------------------
# Helpers & collision primitives
# --------------------------------
def clamp(x, a, b):
    return a if x < a else b if x > b else x


def angle_to_vec(a: float) -> V2:
    return V2(math.cos(a), math.sin(a))


def circle_rect_overlap(center: V2, radius: float, r: pygame.Rect) -> bool:
    nx = clamp(center.x, r.left, r.right)
    ny = clamp(center.y, r.top, r.bottom)
    return (V2(nx, ny) - center).length() <= radius


def resolve_circle_rect(center: V2, radius: float, r: pygame.Rect) -> V2:
    cx, cy = center.x, center.y
    dl = abs(cx - r.left)
    dr = abs(r.right - cx)
    dt = abs(cy - r.top)
    db = abs(r.bottom - cy)
    m = min(dl, dr, dt, db)
    if m == dl:
        cx = r.left - radius
    elif m == dr:
        cx = r.right + radius
    elif m == dt:
        cy = r.top - radius
    else:
        cy = r.bottom + radius
    return V2(cx, cy)


def segment_intersects_rect(p0: V2, p1: V2, rect: pygame.Rect) -> bool:
    return pygame.Rect(rect).clipline(p0.x, p0.y, p1.x, p1.y) != ()


def segment_circle_intersects(p0: V2, p1: V2, c: V2, r: float) -> bool:
    d = p1 - p0
    f = p0 - c
    a = d.dot(d)
    if a == 0:
        return (p0 - c).length() <= r
    b = 2 * f.dot(d)
    cterm = f.dot(f) - r * r
    disc = b * b - 4 * a * cterm
    if disc < 0:
        return False
    disc_sqrt = math.sqrt(disc)
    t1 = (-b - disc_sqrt) / (2 * a)
    t2 = (-b + disc_sqrt) / (2 * a)
    return (0.0 <= t1 <= 1.0) or (0.0 <= t2 <= 1.0)


def segment_circle_hit_t(p0: V2, p1: V2, c: V2, r: float) -> Optional[float]:
    d = p1 - p0
    f = p0 - c
    a = d.dot(d)
    if a == 0:
        return 0.0 if (p0 - c).length() <= r else None
    b = 2 * f.dot(d)
    cterm = f.dot(f) - r * r
    disc = b * b - 4 * a * cterm
    if disc < 0:
        return None
    sq = math.sqrt(disc)
    t1 = (-b - sq) / (2 * a)
    t2 = (-b + sq) / (2 * a)
    hits = [t for t in (t1, t2) if 0.0 <= t <= 1.0]
    return min(hits) if hits else None


def line_of_sight_clear(world, a: V2, b: V2) -> bool:
    for r in world.rect_obstacles:
        if segment_intersects_rect(a, b, r):
            return False
    for c, rad in world.circle_obstacles:
        if segment_circle_intersects(a, b, c, rad):
            return False
    return True


def solve_intercept(
    shooter: V2, target: V2, target_vel: V2, proj_speed: float
) -> Optional[V2]:
    """
    Predictive lead: returns normalized direction to fire from 'shooter' to intercept moving target.
    Solves: |(target - shooter) + target_vel * t| = proj_speed * t
    """
    r = target - shooter
    v = target_vel
    a = v.dot(v) - proj_speed * proj_speed
    b = 2.0 * r.dot(v)
    c = r.dot(r)
    if abs(a) < 1e-6:
        # Nearly linear; aim along current relative position + a tiny lead
        t = -c / b if abs(b) > 1e-6 else 0.0
        if t <= 0.0:
            dir_now = r.normalize() if r.length_squared() > 1e-9 else V2(1, 0)
            return dir_now
        aim = r + v * t
        if aim.length_squared() == 0:
            return None
        return aim.normalize()
    disc = b * b - 4 * a * c
    if disc < 0:
        # No real intercept; aim directly
        return r.normalize() if r.length_squared() > 1e-9 else V2(1, 0)
    sqrt_disc = math.sqrt(disc)
    t1 = (-b - sqrt_disc) / (2 * a)
    t2 = (-b + sqrt_disc) / (2 * a)
    t = min([tt for tt in (t1, t2) if tt > 0.0], default=None)
    if t is None:
        return r.normalize() if r.length_squared() > 1e-9 else V2(1, 0)
    aim = r + v * t
    if aim.length_squared() == 0:
        return None
    return aim.normalize()


# ----------------------------
# World & obstacle layout
# ----------------------------
class World:
    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.inner_rect = self.rect.inflate(-2 * PERIMETER_RING, -2 * PERIMETER_RING)
        self.obs_bounds = self.inner_rect.inflate(-2 * EDGE_CLEAR, -2 * EDGE_CLEAR)
        self.rect_obstacles: List[pygame.Rect] = []
        self.circle_obstacles: List[Tuple[V2, float]] = []

    @classmethod
    def default(cls, screen_size: Tuple[int, int]):
        r = pygame.Rect(FIELD_MARGIN, FIELD_MARGIN, WORLD_W, WORLD_H)
        w = cls(r)
        w._build_three_biomes()
        return w

    def keep_inside(self, pos: V2, half_extent: float) -> V2:
        return V2(
            clamp(pos.x, self.rect.left + half_extent, self.rect.right - half_extent),
            clamp(pos.y, self.rect.top + half_extent, self.rect.bottom - half_extent),
        )

    def _build_three_biomes(self):
        play_rect = self.obs_bounds
        third_w = play_rect.w // 3
        left = pygame.Rect(play_rect.left, play_rect.top, third_w, play_rect.h)
        mid = pygame.Rect(play_rect.left + third_w, play_rect.top, third_w, play_rect.h)
        right = pygame.Rect(
            play_rect.left + 2 * third_w,
            play_rect.top,
            play_rect.w - third_w * 2,
            play_rect.h,
        )

        # Left: urban (big blocks + wide roads)
        block_w, block_h = 220, 160
        road_w = 100
        y = left.top
        while y + block_h <= left.bottom:
            x = left.left
            while x + block_w <= left.right:
                in_road_col = ((x - left.left) // (block_w + road_w)) % 2 == 1
                in_road_row = ((y - left.top) // (block_h + road_w)) % 2 == 1
                if not (in_road_col or in_road_row):
                    pad = 12
                    r = pygame.Rect(
                        int(x + pad),
                        int(y + pad),
                        int(block_w - 2 * pad),
                        int(block_h - 2 * pad),
                    )
                    r.clamp_ip(self.obs_bounds)
                    self.rect_obstacles.append(r)
                x += block_w + road_w
            y += block_h + road_w

        # Middle: mixed
        for i in range(6):
            bw = 140 if i % 2 == 0 else 100
            bh = 90 if i % 2 == 0 else 120
            px = mid.left + 40 + (i * 170) % max(140, (mid.w - bw - 80))
            py = mid.top + 60 + ((i * 230) % max(140, (mid.h - bh - 120)))
            r = pygame.Rect(px, py, bw, bh)
            r.clamp_ip(self.obs_bounds)
            self.rect_obstacles.append(r)
        for i in range(10):
            cr = 28 if i % 3 else 22
            cx = clamp(
                mid.left + 60 + (i * 210) % (mid.w - 120),
                self.obs_bounds.left + cr,
                self.obs_bounds.right - cr,
            )
            cy = clamp(
                mid.top + 80 + ((i * 90 + 50) % (mid.h - 160)),
                self.obs_bounds.top + cr,
                self.obs_bounds.bottom - cr,
            )
            self.circle_obstacles.append((V2(cx, cy), cr))

        # Right: open (sparse)
        for i in range(3):
            bw, bh = (120, 80) if i != 1 else (160, 60)
            px = clamp(
                right.left + 120 + i * (max(1, right.w // 4)),
                self.obs_bounds.left,
                self.obs_bounds.right - bw,
            )
            py = clamp(
                right.centery + (-120 if i % 2 else 100),
                self.obs_bounds.top,
                self.obs_bounds.bottom - bh,
            )
            r = pygame.Rect(int(px), int(py), bw, bh)
            r.clamp_ip(self.obs_bounds)
            self.rect_obstacles.append(r)
        for i in range(8):
            cr = 20 if i % 2 else 26
            cx = clamp(
                right.left + 80 + (i * 220) % max(200, (right.w - 160)),
                self.obs_bounds.left + cr,
                self.obs_bounds.right - cr,
            )
            cy = clamp(
                right.top + 100 + ((i * 140 + 70) % max(220, (right.h - 220))),
                self.obs_bounds.top + cr,
                self.obs_bounds.bottom - cr,
            )
            self.circle_obstacles.append((V2(cx, cy), cr))


# ----------------------------
# Entities & effects
# ----------------------------
class Shell:
    __slots__ = ("pos", "vel", "born", "dead", "owner", "ignore_owner_time")

    def __init__(self, pos: V2, vel: V2, t_now: float, owner: str):
        self.pos = pos.copy()
        self.vel = vel.copy()
        self.born = t_now
        self.dead = False
        self.owner = owner  # "player" or "npc"
        self.ignore_owner_time = 0.08

    def update(self, dt: float, world: World, t_now: float):
        if self.dead:
            return
        self.pos += self.vel * dt
        self.ignore_owner_time = max(0.0, self.ignore_owner_time - dt)
        if (t_now - self.born) > SHELL_LIFETIME:
            self.dead = True
        elif not world.rect.collidepoint(self.pos.x, self.pos.y):
            self.dead = True


class Particle:
    __slots__ = ("pos", "vel", "life", "maxlife", "size", "color")

    def __init__(
        self, pos: V2, vel: V2, life: float, size: float, color: Tuple[int, int, int]
    ):
        self.pos = pos.copy()
        self.vel = vel.copy()
        self.life = life
        self.maxlife = life
        self.size = size
        self.color = color

    def update(self, dt: float):
        self.pos += self.vel * dt
        self.life -= dt


def spawn_sparks(at: V2, count: int = 10) -> List[Particle]:
    parts = []
    for _ in range(count):
        ang = random.uniform(0, math.tau)
        spd = random.uniform(120, 360)
        vel = V2(math.cos(ang), math.sin(ang)) * spd
        life = random.uniform(0.12, 0.25)
        size = random.uniform(2.0, 3.0)
        parts.append(Particle(at, vel, life, size, (255, 200, 80)))
    return parts


def spawn_explosion(at: V2, count: int = 34) -> List[Particle]:
    parts = []
    for _ in range(count):
        ang = random.uniform(0, math.tau)
        spd = random.uniform(140, 420)
        vel = V2(math.cos(ang), math.sin(ang)) * spd
        life = random.uniform(0.35, 0.6)
        size = random.uniform(3.5, 5.5)
        color = (255, 180, 60) if random.random() < 0.6 else (255, 230, 120)
        parts.append(Particle(at, vel, life, size, color))
    return parts


class Tank:
    def __init__(self, pos: V2, angle: float, color: Tuple[int, int, int]):
        self.pos = pos.copy()
        self.angle = angle
        self.speed = 0.0
        self.reload = 0.0
        self.color = color
        self.hp_max = 3
        self.hp = self.hp_max
        self.hit_flash = 0.0  # seconds to flash on damage

    @property
    def fwd(self) -> V2:
        return angle_to_vec(self.angle)

    @property
    def vel(self) -> V2:
        return self.fwd * self.speed

    def reset_for_round(self, pos: V2, angle: float):
        self.pos = pos.copy()
        self.angle = angle
        self.speed = 0.0
        self.reload = 0.0
        self.hp = self.hp_max
        self.hit_flash = 0.0

    def update(self, dt: float, throttle: float, turn_input: float, world: World):
        turn_rate = TURN_RATE * (
            1.0
            - (min(abs(self.speed), MAX_SPEED_FWD) / MAX_SPEED_FWD)
            * (1.0 - TURN_AT_SPEED_FACTOR)
        )
        self.angle += turn_input * turn_rate * dt

        if throttle == 0.0:
            if self.speed != 0.0:
                sign = 1.0 if self.speed > 0 else -1.0
                dec = COAST_DECEL * dt
                self.speed = 0.0 if abs(self.speed) <= dec else self.speed - dec * sign
        else:
            desired = 1.0 if throttle > 0 else -1.0
            if self.speed != 0.0 and (self.speed > 0) != (desired > 0):
                sign = 1.0 if self.speed > 0 else -1.0
                dec = BRAKE_FORCE * dt
                self.speed = 0.0 if abs(self.speed) <= dec else self.speed - dec * sign
            else:
                accel = ACCEL_FWD if throttle > 0 else ACCEL_REV
                self.speed += desired * accel * dt

        self.speed = clamp(self.speed, -MAX_SPEED_REV, MAX_SPEED_FWD)

        half_extent = max(HULL_W, HULL_H) * 0.5
        new_pos = self.pos + self.fwd * self.speed * dt
        new_pos = world.keep_inside(new_pos, half_extent)

        # Resolve vs obstacles (treat tank as circle)
        rad = half_extent * 0.85
        collided = False
        for r in world.rect_obstacles:
            if circle_rect_overlap(new_pos, rad, r):
                new_pos = resolve_circle_rect(new_pos, rad, r)
                collided = True
        for c, cr in world.circle_obstacles:
            delta = new_pos - c
            dist = delta.length()
            if dist < (rad + cr):
                if dist == 0:
                    delta = V2(1, 0)
                    dist = 1
                push = (rad + cr) - dist
                new_pos += (delta / dist) * push
                collided = True
        if collided and self.speed > 0:
            self.speed *= 0.4

        self.pos = new_pos
        if self.reload > 0.0:
            self.reload -= dt
        if self.hit_flash > 0.0:
            self.hit_flash -= dt

    def apply_hit(self, hit_point: V2, impact_dir: V2):
        """
        Rear-arc KO when:
          1) impact is on the back half (fwd·(hit_point - pos) < 0),
          2) projectile direction aligns with tank forward (impact_dir·fwd >= REAR_DIR_DOT_THRESH).
        Else: -1 HP.
        """
        rel = hit_point - self.pos
        is_back_half = rel.dot(self.fwd) < 0
        aligned_with_back = impact_dir.normalize().dot(self.fwd) >= REAR_DIR_DOT_THRESH
        if is_back_half and aligned_with_back:
            self.hp = 0
        else:
            self.hp = max(0, self.hp - 1)
        self.hit_flash = 0.25


# ----------------------------
# NPC controller (smart movement + predictive forward-only fire)
# ----------------------------
class NPCController:
    def __init__(self, tank: Tank, world: World):
        self.tank = tank
        self.world = world
        self.target = self.pick_waypoint()
        self.repick_timer = 0.0
        self.react_timer = random.uniform(NPC_REACT_MIN_S, NPC_REACT_MAX_S)
        self.strafe_dir = random.choice([-1, 1])  # left/right preference

    def pick_waypoint(self) -> V2:
        for _ in range(60):
            x = random.uniform(
                self.world.obs_bounds.left + 40, self.world.obs_bounds.right - 40
            )
            y = random.uniform(
                self.world.obs_bounds.top + 40, self.world.obs_bounds.bottom - 40
            )
            p = V2(x, y)
            if self._point_clear(p, radius=30):
                return p
        return V2(self.world.inner_rect.center)

    def _point_clear(self, p: V2, radius: float) -> bool:
        for r in self.world.rect_obstacles:
            if circle_rect_overlap(p, radius, r):
                return False
        for c, cr in self.world.circle_obstacles:
            if (p - c).length() < (radius + cr):
                return False
        return True

    def choose_vantage_near(self, player_pos: V2) -> V2:
        """Sample around the player and pick a clear LoS point inside the inner field."""
        best = None
        best_cost = 1e9
        radii = (540, 380, 260)  # try far to near
        for r in radii:
            for k in range(12):
                ang = (k / 12.0) * math.tau
                cand = player_pos + V2(math.cos(ang), math.sin(ang)) * r
                # clamp to inner field
                cand.x = clamp(
                    cand.x,
                    self.world.inner_rect.left + 50,
                    self.world.inner_rect.right - 50,
                )
                cand.y = clamp(
                    cand.y,
                    self.world.inner_rect.top + 50,
                    self.world.inner_rect.bottom - 50,
                )
                if not self._point_clear(cand, 30):
                    continue
                if not line_of_sight_clear(self.world, cand, player_pos):
                    continue
                # cost: distance from our current position
                cost = (cand - self.tank.pos).length()
                if cost < best_cost:
                    best_cost = cost
                    best = cand
        return best if best is not None else self.pick_waypoint()

    def update(self, dt: float, player: Tank, shells: List["Shell"], t_now: float):
        tnk = self.tank

        # line-of-sight check
        has_los = line_of_sight_clear(self.world, tnk.pos, player.pos)
        dist = (player.pos - tnk.pos).length()

        # Decide desired facing (aim) using predictive lead when possible
        if has_los:
            lead_dir = solve_intercept(tnk.pos, player.pos, player.vel, SHELL_SPEED)
            if lead_dir is None:
                lead_dir = (
                    (player.pos - tnk.pos).normalize()
                    if (player.pos - tnk.pos).length_squared() > 1e-6
                    else V2(1, 0)
                )
            aim_dir = lead_dir
        else:
            aim_dir = (
                (self.target - tnk.pos).normalize()
                if (self.target - tnk.pos).length_squared() > 1e-6
                else tnk.fwd
            )

        desired_angle = math.atan2(aim_dir.y, aim_dir.x)

        # Movement policy:
        #   Far   (>900): advance
        #   Mid   (400..900): strafe around the target
        #   Close (<400): back off slowly while staying aimed
        if has_los:
            if dist > 900:
                throttle = 1.0
                move_angle = desired_angle
            elif dist > 400:
                throttle = 1.0
                move_angle = desired_angle + self.strafe_dir * math.radians(
                    50
                )  # circle-strafe
                # occasionally flip strafe direction
                if random.random() < 0.003:
                    self.strafe_dir *= -1
            else:
                throttle = -0.5
                move_angle = desired_angle
        else:
            # No LoS: head to a vantage point near the player
            if (tnk.pos - self.target).length() < 60 or self.repick_timer > 3.0:
                self.target = self.choose_vantage_near(player.pos)
                self.repick_timer = 0.0
            throttle = 1.0
            move_angle = math.atan2(
                (self.target - tnk.pos).y, (self.target - tnk.pos).x
            )

        # Turn: bias toward move_angle but keep gun aligned with aim_dir (we fire forward)
        turn_to = move_angle
        diff = (turn_to - tnk.angle + math.pi) % (2 * math.pi) - math.pi
        turn_input = clamp(diff / 0.8, -1.0, 1.0)

        # Update tank kinematics
        tnk.update(dt, throttle, turn_input, self.world)
        self.repick_timer += dt

        # Shooting (forward-only), with small spread that shrinks when closer
        self.react_timer -= dt
        if has_los and self.react_timer <= 0.0 and tnk.reload <= 0.0:
            # alignment check vs AIM, but we can only shoot forward (tnk.fwd)
            align = tnk.fwd.dot(aim_dir)
            if align >= NPC_FIRE_ALIGN_DOT:
                dist_factor = clamp(dist / 1400.0, 0.2, 1.0)  # closer => smaller jitter
                jitter = random.uniform(
                    -NPC_FIRE_SPREAD * dist_factor, NPC_FIRE_SPREAD * dist_factor
                )
                fire_dir = angle_to_vec(math.atan2(tnk.fwd.y, tnk.fwd.x) + jitter)
                muzzle = tnk.pos + fire_dir * ((HULL_H * 0.5) + 10)
                shells.append(Shell(muzzle, fire_dir * SHELL_SPEED, t_now, owner="npc"))
                tnk.reload = NPC_RELOAD_S
                self.react_timer = random.uniform(NPC_REACT_MIN_S, NPC_REACT_MAX_S)


# ----------------------------
# Camera
# ----------------------------
class Camera:
    def __init__(
        self, screen_size: Tuple[int, int], world_rect: pygame.Rect, zoom: float
    ):
        self.sw, self.sh = screen_size
        self.world = world_rect
        self.zoom = max(0.5, float(zoom))
        self.center = V2(world_rect.center)

    def clamp_center(self):
        half_w = (self.sw * 0.5) / self.zoom
        half_h = (self.sh * 0.5) / self.zoom
        min_x = self.world.left + half_w
        max_x = self.world.right - half_w
        min_y = self.world.top + half_h
        max_y = self.world.bottom - half_h
        self.center.x = clamp(self.center.x, min_x, max_x)
        self.center.y = clamp(self.center.y, min_y, max_y)

    def follow(self, target_pos: V2, lerp: float = CAM_FOLLOW_LERP):
        self.center += (target_pos - self.center) * clamp(lerp, 0.0, 1.0)
        self.clamp_center()

    def world_to_screen(self, p: V2) -> Tuple[int, int]:
        dp = (p - self.center) * self.zoom
        return (int(dp.x + self.sw * 0.5), int(dp.y + self.sh * 0.5))

    def scale_len(self, v: float) -> int:
        return max(1, int(v * self.zoom))


# ----------------------------
# Utility: spawns
# ----------------------------
def find_clear_spawn(world: World, near: V2, radius: float = 40) -> V2:
    base = V2(
        clamp(near.x, world.inner_rect.left + radius, world.inner_rect.right - radius),
        clamp(near.y, world.inner_rect.top + radius, world.inner_rect.bottom - radius),
    )
    p = base
    for rings in range(20):
        for dx, dy in ((rings, 0), (-rings, 0), (0, rings), (0, -rings)):
            cand = base + V2(dx * 40, dy * 40)
            if not world.inner_rect.collidepoint(cand.x, cand.y):
                continue
            ok = True
            for r in world.rect_obstacles:
                if circle_rect_overlap(cand, radius, r):
                    ok = False
                    break
            if ok:
                for c, cr in world.circle_obstacles:
                    if (cand - c).length() < (radius + cr):
                        ok = False
                        break
            if ok:
                return cand
    return p


# ----------------------------
class VectorTanksScene(Scene):
    def __init__(self, manager, context=None, callback=None, difficulty=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.minigame_id = MINIGAME_ID

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
            self.duel_id
            and self.participants
            and self.net_client
            and self.local_id in self.participants
        )
        self.is_authority = not self.net_enabled or self.local_idx == 0
        self.net_interval = 1.0 / 20.0
        self.net_last = 0.0
        self.remote_input = {"throttle": 0.0, "turn": 0.0}
        self._input_last = {"throttle": 0.0, "turn": 0.0}
        self._input_last_time = 0.0
        self.pending_payload: Dict[str, Any] = {}

        self.world = World.default((self.w, self.h))
        self.player = Tank(
            V2(self.world.inner_rect.centerx - 500, self.world.inner_rect.centery),
            angle=-math.pi / 2,
            color=PLAYER_COLOR,
        )
        self.enemy = Tank(
            V2(self.world.inner_rect.centerx + 500, self.world.inner_rect.centery),
            angle=math.pi / 2,
            color=NPC_COLOR,
        )
        self.npc_ai = None if self.net_enabled else NPCController(self.enemy, self.world)
        self._player_target = {"pos": self.player.pos.copy(), "angle": self.player.angle, "speed": 0.0}
        self._enemy_target = {"pos": self.enemy.pos.copy(), "angle": self.enemy.angle, "speed": 0.0}
        self._got_state = False

        self.zoom_idx = DEFAULT_ZOOM_INDEX
        self.camera = Camera(
            (self.w, self.h), self.world.rect, zoom=ZOOM_LEVELS[self.zoom_idx]
        )

        self.shells: List[Shell] = []
        self.particles: List[Particle] = []
        self.time = 0.0
        self.accumulator = 0.0

        self.player_wins = 0
        self.enemy_wins = 0
        self.state = "READY"
        self.banner_timer = BANNER_TIME_ROUND
        self.last_banner = "Best of 5 - First to 3"

        self._pending_outcome: Optional[str] = None
        self._finalized = False
        self.forfeited = False
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # --- lifecycle helpers ---
    def _start_round(self):
        p_spawn = find_clear_spawn(
            self.world, V2(self.world.inner_rect.left + 200, self.world.inner_rect.centery)
        )
        e_spawn = find_clear_spawn(
            self.world, V2(self.world.inner_rect.right - 200, self.world.inner_rect.centery)
        )
        self.player.reset_for_round(p_spawn, angle=-math.pi / 2)
        self.enemy.reset_for_round(e_spawn, angle=math.pi / 2)
        self.shells.clear()
        if self.net_enabled:
            self.remote_input = {"throttle": 0.0, "turn": 0.0}
        self.state = "PLAY"
        self.banner_timer = 0.0

    def _set_pending_payload(self, outcome: str, reason: str = ""):
        payload: Dict[str, Any] = {}
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

    def _begin_match_end(
        self,
        outcome: str,
        send_finish: bool = False,
        reason: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ):
        if self._pending_outcome:
            return
        self.state = "MATCH_END"
        self._pending_outcome = outcome
        if outcome == "win":
            self.last_banner = "You win the match!"
        elif outcome == "lose":
            self.last_banner = "You lose the match!"
        else:
            self.last_banner = "Match forfeited"
        self.banner_timer = BANNER_TIME_END
        if payload is not None:
            self.pending_payload = payload
        else:
            self._set_pending_payload(outcome, reason)
        if send_finish:
            self._net_send_finish(outcome, reason)

    def _player_fire(self):
        if self.state != "PLAY":
            return
        if self.player.reload > 0.0:
            return
        muzzle = self.player.pos + self.player.fwd * ((HULL_H * 0.5) + 10)
        self.shells.append(
            Shell(muzzle, self.player.fwd * SHELL_SPEED, self.time, owner="player")
        )
        self.player.reload = PLAYER_RELOAD_S
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _enemy_fire(self):
        if self.state != "PLAY":
            return
        if self.enemy.reload > 0.0:
            return
        muzzle = self.enemy.pos + self.enemy.fwd * ((HULL_H * 0.5) + 10)
        self.shells.append(
            Shell(muzzle, self.enemy.fwd * SHELL_SPEED, self.time, owner="npc")
        )
        self.enemy.reload = PLAYER_RELOAD_S if self.net_enabled else NPC_RELOAD_S

    def _read_local_input(self) -> Tuple[float, float]:
        keys = pygame.key.get_pressed()
        throttle = float(
            (keys[pygame.K_w] or keys[pygame.K_UP])
            - (keys[pygame.K_s] or keys[pygame.K_DOWN])
        )
        turn = float(
            (keys[pygame.K_d] or keys[pygame.K_RIGHT])
            - (keys[pygame.K_a] or keys[pygame.K_LEFT])
        )
        return throttle, turn

    def _adjust_zoom(self, delta: int):
        new_idx = max(0, min(len(ZOOM_LEVELS) - 1, self.zoom_idx + delta))
        if new_idx == self.zoom_idx:
            return
        self.zoom_idx = new_idx
        self.camera.zoom = ZOOM_LEVELS[self.zoom_idx]
        self.camera.clamp_center()

    # --- event handling ---
    def handle_event(self, event):
        if self.state == "MATCH_END" and self._pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                if event.type != pygame.MOUSEBUTTONDOWN or event.button == 1:
                    self._finalize(self._pending_outcome)
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._pause_game()
            elif event.key == pygame.K_SPACE:
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "fire"})
                else:
                    self._player_fire()
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self._adjust_zoom(-1)
            elif event.key in (pygame.K_EQUALS, pygame.K_KP_PLUS):
                self._adjust_zoom(1)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[VectorTanks] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def forfeit_from_pause(self):
        if self._finalized:
            return
        self.forfeited = True
        if self.net_enabled:
            if self.is_authority:
                self._begin_match_end("forfeit", send_finish=True, reason="forfeit")
            else:
                self._net_send_action({"kind": "forfeit"})
                self._begin_match_end("forfeit", reason="forfeit")
            return
        self._finalize("forfeit")

    # --- main loop ---
    def update(self, dt):
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self._finalized:
            return
        if self.net_enabled and not self.is_authority:
            throttle, turn = self._read_local_input()
            self._send_local_input(throttle, turn)
            self._tick_remote_view(dt)
            return
        self.accumulator += dt
        while self.accumulator >= FIXED_DT:
            self.accumulator -= FIXED_DT
            self.time += FIXED_DT
            self._step_sim()
        if self.net_enabled and self.is_authority:
            self._net_send_state()

    def _step_sim(self):
        if self.state == "READY":
            self.banner_timer -= FIXED_DT
            if self.banner_timer <= 0.0:
                self._start_round()
            return

        if self.state == "ROUND_END":
            self.banner_timer -= FIXED_DT
            if self.banner_timer <= 0.0:
                if self.player_wins >= WINS_TO_TAKE_MATCH:
                    self._begin_match_end(
                        "win", send_finish=self.net_enabled and self.is_authority
                    )
                elif self.enemy_wins >= WINS_TO_TAKE_MATCH:
                    self._begin_match_end(
                        "lose", send_finish=self.net_enabled and self.is_authority
                    )
                else:
                    self.state = "READY"
                    self.last_banner = f"Score {self.player_wins}-{self.enemy_wins} - Next Round"
                    self.banner_timer = BANNER_TIME_ROUND
            return

        if self.state == "MATCH_END":
            self.banner_timer -= FIXED_DT
            if self.banner_timer <= 0.0 and self._pending_outcome:
                self._finalize(self._pending_outcome)
            return

        if self.state != "PLAY":
            return

        throttle, turn = self._read_local_input()
        self.player.update(FIXED_DT, throttle, turn, self.world)
        if self.net_enabled:
            r_throttle = float(self.remote_input.get("throttle", 0.0))
            r_turn = float(self.remote_input.get("turn", 0.0))
            self.enemy.update(FIXED_DT, r_throttle, r_turn, self.world)
        else:
            if self.npc_ai:
                self.npc_ai.update(FIXED_DT, self.player, self.shells, self.time)
        self.camera.follow(self.player.pos)

        player_ko, enemy_ko = self._update_shells()

        alive = []
        for p in self.particles:
            p.update(FIXED_DT)
            if p.life > 0.0:
                alive.append(p)
        self.particles = alive

        if player_ko or enemy_ko:
            if player_ko:
                self.particles.extend(spawn_explosion(self.player.pos))
            if enemy_ko:
                self.particles.extend(spawn_explosion(self.enemy.pos))
            if player_ko and enemy_ko:
                self.last_banner = "Round: Tie - Replay"
            elif enemy_ko:
                self.last_banner = "Round: You Win"
                self.player_wins += 1
            else:
                self.last_banner = "Round: You Lose"
                self.enemy_wins += 1
            self.state = "ROUND_END"
            self.banner_timer = BANNER_TIME_ROUND

    def _update_shells(self):
        new_shells = []
        player_ko = False
        enemy_ko = False
        for shell in self.shells:
            if shell.dead:
                continue
            prev = shell.pos.copy()
            shell.update(FIXED_DT, self.world, self.time)
            if shell.dead:
                continue

            hit_obs = False
            for rect in self.world.rect_obstacles:
                if segment_intersects_rect(prev, shell.pos, rect):
                    hit_obs = True
                    break
            if not hit_obs:
                for center, radius in self.world.circle_obstacles:
                    if segment_circle_intersects(prev, shell.pos, center, radius):
                        hit_obs = True
                        break
            if hit_obs:
                shell.dead = True
                self.particles.extend(spawn_sparks(shell.pos))
                continue

            half_extent = max(HULL_W, HULL_H) * 0.5
            tank_rad = half_extent * 0.85
            impact_dir = shell.pos - prev
            if impact_dir.length_squared() > 1e-6:
                impact_dir = impact_dir.normalize()
            else:
                impact_dir = V2(1, 0)

            if shell.owner != "player":
                t_hit = segment_circle_hit_t(prev, shell.pos, self.player.pos, tank_rad)
                if t_hit is not None and shell.ignore_owner_time <= 0.0:
                    hit_point = prev.lerp(shell.pos, t_hit)
                    self.player.apply_hit(hit_point, impact_dir)
                    shell.dead = True
                    self.particles.extend(spawn_sparks(hit_point))
                    if self.player.hp <= 0:
                        player_ko = True
                    continue

            if shell.owner != "npc":
                t_hit = segment_circle_hit_t(prev, shell.pos, self.enemy.pos, tank_rad)
                if t_hit is not None and shell.ignore_owner_time <= 0.0:
                    hit_point = prev.lerp(shell.pos, t_hit)
                    self.enemy.apply_hit(hit_point, impact_dir)
                    shell.dead = True
                    self.particles.extend(spawn_sparks(hit_point))
                    if self.enemy.hp <= 0:
                        enemy_ko = True
                    continue

            new_shells.append(shell)

        self.shells = new_shells
        return player_ko, enemy_ko

    # --- networking ---
    def _pack_tank(self, tank: Tank) -> Dict[str, Any]:
        return {
            "pos": [tank.pos.x, tank.pos.y],
            "angle": tank.angle,
            "speed": tank.speed,
            "reload": tank.reload,
            "hp": tank.hp,
            "hit_flash": tank.hit_flash,
        }

    def _apply_tank_state(self, tank: Tank, st: Optional[Dict[str, Any]], target: Optional[Dict[str, Any]] = None, snap: bool = False):
        if not st:
            return
        pos = st.get("pos")
        ang = st.get("angle", tank.angle)
        speed = st.get("speed", tank.speed)
        reload_v = st.get("reload", tank.reload)
        hp = st.get("hp", tank.hp)
        flash = st.get("hit_flash", tank.hit_flash)
        if target is not None and not snap and not self.is_authority:
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                target["pos"] = V2(float(pos[0]), float(pos[1]))
            target["angle"] = float(ang)
            target["speed"] = float(speed)
        else:
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                tank.pos.update(float(pos[0]), float(pos[1]))
            try:
                tank.angle = float(ang)
            except Exception:
                pass
            try:
                tank.speed = float(speed)
            except Exception:
                pass
        try:
            tank.reload = float(reload_v)
        except Exception:
            pass
        try:
            tank.hp = int(hp)
        except Exception:
            pass
        try:
            tank.hit_flash = float(flash)
        except Exception:
            pass

    def _pack_state(self) -> Dict[str, Any]:
        shells = []
        for shell in self.shells:
            if shell.dead:
                continue
            shells.append(
                {
                    "pos": [shell.pos.x, shell.pos.y],
                    "vel": [shell.vel.x, shell.vel.y],
                    "owner": shell.owner,
                    "born": shell.born,
                    "ignore": shell.ignore_owner_time,
                }
            )
        return {
            "state": self.state,
            "banner_timer": self.banner_timer,
            "last_banner": self.last_banner,
            "player_wins": self.player_wins,
            "enemy_wins": self.enemy_wins,
            "time": self.time,
            "player": self._pack_tank(self.player),
            "enemy": self._pack_tank(self.enemy),
            "shells": shells,
        }

    def _map_banner_text(self, text: str) -> str:
        if self.local_idx == 0:
            return text
        swaps = {
            "You win the match!": "You lose the match!",
            "You lose the match!": "You win the match!",
            "Round: You Win": "Round: You Lose",
            "Round: You Lose": "Round: You Win",
        }
        return swaps.get(text, text)

    def _apply_state(self, st: Dict[str, Any]):
        if not st or self._finalized:
            return
        self.state = st.get("state", self.state)
        try:
            self.banner_timer = float(st.get("banner_timer", self.banner_timer))
        except Exception:
            pass
        self.time = float(st.get("time", self.time))

        host_banner = st.get("last_banner", self.last_banner)
        host_p_wins = int(st.get("player_wins", self.player_wins))
        host_e_wins = int(st.get("enemy_wins", self.enemy_wins))

        if self.local_idx == 0:
            p_state = st.get("player")
            e_state = st.get("enemy")
            self.player_wins = host_p_wins
            self.enemy_wins = host_e_wins
        else:
            p_state = st.get("enemy")
            e_state = st.get("player")
            self.player_wins = host_e_wins
            self.enemy_wins = host_p_wins

        snap = not self._got_state
        self._apply_tank_state(self.player, p_state, target=self._player_target, snap=snap)
        self._apply_tank_state(self.enemy, e_state, target=self._enemy_target, snap=snap)
        self._got_state = True

        self.shells = []
        for shell in st.get("shells", []) or []:
            pos = shell.get("pos")
            vel = shell.get("vel")
            if not (isinstance(pos, (list, tuple)) and isinstance(vel, (list, tuple))):
                continue
            if len(pos) != 2 or len(vel) != 2:
                continue
            born = float(shell.get("born", self.time))
            owner = shell.get("owner", "player")
            s_obj = Shell(V2(pos[0], pos[1]), V2(vel[0], vel[1]), born, owner)
            try:
                s_obj.ignore_owner_time = float(shell.get("ignore", s_obj.ignore_owner_time))
            except Exception:
                pass
            self.shells.append(s_obj)

        self.last_banner = self._map_banner_text(host_banner)
        if self.state == "READY":
            if self.player_wins == 0 and self.enemy_wins == 0:
                self.last_banner = "Best of 5 - First to 3"
            else:
                self.last_banner = f"Score {self.player_wins}-{self.enemy_wins} - Next Round"

    def _net_send_action(self, payload: Dict[str, Any]):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[VectorTanks] Failed to send action: {exc}")

    def _net_send_state(self, force: bool = False):
        if not self.net_enabled or not self.is_authority:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
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

    def _apply_remote_action(self, action: Dict[str, Any]):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "input" and self.is_authority:
            try:
                throttle = float(action.get("throttle", 0.0))
            except Exception:
                throttle = 0.0
            try:
                turn = float(action.get("turn", 0.0))
            except Exception:
                turn = 0.0
            self.remote_input = {
                "throttle": clamp(throttle, -1.0, 1.0),
                "turn": clamp(turn, -1.0, 1.0),
            }
            return
        if kind == "fire" and self.is_authority:
            self._enemy_fire()
            self._net_send_state(force=True)
            return
        if kind == "forfeit" and self.is_authority:
            self._begin_match_end(
                "win", send_finish=True, reason="opponent forfeit"
            )
            return
        if kind == "finish":
            if self.is_authority:
                return
            winner = action.get("winner")
            loser = action.get("loser")
            outcome = action.get("outcome") or "lose"
            reason = action.get("reason") or ""
            mapped = outcome
            if winner and self.local_id:
                if winner == self.local_id:
                    mapped = "win"
                elif loser == self.local_id:
                    mapped = "lose"
            elif outcome == "forfeit":
                mapped = "lose"
            payload = {"winner": winner, "loser": loser}
            if reason:
                payload["reason"] = reason
            self._begin_match_end(mapped, reason=reason, payload=payload)
            return

    def _send_local_input(self, throttle: float, turn: float):
        if not self.net_enabled or self.is_authority:
            return
        throttle = clamp(float(throttle), -1.0, 1.0)
        turn = clamp(float(turn), -1.0, 1.0)
        now = time.perf_counter()
        changed = (
            throttle != self._input_last.get("throttle", 0.0)
            or turn != self._input_last.get("turn", 0.0)
        )
        if not changed and (now - self._input_last_time) < self.net_interval:
            return
        self._input_last = {"throttle": throttle, "turn": turn}
        self._input_last_time = now
        self._net_send_action({"kind": "input", "throttle": throttle, "turn": turn})

    def _tick_remote_view(self, dt: float):
        if self.state == "MATCH_END" and self._pending_outcome:
            self.banner_timer = max(0.0, self.banner_timer - dt)
            if self.banner_timer <= 0.0:
                self._finalize(self._pending_outcome)
                return
        self.time += dt
        for shell in self.shells:
            shell.update(dt, self.world, self.time)
        self.shells = [s for s in self.shells if not s.dead]
        alive = []
        for p in self.particles:
            p.update(dt)
            if p.life > 0.0:
                alive.append(p)
        self.particles = alive
        self._smooth_tank(self.player, self._player_target, dt)
        self._smooth_tank(self.enemy, self._enemy_target, dt)
        self.camera.follow(self.player.pos)

    def _smooth_tank(self, tank: Tank, target: Dict[str, Any], dt: float):
        if not target:
            return
        pos = target.get("pos")
        if isinstance(pos, V2):
            lerp_k = min(1.0, dt * 10.0)
            tank.pos += (pos - tank.pos) * lerp_k
        try:
            tgt_angle = float(target.get("angle", tank.angle))
            diff = (tgt_angle - tank.angle + math.pi) % (2 * math.pi) - math.pi
            tank.angle += diff * min(1.0, dt * 8.0)
        except Exception:
            pass
        try:
            tank.speed = float(target.get("speed", tank.speed))
        except Exception:
            pass

    # --- rendering ---
    def draw(self):
        if (self.w, self.h) != self.manager.size:
            self.w, self.h = self.manager.size
            self.screen = self.manager.screen
            self.camera = Camera(
                (self.w, self.h), self.world.rect, zoom=ZOOM_LEVELS[self.zoom_idx]
            )

        self.screen.fill(BG_COLOR)
        draw_field(self.screen, self.world, self.camera, GRID_COLOR)
        draw_obstacles(self.screen, self.world, self.camera)
        draw_shells(self.screen, self.shells, SHELL_COLOR, SHELL_RADIUS, self.camera)

        draw_tank(
            self.screen,
            self.player,
            HULL_W,
            HULL_H,
            TURRET_W,
            TURRET_H,
            BARREL_LEN,
            self.camera,
            self.player.color,
            barrel_thickness=8,
            flash=max(0.0, self.player.hit_flash),
        )
        if line_of_sight_clear(self.world, self.player.pos, self.enemy.pos):
            draw_tank(
                self.screen,
                self.enemy,
                HULL_W,
                HULL_H,
                TURRET_W,
                TURRET_H,
                BARREL_LEN,
                self.camera,
                self.enemy.color,
                barrel_thickness=8,
                flash=max(0.0, self.enemy.hit_flash),
            )
            draw_tank_hp_pips(
                self.screen, self.enemy, self.camera, hp=self.enemy.hp, hp_max=self.enemy.hp_max
            )
        draw_tank_hp_pips(
            self.screen, self.player, self.camera, hp=self.player.hp, hp_max=self.player.hp_max
        )

        draw_particles(self.screen, self.particles, self.camera)

        reload_frac = (
            0.0
            if self.player.reload <= 0
            else clamp(1.0 - (self.player.reload / PLAYER_RELOAD_S), 0.0, 1.0)
        )
        draw_hud(
            self.screen, reload_frac, self.player_wins, self.enemy_wins, WINS_TO_TAKE_MATCH
        )
        draw_minimap(self.screen, self.world, self.player, self.enemy)

        if self.state in ("READY", "ROUND_END", "MATCH_END"):
            draw_banner(self.screen, self.last_banner)

    # --- results ---
    def _result_details(self):
        return {
            "player_wins": self.player_wins,
            "enemy_wins": self.enemy_wins,
            "best_of": WINS_TO_TAKE_MATCH * 2 - 1,
            "forfeit": self.forfeited,
        }

    def _finalize(self, outcome: str):
        if self._finalized:
            return
        self._finalized = True
        if self.context is None:
            self.context = GameContext()
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self._result_details(),
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
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[VectorTanks] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[VectorTanks] Callback error: {exc}")


def launch(manager, context, callback, **kwargs):
    return VectorTanksScene(manager, context, callback, **kwargs)
