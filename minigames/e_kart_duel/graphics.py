# minigames/e_kart_duel/graphics.py
# Tile atlas (STRAIGHT, TURN90_R, TURN90_L, FINISH) + top-down vector Kart renderer.
# No external assets. Provides dotted centerline, borders, and a checkered FINISH tile
# with gate geometry for lap counting / one-way barrier.

import math
import pygame
from pygame.math import Vector2 as Vec2

__all__ = ["TileAtlas", "TILE_SIZE", "COL_GRASS", "Kart"]

# ---------- Colors ----------
COL_GRASS = (16, 84, 30)
COL_ROADF = (44, 44, 48)
COL_BORDER = (240, 240, 240)
COL_CENTER = (182, 186, 196)  # dotted mid line
COL_FLAG_W = (240, 240, 240)
COL_FLAG_B = (40, 40, 44)

# ---------- Tile params ----------
TILE_SIZE = 256
ROAD_W = 96.0
HALF_W = ROAD_W * 0.5
BORDER_PX = 6

TILE_IDS = ("STRAIGHT", "TURN90_R", "TURN90_L", "FINISH")
ROTS = (0, 90, 180, 270)

# ---------- rotation helpers ----------


def _rot_point(p: Vec2, rot: int) -> Vec2:
    if rot == 0:
        return Vec2(p)
    if rot == 90:
        return Vec2(-p.y, p.x)
    if rot == 180:
        return Vec2(-p.x, -p.y)
    if rot == 270:
        return Vec2(p.y, -p.x)
    raise ValueError("rot must be 0/90/180/270")


def _rot_vec(v: Vec2, rot: int) -> Vec2:
    return _rot_point(v, rot)


