import pygame

# --- palette & constants ---
COLORS = {
    "bg": (10, 12, 16),
    "grid": (25, 28, 34),
    "border": (180, 185, 195),
    "player": (80, 220, 120),
    "player_head": (180, 255, 200),
    "npc": (220, 120, 80),
    "npc_head": (255, 200, 180),
    "item": (250, 230, 80),
    "hud_fg": (230, 235, 240),
    "hud_dim": (150, 156, 165),
    "turbo_ready": (120, 200, 255),
}

TOP_BAR_H = 36  # height of the top HUD bar


# --- helpers ---
def _cell_rect(rect, cell, cell_px, pad=0):
    ox, oy = rect.left, rect.top
    x, y = cell
    return pygame.Rect(
        ox + x * cell_px + pad,
        oy + y * cell_px + pad,
        cell_px - 2 * pad,
        cell_px - 2 * pad,
    )


def _center_in(rect, w, h):
    return rect.left + (rect.w - w) // 2, rect.top + (rect.h - h) // 2


def _pips(n_win, best_of):
    need = (best_of + 1) // 2
    if n_win > need:
        n_win = need
    return "+" * n_win + "-" * (need - n_win)


# --- background & playfield ---
def draw_background(screen, sw, sh):
    screen.fill(COLORS["bg"])


def draw_playfield(screen, rect, gw, gh, cell_px):
    # field background
    pygame.draw.rect(screen, (18, 20, 24), rect)

    # grid lines
    grid_col = COLORS["grid"]
    ox, oy = rect.left, rect.top
    bw, bh = gw * cell_px, gh * cell_px
    for x in range(gw + 1):
        X = ox + x * cell_px
        pygame.draw.line(screen, grid_col, (X, oy), (X, oy + bh), 1)
    for y in range(gh + 1):
        Y = oy + y * cell_px
        pygame.draw.line(screen, grid_col, (ox, Y), (ox + bw, Y), 1)

    # arena border
    pygame.draw.rect(screen, COLORS["border"], rect, width=3, border_radius=4)


# --- entities ---
def draw_items(screen, rect, items, cell_px):
    for it in items:
        r = _cell_rect(rect, it.pos, cell_px, pad=2)
        cx, cy = r.center
        pts = [(cx, r.top), (r.right, cy), (cx, r.bottom), (r.left, cy)]
        pygame.draw.polygon(screen, COLORS["item"], pts)


def draw_worm(screen, rect, worm, cell_px, color_key="player"):
    trail_col = COLORS[color_key]
    head_col = COLORS[color_key + "_head"]

    # body (all but head)
    for seg in list(worm.trail)[:-1]:
        r = _cell_rect(rect, seg, cell_px, pad=2)
        pygame.draw.rect(screen, trail_col, r, border_radius=3)

    # head
    r = _cell_rect(rect, worm.head, cell_px, pad=1)
    pygame.draw.rect(screen, head_col, r, border_radius=4)


# --- HUD ---
def draw_hud(
    screen,
    sw,
    top_y,
    *,
    score_p,
    score_n,
    best_of,
    len_p,
    turbo_p,
    turbo_n,
    controls,
    state,
    ready_until,
):
    # top bar background
    pygame.draw.rect(screen, (18, 20, 24), (0, 0, sw, TOP_BAR_H))

    # fonts
    font = pygame.font.SysFont("consolas,dejavu sans mono", 15)

    # left: title + controls
    left_text = f"TrailWorm  |  {controls}"
    t_left = font.render(left_text, True, COLORS["hud_fg"])
    screen.blit(t_left, (10, 6))

    # right: LEN + TURBO bar
    right_text = f"LEN {len_p:02d}  TURBO "
    t_right = font.render(right_text, True, COLORS["hud_fg"])
    # turbo bar sizes
    bar_w, bar_h = 100, 10
    right_total_w = t_right.get_width() + 6 + bar_w
    rx = sw - right_total_w - 10
    screen.blit(t_right, (rx, 6))

    # bar outline + fill
    bx, by = rx + t_right.get_width() + 6, 9
    pygame.draw.rect(
        screen, COLORS["hud_dim"], (bx, by, bar_w, bar_h), 1, border_radius=3
    )
    fill_w = int(bar_w * max(0.0, min(1.0, turbo_p)))
    fill_col = COLORS["turbo_ready"] if turbo_p >= 1.0 else COLORS["hud_fg"]
    if fill_w > 2:
        pygame.draw.rect(
            screen,
            fill_col,
            (bx + 1, by + 1, fill_w - 2, bar_h - 2),
            0,
            border_radius=2,
        )

    # center: score pips (clamped between left and right blocks)
    pips_text = f"P {_pips(score_p, best_of)}   N {_pips(score_n, best_of)}"
    t_center = font.render(pips_text, True, COLORS["hud_fg"])
    pad = 12
    ideal_cx = (sw - t_center.get_width()) // 2
    min_cx = t_left.get_width() + pad
    max_cx = sw - right_total_w - t_center.get_width() - pad
    cx = max(min_cx, min(ideal_cx, max_cx))
    screen.blit(t_center, (cx, 6))

    # ready countdown
    if state == "ready":
        ms = max(0, ready_until - pygame.time.get_ticks())
        txt = font.render(f"READY {ms/1000.0:0.1f}", True, COLORS["hud_fg"])
        screen.blit(txt, ((sw - txt.get_width()) // 2, TOP_BAR_H + 6))


# --- end-of-match banner ---
def draw_end_banner(screen, full_rect, text):
    # dim the whole window
    overlay = pygame.Surface((full_rect.w, full_rect.h), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 170))
    screen.blit(overlay, full_rect.topleft)

    big = pygame.font.SysFont("consolas,dejavu sans mono", 40)
    small = pygame.font.SysFont("consolas,dejavu sans mono", 18)

    t_main = big.render(text, True, COLORS["hud_fg"])
    t_sub = small.render("Returning...", True, COLORS["hud_dim"])

    x, y = _center_in(full_rect, t_main.get_width(), t_main.get_height())
    screen.blit(t_main, (x, y - 8))
    sx, sy = _center_in(full_rect, t_sub.get_width(), t_sub.get_height())
    screen.blit(t_sub, (sx, sy + 28))
