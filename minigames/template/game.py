"""
Universal minigame template.
Ensure all minigames follow this API:
    launch(manager, context, on_exit) -> Scene
"""

import pygame
from scene_manager import Scene


class MiniGameScene(Scene):
    def __init__(self, manager, context, on_exit):
        super().__init__(manager)
        self.context = context
        self.on_exit = on_exit
        self.screen = manager.screen
        self.done = False
        self.result = None  # "win", "lose", or "draw"

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                self.finish("win")
            elif event.key == pygame.K_l:
                self.finish("lose")

    def update(self, dt):
        pass

    def draw(self):
        self.screen.fill((0, 0, 0))
        font = pygame.font.Font(None, 36)
        txt = font.render("Press W to win, L to lose (test)", True, (255, 255, 255))
        self.screen.blit(txt, (100, 200))

    def finish(self, outcome):
        self.context.last_result = {
            "minigame": "template_test",
            "outcome": outcome,
            "rewards": {"gold": 25} if outcome == "win" else {},
            "score": 0,
        }
        self.manager.pop()
        self.on_exit(self.context)

    def forfeit_from_pause(self):
        self.finish("forfeit")


def launch(manager, context, on_exit):
    """Entry point used by ArenaScene."""
    return MiniGameScene(manager, context, on_exit)