def _rot_dir(card: str, rot: int) -> str:
    dirs = ["N", "E", "S", "W"]
    i = dirs.index(card)
    return dirs[(i + (rot // 90)) % 4]


def _to_surface(p: Vec2) -> tuple[int, int]:
    x = p.x + TILE_SIZE * 0.5
    y = p.y + TILE_SIZE * 0.5
    return max(0, min(TILE_SIZE - 1, int(round(x)))), max(
        0, min(TILE_SIZE - 1, int(round(y)))
    )


# ---------- base metadata (0 deg canonical) ----------


def _meta_straight_like(with_gate: bool = False):
    w = TILE_SIZE * 0.5
    connectors = [
        {"name": "W", "pos": Vec2(-w, 0.0), "dir": "W", "tangent": Vec2(+1, 0)},
        {"name": "E", "pos": Vec2(+w, 0.0), "dir": "E", "tangent": Vec2(+1, 0)},
    ]
    centerline = [Vec2(-w, 0.0), Vec2(+w, 0.0)]
    poly = [Vec2(-w, -HALF_W), Vec2(+w, -HALF_W), Vec2(+w, +HALF_W), Vec2(-w, +HALF_W)]
    base = {
        "id": "STRAIGHT",
        "road_poly": poly,
        "centerline": centerline,
        "connectors": connectors,
    }
    if with_gate:
        base["gate"] = {
            "a": Vec2(0.0, -HALF_W),
            "b": Vec2(0.0, +HALF_W),
            "tangent": Vec2(+1.0, 0.0),  # forward direction along the track
        }
    return base


def _meta_straight():
    return _meta_straight_like(with_gate=False)


def _meta_finish():
    d = _meta_straight_like(with_gate=True)
    d["id"] = "FINISH"
    return d


def _meta_turn90_right():
    """
    Right turn canonical: heading East then turning South.
    Corner center at bottom-left (-T/2, +T/2).
    """
    T = TILE_SIZE
    c = Vec2(-T * 0.5, +T * 0.5)
    r_c = T * 0.5
    r_out = r_c + HALF_W
    r_in = r_c - HALF_W
    steps = 32

    outer = [
        c
        + Vec2(
            r_out * math.cos(-math.pi * 0.5 * (1 - i / steps)),
            r_out * math.sin(-math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(steps + 1)
    ]
    inner = [
        c
        + Vec2(
            r_in * math.cos(-math.pi * 0.5 * (1 - i / steps)),
            r_in * math.sin(-math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(steps, -1, -1)
    ]
    poly = outer + inner

    cl = [
        c
        + Vec2(
            r_c * math.cos(-math.pi * 0.5 * (1 - i / steps)),
            r_c * math.sin(-math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(steps + 1)
    ]

    connectors = [
        {"name": "W", "pos": Vec2(-T * 0.5, 0.0), "dir": "W", "tangent": Vec2(+1, 0)},
        {"name": "S", "pos": Vec2(0.0, +T * 0.5), "dir": "S", "tangent": Vec2(0, +1)},
    ]
    return {
        "id": "TURN90_R",
        "road_poly": poly,
        "centerline": cl,
        "connectors": connectors,
    }


def _meta_turn90_left():
    """
    Left turn canonical: heading East then turning North.
    Corner center at top-left (-T/2, -T/2).
    """
    T = TILE_SIZE
    c = Vec2(-T * 0.5, -T * 0.5)
    r_c = T * 0.5
    r_out = r_c + HALF_W
    r_in = r_c - HALF_W
    steps = 32

    outer = [
        c
        + Vec2(
            r_out * math.cos(+math.pi * 0.5 * (1 - i / steps)),
            r_out * math.sin(+math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(steps, -1, -1)
    ]
    inner = [
        c
        + Vec2(
            r_in * math.cos(+math.pi * 0.5 * (1 - i / steps)),
            r_in * math.sin(+math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(0, steps + 1)
    ]
    poly = outer + inner

    cl = [
        c
        + Vec2(
            r_c * math.cos(+math.pi * 0.5 * (1 - i / steps)),
            r_c * math.sin(+math.pi * 0.5 * (1 - i / steps)),
        )
        for i in range(steps, -1, -1)
    ]

    connectors = [
        {"name": "W", "pos": Vec2(-T * 0.5, 0.0), "dir": "W", "tangent": Vec2(+1, 0)},
        {"name": "N", "pos": Vec2(0.0, -T * 0.5), "dir": "N", "tangent": Vec2(0, -1)},
    ]
    return {
        "id": "TURN90_L",
        "road_poly": poly,
        "centerline": cl,
        "connectors": connectors,
    }


BASE_META = {
    "STRAIGHT": _meta_straight(),
    "TURN90_R": _meta_turn90_right(),
    "TURN90_L": _meta_turn90_left(),
    "FINISH": _meta_finish(),
}

# ---------- dotted center + borders ----------


def _stroke_connector_patches(surface: pygame.Surface, meta_rot: dict, length: int = 8):
    for c in meta_rot["connectors"]:
        t = Vec2(c["tangent"])
        t = t if t.length_squared() == 0 else t.normalize()
        n = Vec2(-t.y, t.x)
        p0 = Vec2(c["pos"])
        a0 = _to_surface(p0 + n * HALF_W)
        a1 = _to_surface(p0 + n * HALF_W + t * length)
        b0 = _to_surface(p0 - n * HALF_W)
        b1 = _to_surface(p0 - n * HALF_W + t * length)
        pygame.draw.line(surface, COL_BORDER, a0, a1, BORDER_PX)
        pygame.draw.line(surface, COL_BORDER, b0, b1, BORDER_PX)


def _stroke_offset_borders(
    surface: pygame.Surface, cl_pts: list, width: int = BORDER_PX
):
    if not cl_pts:
        return
    arc_o, arc_i = [], []
    npts = len(cl_pts)
    for i in range(npts):
        p = cl_pts[i]
        p_prev = cl_pts[i - 1] if i > 0 else cl_pts[i]
        p_next = cl_pts[i + 1] if i + 1 < npts else cl_pts[i]
        t = p_next - p_prev
        if t.length_squared() <= 1e-9:
            t = Vec2(1, 0)
        else:
            t = t.normalize()
        n = Vec2(-t.y, t.x)
        arc_o.append(_to_surface(p + n * HALF_W))
        arc_i.append(_to_surface(p - n * HALF_W))
    pygame.draw.lines(surface, COL_BORDER, False, arc_o, width)
    pygame.draw.lines(surface, COL_BORDER, False, arc_i, width)


def _polyline_length(pts: list[Vec2]) -> float:
    if len(pts) < 2:
        return 0.0
    L = 0.0
    for i in range(len(pts) - 1):
        L += (pts[i + 1] - pts[i]).length()
    return L


def _point_at_distance_on_polyline(pts: list[Vec2], s: float) -> Vec2:
    if not pts:
        return Vec2(0, 0)
    if len(pts) == 1:
        return Vec2(pts[0])

    total = 0.0
    seglens = []
    for i in range(len(pts) - 1):
        L = (pts[i + 1] - pts[i]).length()
        seglens.append(L)
        total += L
    s = max(0.0, min(s, total))

    d = 0.0
    last = len(pts) - 2
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = seglens[i]
        if s <= d + seg or i == last:
            if seg <= 1e-6:
                return Vec2(a)
            t = (s - d) / seg
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            return a.lerp(b, t)
        d += seg
    return Vec2(pts[-1])


def _stroke_dotted_centerline(
    surface: pygame.Surface,
    cl_pts: list[Vec2],
    dash_px: int = 18,
    gap_px: int = 14,
    width: int = 4,
):
    if len(cl_pts) < 2:
        return
    total = _polyline_length(cl_pts)
    if total <= 1.0:
        return
    s = 0.0
    cycle = float(dash_px + gap_px)
    while s < total - 1e-6:
        a_world = _point_at_distance_on_polyline(cl_pts, s)
        b_world = _point_at_distance_on_polyline(cl_pts, min(s + dash_px, total))
        pygame.draw.line(
            surface, COL_CENTER, _to_surface(a_world), _to_surface(b_world), width
        )
        s += cycle


def _draw_finish_band(surface: pygame.Surface, meta_rot: dict):
    """Draw a checkered finish band centered on gate (if present)."""
    gate = meta_rot.get("gate")
    if not gate:
        return
    a = Vec2(gate["a"])
    b = Vec2(gate["b"])
    mid = (a + b) * 0.5
    g = b - a
    gl = max(1e-6, g.length())
    g /= gl
    t = Vec2(gate.get("tangent", Vec2(1, 0)))
    n = Vec2(-g.y, g.x)
    if n.dot(t) < 0:
        n = -n
    band_h = 18
    half = band_h * 0.5
    squares_along = 8
    for i in range(-squares_along // 2, squares_along // 2):
        p0 = mid + g * (i * (gl / squares_along))
        for j in (-1, +1):
            c = p0 + n * (j * half)
            u0 = _to_surface(c - g * (gl / squares_along * 0.5) - n * half)
            u1 = _to_surface(c + g * (gl / squares_along * 0.5) + n * half)
            rect = pygame.Rect(
                min(u0[0], u1[0]),
                min(u0[1], u1[1]),
                abs(u1[0] - u0[0]) + 1,
                abs(u1[1] - u0[1]) + 1,
            )
            col = COL_FLAG_W if (i + (0 if j < 0 else 1)) % 2 == 0 else COL_FLAG_B
            pygame.draw.rect(surface, col, rect)


def _draw_tile_to(surface: pygame.Surface, meta_rot: dict):
    surface.fill(COL_GRASS)
    pts = [_to_surface(p) for p in meta_rot["road_poly"]]
    if len(pts) >= 3:
        pygame.draw.polygon(surface, COL_ROADF, pts, 0)
    if meta_rot.get("id") == "FINISH":
        _draw_finish_band(surface, meta_rot)
    _stroke_dotted_centerline(
        surface, meta_rot["centerline"], dash_px=18, gap_px=14, width=4
    )
    _stroke_offset_borders(surface, meta_rot["centerline"], BORDER_PX)
    _stroke_connector_patches(surface, meta_rot)


# ---------- TileAtlas ----------


class TileAtlas:
    def __init__(self):
        self.meta = {}
        for tid, base in BASE_META.items():
            for rot in ROTS:
                self.meta[(tid, rot)] = self._rotate_meta(base, rot)

        rows = len(TILE_IDS)
        cols = 4
        self.surface = pygame.Surface(
            (cols * TILE_SIZE, rows * TILE_SIZE), flags=pygame.SRCALPHA
        )
        self.rects = {}

        for r, tid in enumerate(TILE_IDS):
            for c, rot in enumerate(ROTS):
                rect = pygame.Rect(c * TILE_SIZE, r * TILE_SIZE, TILE_SIZE, TILE_SIZE)
                subs = self.surface.subsurface(rect)
                _draw_tile_to(subs, self.meta[(tid, rot)])
                self.rects[(tid, rot)] = rect

    def _rotate_meta(self, base: dict, rot: int) -> dict:
        out = {"id": base["id"]}
        out["road_poly"] = [_rot_point(p, rot) for p in base["road_poly"]]
        out["centerline"] = [_rot_point(p, rot) for p in base["centerline"]]
        conns = []
        for c in base["connectors"]:
            conns.append(
                {
                    "name": _rot_dir(c["name"], rot),
                    "pos": _rot_point(c["pos"], rot),
                    "dir": _rot_dir(c["dir"], rot),
                    "tangent": _rot_vec(c["tangent"], rot),
                }
            )
        out["connectors"] = conns
        if "gate" in base:
            g = base["gate"]
            out["gate"] = {
                "a": _rot_point(g["a"], rot),
                "b": _rot_point(g["b"], rot),
                "tangent": _rot_vec(g["tangent"], rot),
            }
        return out

    def get_sprite(self, tile_id: str, rot: int):
        return self.surface, self.rects[(tile_id, rot)]

    def get_meta(self, tile_id: str, rot: int) -> dict:
        return self.meta[(tile_id, rot)]

    def list_tiles(self):
        return [(tid, ROTS) for tid in TILE_IDS]

    def gate_world(self, tile_id: str, rot: int, gx: int, gy: int):
        """Return (a_world, b_world, tangent_world) for tiles with a gate; else None."""
        meta = self.get_meta(tile_id, rot)
        g = meta.get("gate")
        if not g:
            return None
        off = Vec2(gx * TILE_SIZE + TILE_SIZE * 0.5, gy * TILE_SIZE + TILE_SIZE * 0.5)
        a = Vec2(g["a"]) + off
        b = Vec2(g["b"]) + off
        t = Vec2(g["tangent"])
        return (a, b, t)


# ---------- Top-down vector Kart (renderer only) ----------

OUTLINE = (20, 20, 24)
FLOOR_PAN = (42, 42, 46)
FLOOR_PAN_HI = (80, 80, 88)
TIRE_COL = (14, 14, 18)
REGEN_CYAN = (111, 211, 255)

PLAYER_BASE = (61, 165, 255)
PLAYER_DARK = (37, 107, 168)
PLAYER_STRP = (255, 233, 0)

ALT_BASE = (255, 159, 26)
ALT_DARK = (198, 113, 0)
ALT_STRP = (74, 123, 230)


def _rot_apply(p: Vec2, cs: float, sn: float) -> Vec2:
    return Vec2(p.x * cs - p.y * sn, p.x * sn + p.y * cs)


def _rect_poly_local(center: Vec2, w: float, h: float):
    hw, hh = w * 0.5, h * 0.5
    return [
        Vec2(center.x - hw, center.y - hh),
        Vec2(center.x + hw, center.y - hh),
        Vec2(center.x + hw, center.y + hh),
        Vec2(center.x - hw, center.y + hh),
    ]


def _poly_to_screen(local_pts, world_pos: Vec2, cs: float, sn: float, cam_tl: Vec2):
    out = []
    for lp in local_pts:
        wp = _rot_apply(lp, cs, sn) + world_pos
        sp = wp - cam_tl
        out.append((int(sp.x), int(sp.y)))
    return out


def _rect_to_screen(
    center_local: Vec2,
    w: float,
    h: float,
    world_pos: Vec2,
    cs: float,
    sn: float,
    cam_tl: Vec2,
):
    return _poly_to_screen(
        _rect_poly_local(center_local, w, h), world_pos, cs, sn, cam_tl
    )


class Kart:
    """Top-down kart renderer; physics is handled by game.py (we just draw)."""

    def __init__(self, p: Vec2, heading_rad: float, scheme="player"):
        self.p = Vec2(p)
        self.h = heading_rad
        self.v = 0.0
        self.prev = Vec2(p)
        self.steer_vis = 0.0
        self.brake_vis = 0.0

        if scheme == "alt":
            self.col_base = ALT_BASE
            self.col_dark = ALT_DARK
            self.col_strp = ALT_STRP
        else:
            self.col_base = PLAYER_BASE
            self.col_dark = PLAYER_DARK
            self.col_strp = PLAYER_STRP

        self.L = 48.0
        self.W = 30.0
        self.TW = 0.34 * self.W
        self.TL = 0.22 * self.L
        self.front_y = 0.40 * self.W
        self.front_x = 0.22 * self.L
        self.rear_y = 0.40 * self.W
        self.rear_x = -0.40 * self.L
        self.max_steer_visual = math.radians(25.0)

    def draw(self, screen: pygame.Surface, cam_topleft: Vec2):
        cs, sn = math.cos(self.h), math.sin(self.h)
        L, W = self.L, self.W

        roll_px = max(-2.0, min(2.0, -self.steer_vis * 0.5))
        lat = Vec2(-sn, cs)
        body_offset_world = lat * roll_px

        # shadow
        sh_w = W * 1.1
        sh_h = W * 0.6
        shadow_surf = pygame.Surface((int(sh_w) + 4, int(sh_h) + 4), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow_surf, (0, 0, 0, 70), shadow_surf.get_rect())
        sh_pos = self.p - cam_topleft - Vec2(sh_w * 0.5, sh_h * 0.5) + Vec2(0, 3)
        screen.blit(shadow_surf, (int(sh_pos.x), int(sh_pos.y)))

        # floor pan
        pan_size = Vec2(0.92 * L, 0.90 * W)
        pan_ctr = Vec2(-0.05 * L, 0.0)
        pan_poly = _rect_to_screen(
            pan_ctr,
            pan_size.x,
            pan_size.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pygame.draw.polygon(screen, FLOOR_PAN, pan_poly)

        # side pods
        pods_size = Vec2(0.48 * L, 0.34 * W)
        pod_c_l = Vec2(-0.05 * L, -0.46 * W)
        pod_c_r = Vec2(-0.05 * L, +0.46 * W)
        pod_l = _rect_to_screen(
            pod_c_l,
            pods_size.x,
            pods_size.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pod_r = _rect_to_screen(
            pod_c_r,
            pods_size.x,
            pods_size.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pygame.draw.polygon(screen, self.col_dark, pod_l)
        pygame.draw.polygon(screen, self.col_dark, pod_r)

        # nose
        nose_w = 0.55 * W
        nose_base_x = +0.10 * L
        nose_tip_x = +0.50 * L
        nose_pts_local = [
            Vec2(nose_base_x, -nose_w * 0.5),
            Vec2(nose_base_x, +nose_w * 0.5),
            Vec2(nose_tip_x, 0.0),
        ]
        nose_poly = _poly_to_screen(
            nose_pts_local, self.p + body_offset_world, cs, sn, cam_topleft
        )
        pygame.draw.polygon(screen, self.col_dark, nose_poly)

        # rear bumper
        bump_ctr = Vec2(-0.50 * L, 0.0)
        bump_sz = Vec2(0.10 * L, 0.95 * W)
        bump_poly = _rect_to_screen(
            bump_ctr,
            bump_sz.x,
            bump_sz.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pygame.draw.polygon(screen, FLOOR_PAN, bump_poly)

        # seat
        seat_top_x = -0.02 * L
        seat_bot_x = -0.28 * L
        seat_top_w = 0.45 * W
        seat_bot_w = 0.32 * W
        seat_pts_local = [
            Vec2(seat_top_x, -seat_top_w * 0.5),
            Vec2(seat_top_x, +seat_top_w * 0.5),
            Vec2(seat_bot_x, +seat_bot_w * 0.5),
            Vec2(seat_bot_x, -seat_bot_w * 0.5),
        ]
        seat_poly = _poly_to_screen(
            seat_pts_local, self.p + body_offset_world, cs, sn, cam_topleft
        )
        pygame.draw.polygon(screen, FLOOR_PAN_HI, seat_poly)

        # center spine (body color)
        spine_ctr = Vec2(-0.05 * L, 0.0)
        spine_sz = Vec2(0.60 * L, 0.50 * W)
        spine_poly = _rect_to_screen(
            spine_ctr,
            spine_sz.x,
            spine_sz.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pygame.draw.polygon(screen, self.col_base, spine_poly)

        # stripes
        stripe_w = 0.12 * W
        stripe_ctr1 = Vec2(+0.05 * L, 0.0)
        stripe_ctr2 = Vec2(-0.20 * L, 0.0)
        stripe_sz1 = Vec2(0.40 * L, stripe_w)
        stripe_sz2 = Vec2(0.30 * L, stripe_w)
        st1 = _rect_to_screen(
            stripe_ctr1,
            stripe_sz1.x,
            stripe_sz1.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        st2 = _rect_to_screen(
            stripe_ctr2,
            stripe_sz2.x,
            stripe_sz2.y,
            self.p + body_offset_world,
            cs,
            sn,
            cam_topleft,
        )
        pygame.draw.polygon(screen, (255, 233, 0), st1)
        pygame.draw.polygon(screen, (255, 233, 0), st2)

        # tires (rectangles): front rotate by steer_vis, rear aligned with body
        steer_ang = max(
            -self.max_steer_visual,
            min(self.max_steer_visual, self.steer_vis * self.max_steer_visual),
        )
        csf, snf = math.cos(self.h + steer_ang), math.sin(self.h + steer_ang)
        csr, snr = cs, sn
        f_left = Vec2(self.front_x, -self.front_y)
        f_right = Vec2(self.front_x, +self.front_y)
        r_left = Vec2(self.rear_x, -self.rear_y)
        r_right = Vec2(self.rear_x, +self.rear_y)
        for center, tcs, tsn in [
            (f_left, csf, snf),
            (f_right, csf, snf),
            (r_left, csr, snr),
            (r_right, csr, snr),
        ]:
            poly = _rect_to_screen(
                center, self.TL, self.TW, self.p, tcs, tsn, cam_topleft
            )
            pygame.draw.polygon(screen, TIRE_COL, poly)
            pygame.draw.polygon(screen, OUTLINE, poly, 1)

        # outlines
        for poly in (
            pan_poly,
            pod_l,
            pod_r,
            nose_poly,
            bump_poly,
            seat_poly,
            spine_poly,
            st1,
            st2,
        ):
            pygame.draw.polygon(screen, OUTLINE, poly, 1)

        # regen bar (under bumper)
        if self.brake_vis > 0.02:
            bar_len = (0.50 * W + 40.0) * self.brake_vis
            bar_h = 4.0
            bar_ctr_local = Vec2(-0.56 * L, 0.0)
            bar_pts_local = _rect_poly_local(bar_ctr_local, bar_len, bar_h)
            bar_poly = _poly_to_screen(bar_pts_local, self.p, cs, sn, cam_topleft)
            pygame.draw.polygon(screen, REGEN_CYAN, bar_poly)
