import pygame
from pathlib import Path
from resource_path import resource_path

ASSET_DIR = Path(resource_path("minigames", "block_duel"))
BACKGROUND = ASSET_DIR / "background.png"

CELL = 24
GRID_W, GRID_H = 10, 20
BOARD_X, BOARD_Y = 160, 120

COLORS = {
    "I": (0, 255, 255),
    "O": (255, 255, 0),
    "T": (160, 0, 240),
    "S": (0, 255, 0),
    "Z": (255, 0, 0),
    "J": (0, 0, 255),
    "L": (255, 128, 0),
}

SHAPES = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "O": [(1, 0), (2, 0), (1, 1), (2, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)],
    "S": [(1, 1), (2, 1), (0, 2), (1, 2)],
    "Z": [(0, 1), (1, 1), (1, 2), (2, 2)],
    "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}


def load_background():
    return pygame.image.load(str(BACKGROUND)).convert()


def draw_board(surface, board, origin=None, cell_px: int = CELL):
    """Draw a grid at the given origin using the provided cell size."""
    ox, oy = origin if origin else (BOARD_X, BOARD_Y)
    for r in range(GRID_H):
        for c in range(GRID_W):
            val = board[r][c]
            if val:
                x = ox + c * cell_px
                y = oy + r * cell_px
                pygame.draw.rect(
                    surface,
                    COLORS.get(val, (200, 200, 200)),
                    (x + 1, y + 1, cell_px - 2, cell_px - 2),
                )


def draw_dispenser(surface, queue, center_x: int, scale: float = 1.0):
    """Draw 4 upcoming shared pieces floating over center dispenser."""
    box_w = int(46 * scale)
    box_h = int(42 * scale)
    gap = max(6, int(8 * scale))
    start_x = center_x - box_w // 2 - 2
    start_y = int(128 * scale)

    for i, shape in enumerate(queue[:4]):
        bx = start_x
        by = start_y + i * (box_h + gap)
        col = COLORS.get(shape, (255, 255, 255))
        for dx, dy in SHAPES[shape]:
            px = bx + 4 + dx * int(12 * scale)
            py = by + 4 + dy * int(12 * scale)
            pygame.draw.rect(surface, col, (px, py, int(12 * scale), int(12 * scale)))


def draw_next_slots(surface, player_next, enemy_next, center_x: int, scale: float = 1.0):
    """Draw player (blue) and enemy (red) next pieces in the T side boxes."""
    player_x = center_x - int(48 * scale) - int(40 * scale)
    player_y = int(120 * scale)
    if player_next:
        col = COLORS.get(player_next, (255, 255, 255))
        for dx, dy in SHAPES[player_next]:
            px = player_x + 4 + dx * int(12 * scale)
            py = player_y + 4 + dy * int(12 * scale)
            pygame.draw.rect(surface, col, (px, py, int(12 * scale), int(12 * scale)))

    enemy_x = center_x + int(48 * scale) - 4
    enemy_y = int(120 * scale)
    if enemy_next:
        col = COLORS.get(enemy_next, (255, 255, 255))
        for dx, dy in SHAPES[enemy_next]:
            px = enemy_x + 4 + dx * int(12 * scale)
            py = enemy_y + 4 + dy * int(12 * scale)
            pygame.draw.rect(surface, col, (px, py, int(12 * scale), int(12 * scale)))
