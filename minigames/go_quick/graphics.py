# minigames/go_quick/graphics.py
"""
Pure renderer for Go (Quick) — vector grid + star points + stones + last-move pip + hint pips.
No game logic or UI text here.

Public API:
    GoRenderer(n: int, side_px: int, margin: int = 40, colors: Optional[Dict] = None, stone_ratio: float = 0.33)
    .board_rect(screen_w: int, screen_h: int) -> Tuple[int,int,int,int]
    .draw(screen: pygame.Surface, board: List[List[int]], last_move: Optional[Tuple[int,int]],
          turn: int, hint_moves: List[Tuple[int,int]]) -> None
"""

from typing import Dict, List, Optional, Tuple
import pygame

Vec2 = Tuple[int, int]


class GoRenderer:
    def __init__(
        self,
        n: int,
        side_px: int,
        margin: int = 40,
        colors: Optional[Dict[str, Tuple[int, ...]]] = None,
        stone_ratio: float = 0.33,  # smaller stones, per request (was ~0.40)
    ):
        self.n = n
        self.side = side_px
        self.margin = margin
        self.colors = colors or {}
        self.stone_ratio = stone_ratio
        self.cell = 0  # set in board_rect()

    # -------- helpers --------
    def _color(self, key: str, default: Tuple[int, ...]) -> Tuple[int, ...]:
        return self.colors.get(key, default)

    def board_rect(self, screen_w: int, screen_h: int) -> Tuple[int, int, int, int]:
        """
        Compute a centered square board rect and set self.cell.

        Returns:
            (x, y, side, side) — pixel-space rect for the grid (intersections range 0..n-1)
        """
        side = min(self.side, min(screen_w, screen_h) - self.margin * 2)
        x = (screen_w - side) // 2
        y = (screen_h - side) // 2

        # grid spacing from intersections (n-1 gaps). Keep at least 16 px cells for clarity.
        gaps = max(1, self.n - 1)
        self.cell = max(16, side // gaps)
        side = self.cell * gaps
        return (x, y, side, side)

    def _star_points(self) -> List[Vec2]:
        """
        Standard 3x3 star layout for odd boards >= 9.
        Zero-based coordinates: [3-3-3 template] using indices [2, mid, n-3]
        """
        if self.n < 9 or self.n % 2 == 0:
            return []
        a = 2
        mid = (self.n - 1) // 2
        b = self.n - 3
        pts = [a, mid, b]
        out: List[Vec2] = []
        for r in pts:
            for c in pts:
                out.append((r, c))
        return out

    # -------- drawing --------
    def draw(
        self,
        screen: pygame.Surface,
        board: List[List[int]],
        last_move: Optional[Vec2],
        turn: int,
        hint_moves: List[Vec2],
    ) -> None:
        w, h = screen.get_size()
        x, y, side, _ = self.board_rect(w, h)

        bg = self._color("bg", (22, 22, 26))
        grid = self._color("grid", (210, 200, 170))
        star = self._color("star", (140, 130, 100))
        cblack = self._color("black", (24, 24, 24))
        cwhite = self._color("white", (238, 238, 238))
        clast = self._color("last", (255, 240, 120))

        screen.fill(bg)

        # grid lines
        for i in range(self.n):
            xx = x + i * self.cell
            yy = y + i * self.cell
            pygame.draw.line(screen, grid, (x, yy), (x + side, yy), 2)
            pygame.draw.line(screen, grid, (xx, y), (xx, y + side), 2)

        # star points
        for rr, cc in self._star_points():
            sx = x + cc * self.cell
            sy = y + rr * self.cell
            pygame.draw.circle(screen, star, (sx, sy), max(3, int(self.cell * 0.08)))

        # stones (smaller than typical for cleaner look)
        R = max(2, int(self.cell * self.stone_ratio))
        for r in range(self.n):
            for c in range(self.n):
                v = board[r][c]
                if not v:
                    continue
                cx = x + c * self.cell
                cy = y + r * self.cell
                if v == 1:
                    pygame.draw.circle(screen, cblack, (cx, cy), R)
                    pygame.draw.circle(screen, (0, 0, 0), (cx, cy), R, 2)
                else:
                    pygame.draw.circle(screen, cwhite, (cx, cy), R)
                    pygame.draw.circle(screen, (0, 0, 0), (cx, cy), R, 2)

        # last move marker
        if last_move is not None:
            lr, lc = last_move
            lx = x + lc * self.cell
            ly = y + lr * self.cell
            pygame.draw.circle(screen, clast, (lx, ly), max(3, int(self.cell * 0.08)))

        # hint overlays (capture suggestions)
        if hint_moves:
            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            for r, c in hint_moves:
                cx = x + c * self.cell
                cy = y + r * self.cell
                pygame.draw.circle(
                    overlay, (80, 170, 120, 70), (cx, cy), int(self.cell * 0.25)
                )
            screen.blit(overlay, (0, 0))
