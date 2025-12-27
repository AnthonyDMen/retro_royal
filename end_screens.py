import pygame
from scene_manager import Scene
from scoreboard import load_scores


FADE_SPEED = 220  # higher = faster fade


class BaseEndScene(Scene):
    """Common base for all end screens with fade transitions."""

    def __init__(self, manager, title, color, next_scene="menu"):
        super().__init__(manager)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.title = title
        self.color = color
        self.next_scene = next_scene
        self.font_big = pygame.font.SysFont(None, 64)
        self.font_small = pygame.font.SysFont(None, 32)
        self.timer = 0.0

        # fade control
        self.fade_alpha = 255  # start fully black
        self.fade_dir = -1  # -1 = fade in, +1 = fade out
        self.transitioning = False

    def handle_event(self, event):
        if self.transitioning:
            return
        if event.type == pygame.KEYDOWN or event.type == pygame.MOUSEBUTTONDOWN:
            self.fade_dir = +1
            self.transitioning = True

    def update(self, dt):
        self.timer += dt
        # handle fade
        self.fade_alpha += self.fade_dir * FADE_SPEED * dt
        if self.fade_dir < 0 and self.fade_alpha <= 0:
            self.fade_alpha = 0
        elif self.fade_dir > 0 and self.fade_alpha >= 255:
            self.fade_alpha = 255
            # switch scene after fade-out
            if self.next_scene == "menu":
                from main_menu import MainMenu

                self.manager.switch(MainMenu(self.manager))
            elif self.next_scene == "arena":
                from arena_scene import ArenaScene

                self.manager.switch(ArenaScene(self.manager))

    def draw(self):
        # background
        self.screen.fill((15, 10, 20))

        # main text
        title_surf = self.font_big.render(self.title, True, self.color)
        self.screen.blit(
            title_surf, title_surf.get_rect(center=(self.w // 2, self.h // 2 - 40))
        )

        msg = (
            "Press any key to return to menu"
            if self.next_scene == "menu"
            else "Press any key to return to arena"
        )
        sub = self.font_small.render(msg, True, (220, 220, 220))
        self.screen.blit(sub, sub.get_rect(center=(self.w // 2, self.h // 2 + 30)))

        # --- Leaderboard overlay (for Champion only) ---
        if self.title == "You Are the Champion!":
            scores = load_scores("tutor_forest")
            font_small = pygame.font.SysFont(None, 24)
            y = self.h - 200
            if scores:
                msg = font_small.render(
                    "Top 5 Champion Times:", True, (255, 240, 150)
                )
                self.screen.blit(msg, (20, y))
                y += 24
                for i, s in enumerate(scores[:5], start=1):
                    line = (
                        f"#{i} {s['total_time']:>6.1f}s  "
                        f"{s['wins']}W/{s['losses']}L  {s['credits']}cr"
                    )
                    color = (255, 255, 230) if i == 1 else (220, 220, 220)
                    self.screen.blit(font_small.render(line, True, color), (30, y))
                    y += 22
            else:
                msg = font_small.render(
                    "No champion times recorded yet.", True, (220, 220, 220)
                )
                self.screen.blit(msg, (20, y))

        # fade overlay
        if self.fade_alpha > 0:
            fade = pygame.Surface((self.w, self.h))
            fade.fill((0, 0, 0))
            fade.set_alpha(int(self.fade_alpha))
            self.screen.blit(fade, (0, 0))


# -----------------------------------------------------
# Specific Screens
# -----------------------------------------------------
class LoseScene(BaseEndScene):
    def __init__(self, manager):
        super().__init__(manager, "You Lost!", (255, 120, 120), next_scene="menu")


class SpectatorScene(BaseEndScene):
    def __init__(self, manager):
        super().__init__(
            manager,
            "Eliminated — Now Spectating",
            (200, 200, 255),
            next_scene="menu",
        )


class WinMinigameScene(BaseEndScene):
    def __init__(self, manager):
        super().__init__(
            manager, "You Won the Duel!", (120, 255, 140), next_scene="arena"
        )


class WinGameScene(BaseEndScene):
    def __init__(self, manager):
        super().__init__(
            manager, "You Are the Champion!", (255, 240, 150), next_scene="menu"
        )
