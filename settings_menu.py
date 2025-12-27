import pygame
from scene_manager import Scene
import sound_engine
from scoreboard import SCORE_DIR
import shutil


class SettingsMenu(Scene):
    def __init__(self, manager, parent=None):
        super().__init__(manager)
        self.parent = parent
        self.screen = manager.screen
        self.font = pygame.font.SysFont(None, 32)
        self.small = pygame.font.SysFont(None, 22)

        self.labels = [
            "Master Volume",
            "Music Volume",
            "SFX Volume",
            "Reset Scoreboard",
            "Back",
        ]
        self.sel = 0

        # initial values from sound_engine
        self.master = 1.0
        self.music = sound_engine.MUSIC_VOL
        self.sfx = sound_engine.SFX_VOL

    # --- events ---
    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.sel = (self.sel - 1) % len(self.labels)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.sel = (self.sel + 1) % len(self.labels)
            elif event.key == pygame.K_LEFT:
                self._adjust(-0.1)
            elif event.key == pygame.K_RIGHT:
                self._adjust(+0.1)
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._activate()
            elif event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self._go_back()
        elif event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            for i in range(len(self.labels)):
                if self._entry_rect(i).collidepoint(mx, my):
                    self.sel = i
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i in range(len(self.labels)):
                if self._entry_rect(i).collidepoint(mx, my):
                    self.sel = i
                    self._activate()

    # --- helpers ---
    def _adjust(self, delta):
        if self.sel == 0:  # master (affects both)
            self.master = max(0, min(1, self.master + delta))
            self.music = self.sfx = self.master
        elif self.sel == 1:
            self.music = max(0, min(1, self.music + delta))
        elif self.sel == 2:
            self.sfx = max(0, min(1, self.sfx + delta))
        sound_engine.MUSIC_VOL = self.music
        sound_engine.SFX_VOL = self.sfx
        pygame.mixer.music.set_volume(self.music)

    def _activate(self):
        choice = self.labels[self.sel]
        if choice == "Reset Scoreboard":
            if SCORE_DIR.exists():
                shutil.rmtree(SCORE_DIR)
            SCORE_DIR.mkdir(exist_ok=True)
            print("[Settings] Scoreboard reset.")
        elif choice == "Back":
            self._go_back()

    def _go_back(self):
        if self.parent:
            self.manager.switch(self.parent)
        else:
            from main_menu import MainMenu
            self.manager.switch(MainMenu(self.manager))

    # --- draw ---
    def _entry_rect(self, i):
        w, h = self.screen.get_size()
        return pygame.Rect(w // 2 - 180, 180 + i * 60, 360, 40)

    def draw(self):
        self.screen.fill((18, 20, 29))
        title = self.font.render("Settings", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.manager.size[0] // 2, 80)))

        for i, label in enumerate(self.labels):
            color = (255, 235, 140) if i == self.sel else (210, 210, 220)
            r = self._entry_rect(i)
            pygame.draw.rect(self.screen, (35, 42, 58), r, border_radius=8)
            pygame.draw.rect(self.screen, (100, 100, 120), r, 2, border_radius=8)
            text = label
            if label.startswith("Master"):
                text += f": {int(self.master * 100)}%"
            elif label.startswith("Music"):
                text += f": {int(self.music * 100)}%"
            elif label.startswith("SFX"):
                text += f": {int(self.sfx * 100)}%"
            surf = self.small.render(text, True, color)
            self.screen.blit(surf, surf.get_rect(center=r.center))

        hint = self.small.render(
            "Arrows / Click • Enter to select • Esc to return", True, (180, 180, 190)
        )
        self.screen.blit(
            hint, hint.get_rect(center=(self.manager.size[0] // 2, self.manager.size[1] - 30))
        )
