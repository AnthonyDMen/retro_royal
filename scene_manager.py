import pygame, sys, traceback


class Scene:
    """Base scene with no-op event/update/draw hooks."""

    def __init__(self, manager):
        self.manager = manager

    def handle_event(self, event):
        pass

    def update(self, dt):
        pass

    def draw(self):
        pass


class SceneManager:
    """Controls scene stack and main loop."""

    def __init__(self, first_scene_class):
        pygame.init()
        self.screen = pygame.display.set_mode((960, 540))
        self.size = self.screen.get_size()  
        pygame.display.set_caption("Retro Royale Demo")
        self.clock = pygame.time.Clock()
        self.running = True
        self.scenes = []
        self.context = None

        # Initialize first scene
        if callable(first_scene_class):
            first_scene = first_scene_class(self)
            self.scenes.append(first_scene)
        else:
            raise ValueError("First scene must be a class reference.")

    def push(self, scene):
        self.scenes.append(scene)

    def pop(self):
        if self.scenes:
            self.scenes.pop()
        if not self.scenes:
            self.running = False

    def switch(self, scene):
        if self.scenes:
            self.scenes.pop()
        self.push(scene)

    def run(self):
        """Main loop."""
        while self.running:
            dt = self.clock.tick(60) / 1000.0
            if not self.scenes:
                break
            current = self.scenes[-1]

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break
                try:
                    current.handle_event(event)
                except Exception:
                    traceback.print_exc()

            try:
                current.update(dt)
                current.draw()
            except Exception:
                traceback.print_exc()

            pygame.display.flip()

        pygame.quit()
        sys.exit()
