import pygame
from scene_manager import Scene

FADE_BG = (0, 0, 0, 180)  # translucent overlay


class PauseMenuScene(Scene):
    def __init__(self, manager, context, parent_scene):
        super().__init__(manager)
        self.context = context
        self.parent = parent_scene  # whatever scene is paused (arena or minigame)
        self.screen = manager.screen
        self.font_big = pygame.font.SysFont(None, 60)
        self.font_small = pygame.font.SysFont(None, 28)
        self.is_minigame = self._is_minigame_scene(parent_scene)
        self.is_sandbox = (
            (context.flags.get("mode") or "").lower() == "sandbox" if context else False
        )
        self.allow_forfeit = (
            self.is_minigame
            and self.is_sandbox
            and self._parent_can_forfeit(parent_scene)
        )
        self.options = ["Resume"]
        if self.allow_forfeit:
            self.options.append("Forfeit Minigame")
        self.options.extend(["Settings", "Main Menu"])
        self.sel = 0

    def _is_minigame_scene(self, scene):
        if not scene:
            return False
        module = getattr(scene.__class__, "__module__", "") or ""
        return module.startswith("minigames.")

    def _parent_can_forfeit(self, scene):
        if not scene:
            return False
        if callable(getattr(scene, "forfeit_from_pause", None)):
            return True
        if callable(getattr(scene, "_finalize", None)):
            return True
        if callable(getattr(scene, "finish", None)):
            return True
        return False

    # --- Input ---
    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_p):
                self.manager.pop()        # resume
            elif event.key in (pygame.K_UP, pygame.K_w):
                self.sel = (self.sel - 1) % len(self.options)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.sel = (self.sel + 1) % len(self.options)
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._activate_option()
        elif event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            w, h = self.screen.get_size()
            start_y = 180 + 28 * 4 + 40
            for i in range(len(self.options)):
                opt_y = start_y + 34 * i
                text_rect = pygame.Rect(w // 2 - 100, opt_y - 5, 200, 30)
                if text_rect.collidepoint(mx, my):
                    self.sel = i
                    break
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            w, h = self.screen.get_size()
            start_y = 180 + 28 * 4 + 40
            for i in range(len(self.options)):
                opt_y = start_y + 34 * i
                text_rect = pygame.Rect(w // 2 - 100, opt_y - 5, 200, 30)
                if text_rect.collidepoint(mx, my):
                    self.sel = i
                    self._activate_option()
                    break

    # --- Actions ---
    def _activate_option(self):
        choice = self.options[self.sel]
        if choice == "Resume":
            self.manager.pop()
        elif choice == "Forfeit Minigame":
            self._forfeit_minigame()
        elif choice == "Settings":
            from settings_menu import SettingsMenu

            self.manager.switch(SettingsMenu(self.manager, parent=self))
        elif choice == "Main Menu":
            print("[PauseMenu] Player quit the current run.")
            from main_menu import MainMenu
            self.manager.switch(MainMenu(self.manager))

    def _forfeit_minigame(self):
        if not self.allow_forfeit:
            return
        # close pause overlay first
        self.manager.pop()
        handler = getattr(self.parent, "forfeit_from_pause", None)
        if callable(handler):
            handler()
            return
        finalize = getattr(self.parent, "_finalize", None)
        if callable(finalize):
            try:
                finalize("forfeit")
                return
            except TypeError:
                try:
                    finalize()
                    return
                except Exception:
                    pass
        finish = getattr(self.parent, "finish", None)
        if callable(finish):
            try:
                finish("forfeit")
                return
            except TypeError:
                try:
                    finish()
                    return
                except Exception:
                    pass
        print("[PauseMenu] Forfeit requested but parent scene has no handler.")

    # --- No updates while paused ---
    def update(self, dt):
        pass

    # --- Draw overlay ---
    def draw(self):
        self.parent.draw()  # draw the paused scene underneath
        fade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        fade.fill(FADE_BG)
        self.screen.blit(fade, (0, 0))

        title = self.font_big.render("Paused", True, (255, 255, 200))
        self.screen.blit(title, title.get_rect(center=(480, 100)))

        # Stats block
        lines = [
            f"Wins: {self.context.stats['wins']}",
            f"Losses: {self.context.stats['losses']}",
            f"Credits: {self.context.stats['credits']}",
            f"Playtime: {int(self.context.stats['total_time'])} s",
        ]
        y = 180
        for text in lines:
            t = self.font_small.render(text, True, (230, 230, 230))
            self.screen.blit(t, (360, y))
            y += 28

        # Menu options
        y += 40
        for i, txt in enumerate(self.options):
            color = (255, 235, 140) if i == self.sel else (200, 200, 200)
            surf = self.font_small.render(txt, True, color)
            self.screen.blit(surf, (420, y))
            y += 34
