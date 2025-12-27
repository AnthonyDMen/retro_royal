from __future__ import annotations
import math, time
from typing import Dict, Tuple, Optional

import pygame


class Graphics:
    def __init__(
        self,
        cell_size: int,
        win_w: int,
        win_h: int,
        colors: Dict[str, Tuple[int, int, int]],
        number_colors: Dict[int, Tuple[int, int, int]],
        anim_reveal_s: float,
    ):
        self.cell = cell_size
        self.win_w, self.win_h = win_w, win_h
        self.colors = colors
        self.number_colors = number_colors
        self.anim_reveal_s = anim_reveal_s

        # Fonts
        pygame.font.init()
        self.hud_font = pygame.font.SysFont(
            "consolas,menlo,dejavusansmono", 28, bold=True
        )
        self.help_font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 22)
        self.num_font = pygame.font.SysFont(
            "arial,dejavusans", max(20, int(self.cell * 0.5)), bold=True
        )

        # Pre-render help text
        self.help_text = self.help_font.render(
            "LMB reveal, RMB flag, MMB/Shift chord", True, self.colors["hud_dim"]
        )

    # ------------------------- basic passes -------------------------
    def draw_background(self, surface: pygame.Surface):
        surface.fill(self.colors["bg"])

    def draw_board(
        self,
        surface: pygame.Surface,
        origin: Tuple[int, int],
        cell: int,
        model,
        hover_xy: Optional[Tuple[int, int]],
        pressed_xy: Optional[Tuple[int, int]],
        now_time: float,
    ):
        ox, oy = origin
        # board background slab
        board_rect = pygame.Rect(
            ox - 8, oy - 8, model.w * cell + 16, model.h * cell + 16
        )
        pygame.draw.rect(surface, self.colors["board_bg"], board_rect, border_radius=10)

        # grid/tiles
        for x in range(model.w):
            for y in range(model.h):
                r = pygame.Rect(ox + x * cell, oy + y * cell, cell, cell)
                c = model.cells[x][y]

                hovered = hover_xy == (x, y)
                pressed = pressed_xy == (x, y)

                if not c.revealed:
                    base = (
                        self.colors["hidden_hi"] if hovered else self.colors["hidden"]
                    )
                    # pressed nudge
                    if pressed:
                        # slight scale in
                        sr = r.inflate(-int(cell * 0.08), -int(cell * 0.08))
                        pygame.draw.rect(surface, base, sr, border_radius=6)
                    else:
                        pygame.draw.rect(surface, base, r, border_radius=6)

                    pygame.draw.rect(
                        surface, self.colors["grid"], r, width=1, border_radius=6
                    )

                    if c.flagged:
                        self._draw_flag(surface, r.centerx, r.centery)
                else:
                    # revealed
                    # subtle reveal "pop" animation based on reveal_t timestamp
                    draw_rect = r
                    if c.reveal_t >= 0:
                        t = max(0.0, now_time - c.reveal_t)
                        if t < self.anim_reveal_s:
                            k = (
                                1.0 - (t / self.anim_reveal_s) * 0.06
                            )  # shrink up to ~6%
                            dw = int(r.w * (1 - k))
                            dh = int(r.h * (1 - k))
                            draw_rect = r.inflate(-dw, -dh)

                    pygame.draw.rect(
                        surface, self.colors["revealed"], draw_rect, border_radius=4
                    )
                    pygame.draw.rect(
                        surface,
                        self.colors["outline"],
                        draw_rect,
                        width=1,
                        border_radius=4,
                    )

                    if c.mine:
                        self._draw_mine(
                            surface,
                            draw_rect.centerx,
                            draw_rect.centery,
                            detonated=c.detonated,
                        )
                    elif c.adj > 0:
                        col = self.number_colors.get(c.adj, self.number_colors[8])
                        txt = self.num_font.render(str(c.adj), True, col)
                        tr = txt.get_rect(center=draw_rect.center)
                        surface.blit(txt, tr)

        # draw detonation tint on top if any detonated
        # (already tinted per-mine, but this adds a subtle hit)
        # optional: could do a small radial pulse — keeping simple

    def draw_hud(
        self,
        surface: pygame.Surface,
        elapsed_s: int,
        mines_remaining: int,
        help_alpha: int,
        round_state: str,
        limit_s: int,
    ):
        # timer (top-left)
        mm = elapsed_s // 60
        ss = elapsed_s % 60
        if limit_s > 0:
            remaining = max(0, limit_s - elapsed_s)
            mm = remaining // 60
            ss = remaining % 60
        timer_str = f"{mm:02d}:{ss:02d}"
        t_surf = self.hud_font.render(timer_str, True, self.colors["hud"])
        surface.blit(t_surf, (18, 14))

        # mines remaining (top-right)
        m_surf = self.hud_font.render(
            f"Mines: {mines_remaining}", True, self.colors["hud"]
        )
        mr = m_surf.get_rect(top=14, right=self.win_w - 18)
        surface.blit(m_surf, mr)

        # hint at bottom center (fades after first move)
        if help_alpha > 0 and round_state in ("ready", "playing"):
            ht = self.help_text.copy()
            ht.set_alpha(help_alpha)
            hr = ht.get_rect(midbottom=(self.win_w // 2, self.win_h - 16))
            surface.blit(ht, hr)

    def draw_result_overlay(
        self, surface: pygame.Surface, state: str, post_timer: float, post_total: float
    ):
        if state not in ("post_win", "post_lose"):
            return
        # normalized 0..1 (fade out)
        t = 1.0 - max(0.0, min(1.0, post_timer / max(0.0001, post_total)))
        if state == "post_win":
            overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
            a = int(100 * (1.0 - t))  # brief flash then fade
            overlay.fill((*self.colors["win_flash"], a))
            surface.blit(overlay, (0, 0))
        else:
            # post_lose: soft red tint
            overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
            a = int(120 * (1.0 - t))
            overlay.fill(
                (
                    self.colors["det_red"][0],
                    self.colors["det_red"][1],
                    self.colors["det_red"][2],
                    a,
                )
            )
            surface.blit(overlay, (0, 0))

    # ------------------------- icons/shapes -------------------------
    def _draw_flag(self, surface: pygame.Surface, cx: int, cy: int):
        # simple pole + triangular cloth
        pole_h = int(self.cell * 0.6)
        pole_w = max(2, int(self.cell * 0.06))
        x = cx - pole_w // 2
        y = cy - pole_h // 2
        pygame.draw.rect(
            surface, self.colors["flag_pole"], (x, y, pole_w, pole_h), border_radius=2
        )
        # triangle cloth to the right
        tri_w = int(self.cell * 0.45)
        tri_h = int(self.cell * 0.32)
        p1 = (x + pole_w, y + int(self.cell * 0.08))
        p2 = (p1[0] + tri_w, p1[1] + tri_h // 2)
        p3 = (x + pole_w, y + tri_h + int(self.cell * 0.08))
        pygame.draw.polygon(surface, self.colors["flag"], (p1, p2, p3))

    def _draw_mine(self, surface: pygame.Surface, cx: int, cy: int, detonated: bool):
        r = int(self.cell * 0.20)
        core_color = self.colors["mine"]
        pygame.draw.circle(surface, core_color, (cx, cy), r)
        # spokes
        for i in range(8):
            ang = i * math.pi / 4
            x1 = cx + int(r * 0.4 * math.cos(ang))
            y1 = cy + int(r * 0.4 * math.sin(ang))
            x2 = cx + int((r + self.cell * 0.18) * math.cos(ang))
            y2 = cy + int((r + self.cell * 0.18) * math.sin(ang))
            pygame.draw.line(surface, core_color, (x1, y1), (x2, y2), width=2)
        if detonated:
            # red ring
            pygame.draw.circle(
                surface, self.colors["det_red"], (cx, cy), int(r * 1.4), width=3
            )
