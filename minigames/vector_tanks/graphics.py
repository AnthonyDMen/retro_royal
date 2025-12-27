# minigames/vector_tanks/graphics.py
import pygame
from pygame.math import Vector2 as V2
from typing import Tuple


def _line(surface: pygame.Surface, color, a, b, w: int):
    pygame.draw.line(surface, color, a, b, w)


def _rect_outline(surface: pygame.Surface, color, rect, w: int, radius: int = 0):
    pygame.draw.rect(surface, color, rect, width=w, border_radius=radius)


def _poly(surface: pygame.Surface, color, pts, w: int = 0):
    pygame.draw.polygon(surface, color, pts, w)


def _circle(surface: pygame.Surface, color, center, r: int, w: int = 0):
    pygame.draw.circle(surface, color, center, r, w)


def draw_field(surface: pygame.Surface, world, cam, grid_color=(42, 42, 48)):
    r = world.rect
    ir = world.inner_rect

    # Outer border
    tl = cam.world_to_screen(V2(r.left, r.top))
    tr = cam.world_to_screen(V2(r.right, r.top))
    bl = cam.world_to_screen(V2(r.left, r.bottom))
    br = cam.world_to_screen(V2(r.right, r.bottom))
    border_w = max(2, cam.scale_len(3))
    _line(surface, (170, 170, 170), tl, tr, border_w)
    _line(surface, (170, 170, 170), tr, br, border_w)
    _line(surface, (170, 170, 170), br, bl, border_w)
    _line(surface, (170, 170, 170), bl, tl, border_w)

    # Inner border ring
    itl = cam.world_to_screen(V2(ir.left, ir.top))
    itr = cam.world_to_screen(V2(ir.right, ir.top))
    ibl = cam.world_to_screen(V2(ir.left, ir.bottom))
    ibr = cam.world_to_screen(V2(ir.right, ir.bottom))
    inner_w = max(1, cam.scale_len(2))
    _line(surface, (120, 120, 140), itl, itr, inner_w)
    _line(surface, (120, 120, 140), itr, ibr, inner_w)
    _line(surface, (120, 120, 140), ibr, ibl, inner_w)
    _line(surface, (120, 120, 140), ibl, itl, inner_w)

    # Grid
    step = 160
    lw = max(1, cam.scale_len(1))
    x = (r.left // step) * step
    while x <= r.right:
        a = cam.world_to_screen(V2(x, r.top))
        b = cam.world_to_screen(V2(x, r.bottom))
        _line(surface, grid_color, a, b, lw)
        x += step
    y = (r.top // step) * step
    while y <= r.bottom:
        a = cam.world_to_screen(V2(r.left, y))
        b = cam.world_to_screen(V2(r.right, y))
        _line(surface, grid_color, a, b, lw)
        y += step

    # Section dividers inside the inner play area (obs_bounds)
    third = world.obs_bounds.w // 3
    x1 = world.obs_bounds.left + third
    x2 = world.obs_bounds.left + 2 * third
    _line(
        surface,
        (70, 70, 90),
        cam.world_to_screen(V2(x1, world.obs_bounds.top)),
        cam.world_to_screen(V2(x1, world.obs_bounds.bottom)),
        max(1, cam.scale_len(1)),
    )
    _line(
        surface,
        (70, 70, 90),
        cam.world_to_screen(V2(x2, world.obs_bounds.top)),
        cam.world_to_screen(V2(x2, world.obs_bounds.bottom)),
        max(1, cam.scale_len(1)),
    )


def draw_obstacles(surface: pygame.Surface, world, cam):
    # Rect buildings
    for r in world.rect_obstacles:
        tl = cam.world_to_screen(V2(r.left, r.top))
        w = max(1, cam.scale_len(r.w))
        h = max(1, cam.scale_len(r.h))
        _rect_outline(
            surface,
            (180, 180, 200),
            pygame.Rect(tl[0], tl[1], w, h),
            max(1, cam.scale_len(2)),
            radius=4,
        )

    # Circles (rocks/trees)
    for c, rad in world.circle_obstacles:
        _circle(
            surface,
            (180, 180, 200),
            cam.world_to_screen(c),
            max(2, cam.scale_len(rad)),
            max(1, cam.scale_len(2)),
        )


def draw_tank(
    surface: pygame.Surface,
    tank,
    hull_w: int,
    hull_h: int,
    turret_w: int,
    turret_h: int,
    barrel_len: int,
    cam,
    color,
    barrel_thickness: int = 8,
    flash: float = 0.0,
):
    """Rectangular hull + turret + thick rectangular barrel + optional red flash overlay."""
    f = tank.fwd
    r = V2(-f.y, f.x)

    # Hull outline
    hw, hh = hull_w * 0.5, hull_h * 0.5
    c = tank.pos
    hull_pts = [
        c + f * hh + r * hw,
        c + f * hh - r * hw,
        c - f * hh - r * hw,
        c - f * hh + r * hw,
    ]
    hull_pts_s = [cam.world_to_screen(p) for p in hull_pts]
    _poly(surface, color, hull_pts_s, max(1, cam.scale_len(2)))

    # Tracks
    track_offset = hw * 0.85
    a1 = cam.world_to_screen(c + r * track_offset + f * hh)
    b1 = cam.world_to_screen(c + r * track_offset - f * hh)
    a2 = cam.world_to_screen(c - r * track_offset + f * hh)
    b2 = cam.world_to_screen(c - r * track_offset - f * hh)
    _line(surface, color, a1, b1, max(1, cam.scale_len(2)))
    _line(surface, color, a2, b2, max(1, cam.scale_len(2)))

    # Turret
    tw, th = turret_w * 0.5, turret_h * 0.5
    turret_center = c + f * (0.12 * hull_h)
    t_pts = [
        turret_center + f * th + r * tw,
        turret_center + f * th - r * tw,
        turret_center - f * th - r * tw,
        turret_center - f * th + r * tw,
    ]
    _poly(
        surface,
        color,
        [cam.world_to_screen(p) for p in t_pts],
        max(1, cam.scale_len(2)),
    )

    # Barrel rectangle (thick)
    half = barrel_thickness * 0.5
    base = turret_center + f * th
    tip = base + f * barrel_len
    bpts = [base + r * half, base - r * half, tip - r * half, tip + r * half]
    pts = [cam.world_to_screen(p) for p in bpts]
    _poly(surface, color, pts)
    _poly(surface, color, pts, max(1, cam.scale_len(1)))

    # Flash overlay (red, fades by flash param)
    if flash > 0.0:
        alpha = max(0, min(180, int(255 * (flash / 0.25))))
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        pygame.draw.polygon(overlay, (255, 60, 60, alpha), hull_pts_s, 0)
        surface.blit(overlay, (0, 0))

    # Hatch
    pygame.draw.circle(
        surface,
        color,
        cam.world_to_screen(turret_center - r * (tw * 0.45)),
        max(2, cam.scale_len(3)),
    )


def draw_shells(surface: pygame.Surface, shells, color, radius: int, cam):
    rr = max(1, cam.scale_len(radius))
    for s in shells:
        pygame.draw.circle(surface, color, cam.world_to_screen(s.pos), rr)


def draw_particles(surface: pygame.Surface, particles, cam):
    for p in particles:
        if p.life <= 0.0:
            continue
        t = max(0.0, min(1.0, p.life / p.maxlife))
        size = max(1, int(p.size * (0.6 + 0.4 * t) * cam.zoom))
        col = (
            int(p.color[0] * (0.4 + 0.6 * t)),
            int(p.color[1] * (0.4 + 0.6 * t)),
            int(p.color[2] * (0.4 + 0.6 * t)),
        )
        pygame.draw.circle(surface, col, cam.world_to_screen(p.pos), size)


def draw_tank_hp_pips(surface: pygame.Surface, tank, cam, hp: int, hp_max: int):
    base = tank.pos + V2(0, -max(50, hp_max * 8))
    gap = 12
    r = 4
    start = base - V2((hp_max - 1) * gap * 0.5, 0)
    for i in range(hp_max):
        c = cam.world_to_screen(start + V2(i * gap, 0))
        if i < hp:
            pygame.draw.circle(surface, (230, 230, 240), c, max(2, cam.scale_len(r)))
        else:
            pygame.draw.circle(
                surface,
                (80, 80, 90),
                c,
                max(2, cam.scale_len(r)),
                max(1, cam.scale_len(2)),
            )


def draw_hud(
    surface: pygame.Surface,
    reload_frac: float,
    p_wins: int = 0,
    e_wins: int = 0,
    wins_to: int = 3,
):
    w, h = surface.get_size()
    # Score pips top center
    pip_r = 6
    gap = 18
    total_w = (wins_to * 2 - 1) * gap
    cx = w // 2
    cy = 28
    for i in range(wins_to):
        x = cx - total_w // 2 + i * gap
        col = (0, 220, 220) if i < p_wins else (60, 90, 100)
        pygame.draw.circle(surface, col, (x, cy), pip_r, 0)
    for i in range(wins_to):
        x = cx - total_w // 2 + i * gap
        col = (220, 0, 220) if i < e_wins else (90, 60, 100)
        pygame.draw.circle(surface, col, (x, cy + 18), pip_r, 0)

    # Controls + reload bar bottom-left
    font = pygame.font.SysFont(None, 22)
    tips = "W/S throttle, A/D rotate, Space fire, +/- zoom, Esc to pause"
    surface.blit(font.render(tips, True, (210, 210, 210)), (16, h - 28))

    bar_w, bar_h = 220, 10
    x, y = 16, h - 46
    pygame.draw.rect(
        surface, (80, 80, 90), pygame.Rect(x, y, bar_w, bar_h), border_radius=3
    )
    fill_w = int(bar_w * reload_frac)
    if fill_w > 0:
        pygame.draw.rect(
            surface, (0, 220, 220), pygame.Rect(x, y, fill_w, bar_h), border_radius=3
        )
    label = pygame.font.SysFont(None, 18).render("Reload", True, (160, 160, 170))
    surface.blit(label, (x, y - 16))


def draw_banner(surface: pygame.Surface, title: str, subtext: str | None = None):
    w, h = surface.get_size()
    font_big = pygame.font.SysFont(None, 54)
    title_surf = font_big.render(title, True, (240, 240, 240))
    rect = title_surf.get_rect(center=(w // 2, int(h * 0.08)))
    pad = 10
    bg = pygame.Rect(
        rect.left - pad, rect.top - pad, rect.width + 2 * pad, rect.height + 2 * pad
    )
    pygame.draw.rect(surface, (20, 20, 28), bg, border_radius=10)
    pygame.draw.rect(surface, (180, 180, 200), bg, width=2, border_radius=10)
    surface.blit(title_surf, rect)


def draw_minimap(surface: pygame.Surface, world, player, enemy):
    """Bottom-right minimap: world rect and two dots for tank positions."""
    sw, sh = surface.get_size()
    margin = 12
    map_w, map_h = 220, 140
    x0 = sw - map_w - margin
    y0 = sh - map_h - margin
    # frame
    frame = pygame.Rect(x0, y0, map_w, map_h)
    pygame.draw.rect(surface, (20, 20, 28), frame, border_radius=8)
    pygame.draw.rect(surface, (160, 160, 180), frame, width=2, border_radius=8)

    # map area inside with small padding
    pad = 6
    inner = pygame.Rect(x0 + pad, y0 + pad, map_w - 2 * pad, map_h - 2 * pad)
    pygame.draw.rect(surface, (28, 28, 36), inner, border_radius=6)
    pygame.draw.rect(surface, (90, 90, 110), inner, width=1, border_radius=6)

    # project world rect -> inner
    wr = world.rect
    sx = inner.w / wr.w
    sy = inner.h / wr.h

    def world_to_minimap(p: V2) -> Tuple[int, int]:
        return (
            int(inner.left + (p.x - wr.left) * sx),
            int(inner.top + (p.y - wr.top) * sy),
        )

    # dots
    pr = world_to_minimap(player.pos)
    er = world_to_minimap(enemy.pos)
    pygame.draw.circle(surface, (0, 220, 220), pr, 4)
    pygame.draw.circle(surface, (220, 0, 220), er, 4)
