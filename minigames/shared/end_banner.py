"""Reusable end-of-match banner overlay for minigames."""

from __future__ import annotations

import pygame

DEFAULT_TITLES = {
    "win": "Victory!",
    "lose": "Defeat",
    "draw": "Match Complete",
    None: "Match Complete",
}


class EndBanner:
    def __init__(self, duration: float = 2.0, titles: dict | None = None):
        self.duration = duration
        self.titles = {**DEFAULT_TITLES, **(titles or {})}
        self.active = False
        self.timer = 0.0
        self.outcome = None
        self.title = ""
        self.subtitle = ""

    def show(self, outcome: str, title: str | None = None, subtitle: str | None = None):
        self.outcome = outcome
        self.title = title or self.titles.get(outcome, self.titles[None])
        self.subtitle = subtitle or ""
        self.timer = self.duration
        self.active = True

    def cancel(self):
        self.active = False
        self.timer = 0.0

    def skip(self):
        if self.active:
            self.timer = 0.0

    def update(self, dt: float) -> bool:
        if not self.active:
            return False
        self.timer -= dt
        if self.timer <= 0:
            self.active = False
            return True
        return False

    def draw(
        self,
        screen: pygame.Surface,
        font_big: pygame.font.Font,
        font_small: pygame.font.Font,
        size: tuple[int, int],
    ):
        if not self.active:
            return
        w, h = size
        dim = pygame.Surface(size, pygame.SRCALPHA)
        dim.fill((0, 0, 0, 180))
        screen.blit(dim, (0, 0))
        title = font_big.render(self.title, True, (255, 235, 160))
        screen.blit(title, title.get_rect(center=(w // 2, h // 2 - 20)))
        if self.subtitle:
            sub = font_small.render(self.subtitle, True, (230, 240, 250))
            screen.blit(sub, sub.get_rect(center=(w // 2, h // 2 + 18)))
