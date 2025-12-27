import pygame
from scene_manager import SceneManager
from main_menu import MainMenu


def main():
    pygame.init()
    manager = SceneManager(MainMenu)
    manager.run()


if __name__ == "__main__":
    main()
