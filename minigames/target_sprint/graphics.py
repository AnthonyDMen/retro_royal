# minigames/target_sprint/graphics.py
import math
import pygame
from types import SimpleNamespace

class VectorRenderer:
    """
    Vector-only renderer for Target Sprint.
    - No RNG here (determinism preserved).
    - UI scales to window size.
    - Golden targets get a subtle pulse ring.
    - Reload bar, banners, streak badges, accuracy, lanes, etc.
    """
    def __init__(self, screen, config):
        self.screen = screen
        self.cfg = config
        self._compute_ui_scale()

        pygame.font.init()
        # Two font sizes: regular HUD + banner / badge
        base = max(12, int(14 * self.ui_scale))
        big = max(20, int(36 * self.ui_scale))
        # Prefer monospace feel for timer/score
        self.font = pygame.font.SysFont("consolas,dejavusansmono,menlo", base, bold=False)
        self.font_small = pygame.font.SysFont("consolas,dejavusansmono,menlo", max(10, base - 2))
        self.font_big = pygame.font.SysFont("consolas,dejavusansmono,menlo", big, bold=True)

        # Colors (kept here so renderer is self-contained)
        self.COL = SimpleNamespace(
            bg=self.cfg.get("BG_COLOR", (14, 14, 17)),
            lane=(28, 28, 34),

            large=(102, 194, 255),
            medium=(125, 255, 122),
            small=(255, 122, 122),
            gold=(255, 216, 77),

            rim=(240, 240, 245),
            hud=(230, 230, 238),
            dim=(155, 155, 168),
            reload_bar=(200, 200, 240),
            spark=(255, 255, 255),
            shadow=(0, 0, 0),
        )

        # Precompute some UI metrics
        self.safe = int(self.cfg.get("SAFE_MARGIN", 24) * self.ui_scale)
        self.lane_count = int(self.cfg.get("LANE_COUNT", 6))

        # Cached rect for convenience
        self.bounds = self.screen.get_rect()

    # ---------------------
    # Public draw functions
    # ---------------------

    def draw_background(self):
        self.screen.fill(self.COL.bg)
        # subtle lane guides
        w, h = self.screen.get_size()
        for i in range(max(1, self.lane_count)):
            y = self._lane_y_from_index(i)
            pygame.draw.line(self.screen, self.COL.lane, (0, y), (w, y), 1)

    def draw_targets(self, targets, t_now):
        """
        targets: iterable of objects with:
            - pos: Vector2(x,y)
            - radius: float
            - golden: bool
            - size_key: "L"|"M"|"S" or "GOLD"
            - spawn_time: float (wave-relative)
        """
        for t in targets:
            if t.golden or t.size_key == "GOLD":
                fill = self.COL.gold
            else:
                if t.size_key == "L":
                    fill = self.COL.large
                elif t.size_key == "M":
                    fill = self.COL.medium
                else:
                    fill = self.COL.small

            x, y = int(t.pos.x), int(t.pos.y)
            r = int(t.radius)

            # filled disc + thin rim for readability
            pygame.draw.circle(self.screen, fill, (x, y), r)
            pygame.draw.circle(self.screen, self.COL.rim, (x, y), r, max(1, int(2 * self.ui_scale)))

            # Golden pulsing ring (non-blocking, cosmetic)
            if t.golden or t.size_key == "GOLD":
                age = max(0.0, t_now - getattr(t, "spawn_time", 0.0))
                pulse = 0.9 + 0.2 * math.sin(age * 6.0)
                ring_r = int(r * (1.25 * pulse))
                ring_w = max(1, int(2 * self.ui_scale))
                pygame.draw.circle(self.screen, fill, (x, y), ring_r, ring_w)

    def draw_crosshair(self, pos):
        x, y = int(pos.x), int(pos.y)
        # Hollow ring + cross lines; light alpha effect via two thicknesses
        r = max(8, int(10 * self.ui_scale))
        lw = max(1, int(1 * self.ui_scale))
        pygame.draw.circle(self.screen, self.COL.hud, (x, y), r, lw)
        pygame.draw.line(self.screen, self.COL.hud, (x - int(12 * self.ui_scale), y), (x + int(12 * self.ui_scale), y), lw)
        pygame.draw.line(self.screen, self.COL.hud, (x, y - int(12 * self.ui_scale)), (x, y + int(12 * self.ui_scale)), lw)

    def draw_sparks(self, sparks, t_now):
        for s in sparks:
            age = t_now - s.t0
            a = max(0.0, 1.0 - age / s.life)
            if a <= 0:
                continue
            x, y = s.pos
            blade_count = 8
            inner = 3 * self.ui_scale
            outer = max(8 * self.ui_scale, 10 * self.ui_scale * a)
            w = max(1, int(2 * self.ui_scale))
            for i in range(blade_count):
                ang = i * (math.tau / blade_count)
                x0 = x + inner * math.cos(ang)
                y0 = y + inner * math.sin(ang)
                x1 = x + outer * math.cos(ang)
                y1 = y + outer * math.sin(ang)
                pygame.draw.line(self.screen, self.COL.spark, (x0, y0), (x1, y1), w)

    def draw_hud(self, gs_view, wave_info, accuracy, quota_text):
        """
        gs_view: SimpleNamespace(
          time_left, score, wave, mag, reserve,
          is_reloading, reload_frac, streak,
          banner_text (or None), streak_badge (or None)
        )
        wave_info: (current_wave, total, current_quota, target_quota)
        accuracy: (shots_fired, hits, percent) or None
        quota_text: preformatted "W2/3 • 1,140/1,200"
        """
        # ---- Timer (top-left) ----
        t = max(0.0, gs_view.time_left)
        mm = int(t // 60)
        ss = int(t % 60)
        cs = int((t - int(t)) * 100)  # centiseconds
        timer_text = f"{mm:02d}:{ss:02d}.{cs:02d}"
        self._blit_text(timer_text, (10, 8), self.COL.hud, align="topleft", font=self.font)

        # ---- Score + quota (top-right) ----
        score_text = f"{gs_view.score:,}  •  {quota_text}"
        self._blit_text(score_text, (self.bounds.width - 12, 8), self.COL.hud, align="topright", font=self.font)

        # ---- Accuracy (under top-right) ----
        if accuracy:
            shots, hits, pct = accuracy
            acc_text = f"{hits}/{shots} • {pct:.0f}%"
            self._blit_text(acc_text, (self.bounds.width - 12, 8 + self._line_h()), self.COL.dim, align="topright", font=self.font_small)

        # ---- Ammo + reload (bottom-right) ----
        ammo_text = f"{gs_view.mag}/{gs_view.reserve}"
        self._blit_text(ammo_text, (self.bounds.width - 12, self.bounds.height - (self._line_h() + 10)), self.COL.hud, align="topright", font=self.font)

        if gs_view.is_reloading:
            frac = max(0.0, min(1.0, gs_view.reload_frac))
            w = int(160 * self.ui_scale)
            h = max(6, int(8 * self.ui_scale))
            x = self.bounds.width - w - 12
            y = self.bounds.height - (h + 12)
            pygame.draw.rect(self.screen, self.COL.dim, (x, y, w, h), 1)
            pygame.draw.rect(self.screen, self.COL.reload_bar, (x + 1, y + 1, int((w - 2) * frac), h - 2))

        # ---- Wave banner (brief) ----
        if getattr(gs_view, "banner_text", None):
            self._blit_center_banner(gs_view.banner_text)

        # ---- Streak badge (center) ----
        if getattr(gs_view, "streak_badge", None):
            self._blit_center_badge(gs_view.streak_badge)

    def draw_banner(self, text, alpha):
        # Optional separate banner method (not required by current game.py flow)
        if not text:
            return
        self._blit_center_banner(text, alpha=alpha)

    # -------------
    # UI utilities
    # -------------

    def _compute_ui_scale(self):
        w, h = self.screen.get_size()
        self.ui_scale = max(0.75, min(1.25, min(w, h) / 720.0))  # gentle scaling around 720p baseline

    def _lane_y_from_index(self, idx: int) -> float:
        top = self.safe
        bottom = self.bounds.height - self.safe
        if self.lane_count <= 1:
            return self.bounds.centery
        frac = idx / (self.lane_count - 1)
        return top + frac * (bottom - top)

    def _line_h(self):
        return max(16, int(20 * self.ui_scale))

    def _blit_text(self, text, pos, color, align="topleft", font=None, shadow=True):
        if font is None:
            font = self.font
        surf = font.render(str(text), True, color)
        rect = surf.get_rect()
        setattr(rect, align, (int(pos[0]), int(pos[1])))

        if shadow:
            sh = font.render(str(text), True, self.COL.shadow)
            sh_rect = sh.get_rect()
            setattr(sh_rect, align, (rect.x + 1, rect.y + 1))
            self.screen.blit(sh, sh_rect)
        self.screen.blit(surf, rect)

    def _blit_center_banner(self, text, alpha=1.0):
        # Banner at top center
        surf = self.font_big.render(str(text), True, self.COL.hud)
        surf.set_alpha(int(255 * max(0.0, min(1.0, alpha))))
        rect = surf.get_rect(center=(self.bounds.width // 2, int(64 * self.ui_scale)))
        # subtle shadow
        sh = self.font_big.render(str(text), True, self.COL.shadow)
        sh.set_alpha(int(180 * max(0.0, min(1.0, alpha))))
        sh_rect = sh.get_rect(center=(rect.centerx + 1, rect.centery + 1))
        self.screen.blit(sh, sh_rect)
        self.screen.blit(surf, rect)

    def _blit_center_badge(self, text):
        # Large streak text at screen center (brief life handled by game.py)
        surf = self.font_big.render(str(text), True, self.COL.hud)
        rect = surf.get_rect(center=(self.bounds.width // 2, self.bounds.height // 2))
        sh = self.font_big.render(str(text), True, self.COL.shadow)
        sh_rect = sh.get_rect(center=(rect.centerx + 2, rect.centery + 2))
        self.screen.blit(sh, sh_rect)
        self.screen.blit(surf, rect)
