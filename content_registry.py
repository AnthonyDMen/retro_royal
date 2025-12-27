import pygame


def load_game_fonts():
    """Return (big, medium, small) default fonts."""
    if not pygame.font.get_init():
        pygame.font.init()

    try:
        big = pygame.font.Font(None, 48)
        medium = pygame.font.Font(None, 32)
        small = pygame.font.Font(None, 20)
        return big, medium, small
    except Exception as e:
        print(f"[FontLoader] Warning: {e}")
        f = pygame.font.Font(None, 24)
        return f, f, f
