# minigames/shut_the_box/game.py
import pygame, random, itertools, time

TITLE = "Shut the Box"
MINIGAME_ID = "shut_the_box"
MULTIPLAYER_ENABLED = True

# ---------- Virtual design resolution ----------
VW, VH = 640, 480
BTN_W, BTN_H = 110, 40
BTN_SP = 12

# ---------- Game config ----------
TILES = list(range(1, 13))  # 1–12
BEST_OF = 3  # first to 2
RNG_SEED = 7331  # deterministic host seed
AI_RANDOMNESS = 0.03  # slight imperfection
AI_THINK_MS = 350  # short pause after dice settle
ALLOW_SINGLE_ANYTIME = True  # allow 1 or 2 dice anytime
ROLL_LIMIT = 30  # total rolls per round (shared)

# ---------- Dice spritesheet (use your Poker Dice sheet) ----------
# We will try spritesheet.png first (your stated convention), then fall back to the
# original poker dice name if present.
DICE_SHEET_PATHS = [
    "minigames/shut_the_box/spritesheet.png",
    "minigames/shut_the_box/spritesheetpokerdice.png",
]

# The sheet format we assume (based on your poker_dice sheet):
# - Top row: faces 1..6, left to right (square cells)
# - Second row: rolling/blur frames; we’ll use the first 6 cells there for animation
# If the sheet contains extra rows (e.g., won/lost/tie badges), they are ignored.

# ---------- Colors ----------
WHITE = (245, 245, 245)
YELLOW = (255, 230, 40)
DIM = (140, 120, 95)
SHADOW = (0, 0, 0)
BTN_BG = (50, 60, 100)
BTN_OL = (210, 210, 240)
SEL_C = (80, 200, 255)


# ---------- Helpers ----------
def sum_to_combos(tiles_avail, target_sum):
    out = []
    tiles = sorted(tiles_avail)
    for r in range(1, min(6, len(tiles)) + 1):
        for combo in itertools.combinations(tiles, r):
            if sum(combo) == target_sum:
                out.append(list(combo))
    return out


# ---------- Dice sprite atlas (auto-slices the first two rows into 6 square cells) ----------
class DiceAtlas:
    def __init__(self, paths):
        self.ok = False
        self.roll_frames = []  # list[Surface]
        self.faces = {}  # 1..6 -> Surface

        sheet = None
        for p in paths:
            try:
                sheet = pygame.image.load(p).convert_alpha()
                break
            except Exception:
                continue
        if sheet is None:
            return

        sw, sh = sheet.get_width(), sheet.get_height()

        # cell size: square based on height/2 (two rows of dice), and width/6 (six columns)
        ch_by_rows = sh // 2
        cw_by_cols = sw // 6
        c = min(ch_by_rows, cw_by_cols)  # be safe if sheet has extra margins

        # Faces: row 0, cols 0..5 (six faces)
        for fv in range(1, 7):
            ix = fv - 1
            rect = pygame.Rect(ix * c, 0, c, c)
            self.faces[fv] = sheet.subsurface(rect).copy()

        # Rolling frames: row 1, cols 0..5 (use first six frames)
        for ix in range(6):
            rect = pygame.Rect(ix * c, c, c, c)
            self.roll_frames.append(sheet.subsurface(rect).copy())

        self.ok = (len(self.faces) == 6) and (len(self.roll_frames) > 0)

    def get_face(self, pip):
        return self.faces.get(pip)

    def get_roll(self, idx):
        return (
            self.roll_frames[idx % len(self.roll_frames)] if self.roll_frames else None
        )


class DiceAnim:
    """Dice roll animation that cycles bottom-row sprites, then settles to top-row faces."""

    def __init__(self, atlas: DiceAtlas):
        self.atlas = atlas
        self.rolling = False
        self.t = 0.0
        self.duration = 1.25
        self.final_faces = []
        self.cb = None
        self._frame_timer = 0.0
        self._frame_rate = 1 / 16.0
        self._anim_idx = 0
        self.num_dice = 0

    def start(self, faces, duration=1.25, cb=None):
        self.rolling = True
        self.t = 0.0
        self.duration = duration
        self.final_faces = list(faces)
        self.cb = cb
        self._frame_timer = 0.0
        self._anim_idx = 0
        self.num_dice = len(faces)

    def update(self, dt):
        if not self.rolling:
            return
        self.t += dt
        self._frame_timer += dt
        if self._frame_timer >= self._frame_rate:
            self._frame_timer -= self._frame_rate
            self._anim_idx += 1
        if self.t >= self.duration:
            self.rolling = False
            if self.cb:
                cb = self.cb
                self.cb = None
                cb()


class Board:
    def __init__(self):
        self.down = set()
        self.selected = set()
        self.rolls = 0

    def available(self):
        return [v for v in TILES if v not in self.down]

    def score(self):
        return sum(self.available())

    def toggle(self, v):
        if v in self.down:
            return
        if v in self.selected:
            self.selected.remove(v)
        else:
            self.selected.add(v)

    def clear_sel(self):
        self.selected.clear()

    def commit(self):
        for v in self.selected:
            self.down.add(v)
        self.selected.clear()


class AIPlayer:
    def choose_dice(self, avail_tiles):
        if not ALLOW_SINGLE_ANYTIME:
            return 2
        return 1 if max(avail_tiles or [0]) <= 6 else 2

    def pick_combo(self, avail_tiles, total):
        combos = sum_to_combos(avail_tiles, total)
        if not combos:
            return None

        def future_cov(left):
            c = 0
            for s in range(2, 13):
                if sum_to_combos(left, s):
                    c += 1
            return c

        best = None
        for c in combos:
            left = [x for x in avail_tiles if x not in c]
            score = (-future_cov(left), sum(left), -max(c))
            if best is None or score < best[0]:
                best = (score, c)
        choice = best[1]
        if random.random() < AI_RANDOMNESS:
            choice = random.choice(combos)
        return choice


# -----------------------------------------------------------------------------

from scene_manager import Scene
from game_context import GameContext


class ShutTheBoxScene(Scene):
    def __init__(self, manager, context=None, callback=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.minigame_id = MINIGAME_ID
        self.pending_payload = {}
        self._completed = False
        self._pending_outcome = None
        self.forfeited = False
        # Multiplayer plumbing
        flags = getattr(self.context, "flags", {}) if self.context else {}
        self.duel_id = kwargs.get("duel_id") or (flags or {}).get("duel_id")
        self.participants = kwargs.get("participants") or (flags or {}).get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or (flags or {}).get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or (flags or {}).get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.is_authority = not self.net_enabled or self.local_idx == 0
        seed_src = self.duel_id or RNG_SEED
        self.rng = random.Random(seed_src)
        self.net_interval = 1.0 / 15.0
        self.net_timer = 0.0
        self.net_last = 0.0
        self.ai_player = AIPlayer()
        self.ai_timer = 0.0

        # Fonts
        try:
            from content_registry import load_game_fonts

            self.big, self.font, self.small = load_game_fonts()
        except Exception:
            self.big = pygame.font.SysFont(None, 48)
            self.font = pygame.font.SysFont(None, 28)
            self.small = pygame.font.SysFont(None, 22)

        self.tile_font = pygame.font.SysFont(None, 64)
        self.tile_font_bold = pygame.font.SysFont(None, 64)
        self.tile_font_bold.set_bold(True)
        self.dice_font = pygame.font.SysFont(None, 44)
        self.banner_font = pygame.font.SysFont(None, 54)
        self.banner_small = pygame.font.SysFont(None, 28)

        # Background
        self.bg = pygame.image.load("minigames/shut_the_box/background.png").convert()

        # Tile rects (final tuned by you)
        self.tiles_player_rects = [
            pygame.Rect(30 + i * 49, 364, 49, 60) for i in range(12)
        ]
        self.tiles_opponent_rects = [
            pygame.Rect(30 + i * 49, 84, 49, 60) for i in range(12)
        ]

        # Buttons at bottom
        total_w = 4 * BTN_W + 3 * BTN_SP
        left = VW // 2 - total_w // 2
        yb = VH - 44
        self.btn_roll1 = pygame.Rect(left + 0 * (BTN_W + BTN_SP), yb, BTN_W, BTN_H)
        self.btn_roll2 = pygame.Rect(left + 1 * (BTN_W + BTN_SP), yb, BTN_W, BTN_H)
        self.btn_confirm = pygame.Rect(left + 2 * (BTN_W + BTN_SP), yb, BTN_W, BTN_H)
        self.btn_pass = pygame.Rect(left + 3 * (BTN_W + BTN_SP), yb, BTN_W, BTN_H)

        # Game state
        self.player = Board()  # local
        self.ai = Board()      # opponent
        self.turn_idx = 0  # 0 = host, 1 = remote; we map bottom to local when applying state
        self.state = "WAIT_ROLL"  # WAIT_ROLL → SELECT → ...
        self.total_required = 0
        self.dice_last = []

        # Round/match
        self.round_num = 1
        self.win_p = 0
        self.win_a = 0
        self.rolls_left = ROLL_LIMIT
        self.last_round_winner = None

        # Banner/summary
        self.round_banner = None
        self.banner_lines = []
        self.banner_timer = 0
        self.state_after_banner = None

        # Scaling cache
        self._sx = 1.0
        self._sy = 1.0

        # Dice atlas + animator (spritesheet-based with fallback)
        self.dice_atlas = DiceAtlas(DICE_SHEET_PATHS)
        self.dice_anim = DiceAnim(self.dice_atlas)

        # Pit center
        self.pit_cx, self.pit_cy = VW // 2, 262
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ----- Scaling helpers -----
    def _scale_rect(self, r):
        return pygame.Rect(
            int(r.x * self._sx),
            int(r.y * self._sy),
            int(r.w * self._sx),
            int(r.h * self._sy),
        )

    def _pt(self, x, y):
        return (int(x * self._sx), int(y * self._sy))

    # ----- Input -----
    def _tile_hit(self, pos):
        for i, r in enumerate(self.tiles_player_rects, start=1):
            if self._scale_rect(r).collidepoint(pos):
                return i
        return None

    def handle_event(self, e):
        if self._pending_outcome:
            if e.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                self._finalize(self._pending_outcome)
            return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self._pause_game()
            return
        if self.state in ("ROUND_END", "MATCH_END") and (
            e.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN)
        ):
            self.banner_timer = 0
            return

        if e.type != pygame.MOUSEBUTTONDOWN or e.button != 1:
            return
        if self.state in ("ROUND_END", "MATCH_END"):
            return
        if self.dice_anim.rolling:
            return
        if self.net_enabled and self.turn_idx != self.local_idx:
            return

        pos = e.pos

        if (
            self._scale_rect(self.btn_roll1).collidepoint(pos)
            and self.state == "WAIT_ROLL"
            and ALLOW_SINGLE_ANYTIME
        ):
            self._request_roll(1)
            return
        if (
            self._scale_rect(self.btn_roll2).collidepoint(pos)
            and self.state == "WAIT_ROLL"
        ):
            self._request_roll(2)
            return
        if (
            self._scale_rect(self.btn_confirm).collidepoint(pos)
            and self.state == "SELECT"
        ):
            if sum(self.player.selected) == self.total_required and self.player.selected:
                self._request_confirm()
            return
        if (
            self._scale_rect(self.btn_pass).collidepoint(pos)
            and self.state == "SELECT"
        ):
            self._request_pass()
            return

        if self.state == "SELECT":
            v = self._tile_hit(pos)
            if v and v not in self.player.down:
                self._request_toggle(v)

    # ----- Dice + flow -----
    def _roll(self, n):
        return [self.rng.randint(1, 6) for _ in range(n)]

    def _board_for_side(self, side_idx):
        return self.player if side_idx == self.local_idx else self.ai

    def _start_roll(self, n, side_idx, faces=None):
        faces = list(faces) if faces else self._roll(n)
        if self.rolls_left > 0:
            self.rolls_left -= 1

        def finish():
            self.dice_last = faces
            self.total_required = sum(faces)
            board = self._board_for_side(side_idx)
            board.rolls += 1
            self.state = "SELECT"

        self.dice_anim.start(faces, duration=1.25, cb=finish)
        self.state = "PLAYER_ROLL"

    def _after_commit(self, side_idx):
        board = self._board_for_side(side_idx)
        if not board.available():
            self._end_round(side_idx)
            return
        if self._check_roll_cap():
            return
        self._switch_turn()

    def _switch_turn(self):
        self.turn_idx = 1 - self.turn_idx
        self.state = "WAIT_ROLL"
        self.total_required = 0
        self.dice_last = []
        self.player.clear_sel()
        self.ai.clear_sel()
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ----- Round/Match banners -----
    def _set_round_banner(self, title, ps, pr, as_, ar, footer=""):
        self.round_banner = title
        self.banner_lines = [
            f"Your score: {ps}  (rolls {pr})",
            f"Opponent:   {as_}  (rolls {ar})",
        ]
        if footer:
            self.banner_lines.append(footer)
        self.banner_timer = 3200
        self.state = "ROUND_END"

    def _set_match_banner(self, title):
        self.round_banner = title
        self.banner_lines = [f"Final: Player {self.win_p} — Opponent {self.win_a}"]
        self.banner_timer = 3600
        self.state = "MATCH_END"
        self.state_after_banner = "EXIT"
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _end_round(self, winner_idx):
        ps, as_ = self.player.score(), self.ai.score()
        pr, ar = self.player.rolls, self.ai.rolls
        self.last_round_winner = winner_idx

        local_won = winner_idx == self.local_idx
        if local_won:
            self.win_p += 1
            self._set_round_banner(
                f"Round {self.round_num}: You Cleared the Box!", ps, pr, as_, ar, "Click to continue"
            )
            self.state_after_banner = "NEXT"
        else:
            self.win_a += 1
            self._set_round_banner(
                f"Round {self.round_num}: Opponent Cleared",
                ps,
                pr,
                as_,
                ar,
                "Click to continue",
            )
            self.state_after_banner = "NEXT"

        self.player = Board()
        self.ai = Board()
        self.turn_idx = 0
        self.state = "ROUND_END"
        self.dice_last = []
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _prepare_next_round(self):
        self.round_num += 1
        need = (BEST_OF + 1) // 2
        if self.win_p >= need or self.win_a >= need:
            self._set_match_banner(
                "Match Win!" if self.win_p > self.win_a else "Match Lost"
            )
            return
        self.round_banner = None
        self.banner_lines = []
        self.state_after_banner = None
        self.state = "WAIT_ROLL"
        self.rolls_left = ROLL_LIMIT
        self.last_round_winner = None
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ----- Update/Draw -----
    def update(self, dt):
        sw, sh = self.manager.size
        self._sx, self._sy = sw / VW, sh / VH

        if self.net_enabled:
            self._net_poll_actions(float(dt))

        self.dice_anim.update(dt)
        self._ai_take_turn(dt)

        if self.state in ("ROUND_END", "MATCH_END"):
            self.banner_timer -= dt * 1000
            if self.banner_timer <= 0:
                if self.state == "ROUND_END":
                    if self.state_after_banner == "NEXT":
                        self._prepare_next_round()
                    else:
                        self.round_banner = None
                        self.banner_lines = []
                        self.state = "WAIT_ROLL"
                elif self.state == "MATCH_END":
                    outcome = "win" if self.win_p > self.win_a else "lose"
                    self.pending_payload = {
                        "player_rounds": self.win_p,
                        "opponent_rounds": self.win_a,
                        "rounds_played": self.round_num,
                    }
                    self._pending_outcome = outcome
                    if self.net_enabled and self.is_authority:
                        winner_id = self.local_id if self.win_p > self.win_a else self.remote_id
                        loser_id = self.remote_id if winner_id == self.local_id else self.local_id
                        self.pending_payload["winner"] = winner_id
                        self.pending_payload["loser"] = loser_id
                        self._net_send_action(
                            {"kind": "finish", "winner": winner_id, "loser": loser_id, "outcome": outcome}
                        )

    def _draw_button(self, surf, rect, label, enabled=True):
        r = self._scale_rect(rect)
        base = BTN_BG if enabled else (36, 36, 60)
        border = BTN_OL if enabled else (140, 140, 160)
        pygame.draw.rect(surf, base, r, border_radius=8)
        pygame.draw.rect(surf, border, r, 2, border_radius=8)
        txt = self.small.render(label, True, (240, 240, 240))
        surf.blit(txt, txt.get_rect(center=r.center))

    def _draw_tile_num(self, surf, num, rect, color, down=False, selected=False):
        sr = self._scale_rect(rect)
        if down:
            txt = self.tile_font.render(str(num), True, DIM)
            surf.blit(txt, txt.get_rect(center=(sr.centerx, sr.centery + 6)))
            return
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            o = self.tile_font_bold.render(str(num), True, SHADOW)
            surf.blit(o, o.get_rect(center=(sr.centerx + dx, sr.centery + 6 + dy)))
        col = SEL_C if selected else color
        txt = self.tile_font_bold.render(str(num), True, col)
        surf.blit(txt, txt.get_rect(center=(sr.centerx, sr.centery + 6)))

    def _draw_die_vector(self, surf, center, size, face, jitter=0):
        cx, cy = center
        s = int(size)
        r = pygame.Rect(cx - s // 2 + jitter, cy - s // 2 + jitter, s, s)
        pygame.draw.rect(surf, (240, 240, 240), r, border_radius=int(s * 0.18))
        pygame.draw.rect(surf, (30, 30, 30), r, 2, border_radius=int(s * 0.18))

        def pip(x, y):
            pygame.draw.circle(
                surf, (30, 30, 30), (cx + x + jitter, cy + y + jitter), max(2, s // 10)
            )

        d = s // 4
        spots = {
            1: [(0, 0)],
            2: [(-d, -d), (d, d)],
            3: [(-d, -d), (0, 0), (d, d)],
            4: [(-d, -d), (d, -d), (-d, d), (d, d)],
            5: [(-d, -d), (d, -d), (0, 0), (-d, d), (d, d)],
            6: [(-d, -d), (d, -d), (-d, 0), (d, 0), (-d, d), (d, d)],
        }
        for px, py in spots[face]:
            pip(px, py)

    def _blit_scaled(self, surf, frame: pygame.Surface, center, size):
        if frame is None:
            return
        target = pygame.transform.scale(
            frame, (int(size * self._sx), int(size * self._sy))
        )
        rect = target.get_rect(center=self._pt(center[0], center[1]))
        surf.blit(target, rect.topleft)

    def _draw_dice_in_pit(self, surf):
        cx, cy = self.pit_cx, self.pit_cy
        sprite_size = 64  # logical sprite size

        nd = (
            self.dice_anim.num_dice
            if self.dice_anim.num_dice
            else (1 if self.dice_last else 2)
        )
        pos = [(-28, 0), (28, 0)] if nd == 2 else [(0, 0)]

        if self.dice_anim.rolling and self.dice_atlas.ok:
            frame = self.dice_atlas.get_roll(self.dice_anim._anim_idx)
            for i in range(nd):
                self._blit_scaled(
                    surf, frame, (cx + pos[i][0], cy + pos[i][1]), sprite_size
                )
        elif self.dice_last and self.dice_atlas.ok:
            for i, f in enumerate(self.dice_last):
                frame = self.dice_atlas.get_face(f)
                if frame is None:
                    self._draw_die_vector(
                        surf, self._pt(cx + pos[i][0], cy + pos[i][1]), 44, f
                    )
                else:
                    self._blit_scaled(
                        surf, frame, (cx + pos[i][0], cy + pos[i][1]), sprite_size
                    )
            label = f"{' + '.join(map(str,self.dice_last))} = {sum(self.dice_last)}"
            t = self.dice_font.render(label, True, (250, 240, 210))
            surf.blit(t, t.get_rect(center=self._pt(VW // 2, cy - 34)))
        else:
            # fallback: vector dice
            size = 44
            if self.dice_anim.rolling:
                for i in range(nd):
                    self._draw_die_vector(
                        surf, self._pt(cx + pos[i][0], cy + pos[i][1]), size, 6
                    )
            elif self.dice_last:
                for i, f in enumerate(self.dice_last):
                    self._draw_die_vector(
                        surf, self._pt(cx + pos[i][0], cy + pos[i][1]), size, f
                    )
                label = f"{' + '.join(map(str,self.dice_last))} = {sum(self.dice_last)}"
                t = self.dice_font.render(label, True, (250, 240, 210))
                surf.blit(t, t.get_rect(center=self._pt(VW // 2, cy - 34)))

    def draw(self):
        sw, sh = self.manager.size
        self._sx, self._sy = sw / VW, sh / VH
        s = self.manager.screen

        bg_scaled = pygame.transform.scale(self.bg, (sw, sh))
        s.blit(bg_scaled, (0, 0))

        for i, r in enumerate(self.tiles_opponent_rects, start=1):
            self._draw_tile_num(
                s, i, r, WHITE, down=(i in self.ai.down), selected=False
            )
        for i, r in enumerate(self.tiles_player_rects, start=1):
            self._draw_tile_num(
                s,
                i,
                r,
                YELLOW,
                down=(i in self.player.down),
                selected=(i in self.player.selected),
            )

        self._draw_dice_in_pit(s)

        rolling = self.dice_anim.rolling
        my_turn = self.turn_idx == self.local_idx
        if my_turn:
            self._draw_button(
                s,
                self.btn_roll1,
                "ROLL 1",
                enabled=(
                    self.state == "WAIT_ROLL" and ALLOW_SINGLE_ANYTIME and not rolling
                ),
            )
            self._draw_button(
                s,
                self.btn_roll2,
                "ROLL 2",
                enabled=(self.state == "WAIT_ROLL" and not rolling),
            )
            can_confirm = (
                self.state == "SELECT"
                and not rolling
                and self.player.selected
                and sum(self.player.selected) == self.total_required
            )
            self._draw_button(s, self.btn_confirm, "CONFIRM", enabled=can_confirm)
            no_moves = self.state == "SELECT" and not sum_to_combos(
                self.player.available(), self.total_required
            )
            self._draw_button(
                s, self.btn_pass, "PASS", enabled=no_moves and not rolling
            )
        else:
            self._draw_button(s, self.btn_roll1, "ROLL 1", enabled=False)
            self._draw_button(s, self.btn_roll2, "ROLL 2", enabled=False)
            self._draw_button(s, self.btn_confirm, "CONFIRM", enabled=False)
            self._draw_button(s, self.btn_pass, "PASS", enabled=False)

        need = (BEST_OF + 1) // 2

        def tally(x, y, wins):
            for i in range(need):
                c = (255, 230, 120) if i < wins else (70, 60, 40)
                pygame.draw.circle(
                    s, (10, 8, 6), self._pt(x + i * 28, y), int(12 * self._sx)
                )
                pygame.draw.circle(s, c, self._pt(x + i * 28, y), int(10 * self._sx))

        lab_t = self.small.render("Opponent", True, WHITE)
        s.blit(lab_t, self._pt(16, 12))
        tally(16, 34, self.win_a)
        lab_b = self.small.render("Player", True, WHITE)
        s.blit(lab_b, self._pt(16, VH - 56))
        tally(16, VH - 34, self.win_p)

        # Roll + score info
        info = f"Rolls left: {self.rolls_left}   |   Your tiles: {self.player.score()}   Opp tiles: {self.ai.score()}"
        tinfo = self.small.render(info, True, (235, 235, 235))
        s.blit(tinfo, self._pt(VW // 2 - tinfo.get_width() // 2, 12))

        if my_turn and self.state == "SELECT":
            ssum = sum(self.player.selected)
            msg = f"Selected: {ssum} / Need: {self.total_required}"
            m = self.small.render(msg, True, WHITE)
            s.blit(m, self._pt(VW - 12 - m.get_width(), VH - 24))

        if self.state in ("ROUND_END", "MATCH_END") and self.round_banner:
            dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 140))
            s.blit(dim, (0, 0))
            title = self.banner_font.render(self.round_banner, True, (255, 240, 160))
            s.blit(
                title, title.get_rect(center=(sw // 2, sh // 2 - int(36 * self._sy)))
            )
            y = sh // 2 + int(2 * self._sy)
            for line in self.banner_lines:
                t = self.font.render(line, True, (245, 245, 235))
                s.blit(t, t.get_rect(center=(sw // 2, y)))
                y += int(26 * self._sy)
            hint = self.banner_small.render(
                "Click or press any key to continue", True, (220, 220, 220)
            )
            s.blit(hint, hint.get_rect(center=(sw // 2, y + int(10 * self._sy))))

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[ShutTheBox] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed or outcome is None:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        if not self.pending_payload:
            self.pending_payload = {
                "player_rounds": self.win_p,
                "opponent_rounds": self.win_a,
                "rounds_played": self.round_num,
                "forfeit": self.forfeited,
            }
        # add winner/loser if available
        if self.net_enabled and self.remote_id:
            if self.win_p > self.win_a:
                self.pending_payload.setdefault("winner", self.local_id)
                self.pending_payload.setdefault("loser", self.remote_id)
            elif self.win_a > self.win_p:
                self.pending_payload.setdefault("winner", self.remote_id)
                self.pending_payload.setdefault("loser", self.local_id)
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[ShutTheBox] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[ShutTheBox] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self.forfeited = True
        self.pending_payload = {
            "player_rounds": self.win_p,
            "opponent_rounds": self.win_a,
            "rounds_played": self.round_num,
            "forfeit": True,
            "reason": "forfeit",
        }
        self._pending_outcome = "forfeit"
        self._finalize("forfeit")

    # ----- Local action application -----
    def _apply_toggle(self, side_idx, tile):
        if self.state != "SELECT":
            return
        board = self._board_for_side(side_idx)
        if tile not in TILES or tile in board.down:
            return
        board.toggle(tile)

    def _apply_confirm(self, side_idx):
        if self.state != "SELECT":
            return
        board = self._board_for_side(side_idx)
        if not board.selected or sum(board.selected) != self.total_required:
            return
        board.commit()
        self._after_commit(side_idx)

    def _apply_pass(self, side_idx):
        if self.state != "SELECT":
            return
        board = self._board_for_side(side_idx)
        board.clear_sel()
        if self._check_roll_cap():
            return
        self._switch_turn()

    def _check_roll_cap(self):
        if self.rolls_left > 0:
            return False
        ps, as_ = self.player.score(), self.ai.score()
        pr, ar = self.player.rolls, self.ai.rolls
        win_idx = self.local_idx
        if as_ < ps:
            win_idx = self.remote_idx
        elif ps == as_:
            if ar < pr:
                win_idx = self.remote_idx
        self._end_round(win_idx)
        return True

    # ----- Single-player AI -----
    def _ai_take_turn(self, dt):
        if self.net_enabled:
            return
        if self.turn_idx == self.local_idx:
            return
        if self.state not in ("WAIT_ROLL", "SELECT"):
            return
        if self.dice_anim.rolling:
            return
        self.ai_timer += dt
        if self.ai_timer < 0.2:
            return
        self.ai_timer = 0.0
        ai_side = 1 - self.local_idx
        board = self._board_for_side(ai_side)
        if self.state == "WAIT_ROLL":
            n = self.ai_player.choose_dice(board.available())
            self._start_roll(n, ai_side)
            return
        # SELECT
        combo = self.ai_player.pick_combo(board.available(), self.total_required)
        if combo:
            # ensure selected cleared then toggle chosen tiles
            board.clear_sel()
            for v in combo:
                board.toggle(v)
            self._apply_confirm(ai_side)
        else:
            self._apply_pass(ai_side)

    # ----- Local requests (fan out to authority) -----
    def _request_roll(self, n):
        if self.state != "WAIT_ROLL":
            return
        if self.rolls_left <= 0:
            return
        n = 1 if (ALLOW_SINGLE_ANYTIME and n == 1) else 2
        if self.net_enabled:
            if self.is_authority:
                faces = self._roll(n)
                self._start_roll(n, self.turn_idx, faces=faces)
                self._net_send_action(
                    {
                        "kind": "roll",
                        "n": n,
                        "faces": faces,
                        "side": self.turn_idx,
                        "rolls_left": self.rolls_left,
                    }
                )
                self._net_send_state(force=True)
            else:
                self._net_send_action({"kind": "roll", "n": n, "side": self.turn_idx})
        else:
            self._start_roll(n, self.turn_idx)

    def _request_toggle(self, tile):
        if self.state != "SELECT":
            return
        try:
            tval = int(tile)
        except Exception:
            return
        self._apply_toggle(self.turn_idx, tval)
        if self.net_enabled:
            if self.is_authority:
                self._net_send_state(force=True)
            else:
                self._net_send_action({"kind": "toggle", "tile": tval, "side": self.turn_idx})

    def _request_confirm(self):
        if self.state != "SELECT":
            return
        if self.net_enabled and not self.is_authority:
            self._net_send_action({"kind": "confirm", "side": self.turn_idx})
            return
        self._apply_confirm(self.turn_idx)
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _request_pass(self):
        if self.state != "SELECT":
            return
        if self.net_enabled and not self.is_authority:
            self._net_send_action({"kind": "pass", "side": self.turn_idx})
            return
        self._apply_pass(self.turn_idx)
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ----- Networking helpers -----
    def _board_to_dict(self, b: Board):
        return {
            "down": list(b.down),
            "selected": list(b.selected),
            "rolls": b.rolls,
        }

    def _board_from_dict(self, d, fallback=None):
        if isinstance(d, Board):
            return d
        b = Board()
        if fallback:
            b.down = set(fallback.down)
            b.selected = set(fallback.selected)
            b.rolls = fallback.rolls
        try:
            b.down = set(int(x) for x in d.get("down", []))
            b.selected = set(int(x) for x in d.get("selected", []))
            b.rolls = int(d.get("rolls", b.rolls))
        except Exception:
            pass
        return b

    def _pack_state(self):
        return {
            "round": self.round_num,
            "wins": [self.win_p, self.win_a],
            "turn": self.turn_idx,
            "state": self.state,
            "total_required": self.total_required,
            "dice_last": list(self.dice_last),
            "boards": [self._board_to_dict(self.player), self._board_to_dict(self.ai)],
            "banner": self.round_banner,
            "banner_lines": list(self.banner_lines),
            "banner_timer": self.banner_timer,
            "state_after_banner": self.state_after_banner,
            "rolls_left": self.rolls_left,
            "last_round_winner": self.last_round_winner,
        }

    def _apply_state(self, st: dict):
        if not st:
            return
        boards = st.get("boards") or []
        if len(boards) >= 2:
            bottom = boards[self.local_idx] if self.local_idx < len(boards) else boards[0]
            top = boards[1 - self.local_idx] if (1 - self.local_idx) < len(boards) else boards[0]
            self.player = self._board_from_dict(bottom, self.player)
            self.ai = self._board_from_dict(top, self.ai)
        wins = st.get("wins")
        if isinstance(wins, (list, tuple)) and len(wins) == 2:
            if self.local_idx == 0:
                self.win_p, self.win_a = int(wins[0]), int(wins[1])
            else:
                self.win_p, self.win_a = int(wins[1]), int(wins[0])
        if "round" in st:
            try:
                self.round_num = int(st.get("round", self.round_num))
            except Exception:
                pass
        if "turn" in st:
            try:
                self.turn_idx = int(st.get("turn", self.turn_idx))
            except Exception:
                pass
        self.state = st.get("state", self.state)
        try:
            self.total_required = int(st.get("total_required", self.total_required))
        except Exception:
            pass
        try:
            dl = st.get("dice_last", self.dice_last)
            if dl is not None:
                self.dice_last = [int(x) for x in dl]
        except Exception:
            pass
        self.round_banner = st.get("banner", self.round_banner)
        self.banner_lines = st.get("banner_lines", self.banner_lines)
        try:
            self.banner_timer = st.get("banner_timer", self.banner_timer)
        except Exception:
            pass
        self.state_after_banner = st.get("state_after_banner", self.state_after_banner)
        if "rolls_left" in st:
            try:
                self.rolls_left = int(st.get("rolls_left", self.rolls_left))
            except Exception:
                pass
        self.last_round_winner = st.get("last_round_winner", self.last_round_winner)
        if not self.is_authority and self.round_banner and self.state == "ROUND_END":
            ps, as_ = self.player.score(), self.ai.score()
            pr, ar = self.player.rolls, self.ai.rolls
            local_won = self.last_round_winner == self.local_idx
            title = (
                f"Round {self.round_num}: You Cleared the Box!"
                if local_won
                else f"Round {self.round_num}: Opponent Cleared"
            )
            self._set_round_banner(title, ps, pr, as_, ar, "Click to continue")
            # restore timer to whatever host sent to avoid instant skip
            try:
                self.banner_timer = st.get("banner_timer", self.banner_timer)
            except Exception:
                pass
        if not self.is_authority and self.state == "MATCH_END":
            title = "Match Win!" if self.win_p > self.win_a else "Match Lost"
            self._set_match_banner(title)
            try:
                self.banner_timer = st.get("banner_timer", self.banner_timer)
            except Exception:
                pass
        if self.state != "SELECT":
            self.player.clear_sel()
            self.ai.clear_sel()

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[ShutTheBox] send failed: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
        payload = {"kind": kind, "state": self._pack_state()}
        payload.update(extra or {})
        self._net_send_action(payload)

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            if msg.get("from") == self.local_id:
                continue
            self._apply_remote_action(msg.get("action") or {})

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "roll":
            side = int(action.get("side", self.turn_idx))
            faces = action.get("faces") or []
            n = int(action.get("n", len(faces) or 2))
            if self.is_authority:
                if self.state != "WAIT_ROLL" or side != self.turn_idx:
                    return
                if not faces:
                    faces = self._roll(n)
                self._start_roll(n, side_idx=side, faces=faces)
                self._net_send_action(
                    {
                        "kind": "roll",
                        "n": n,
                        "faces": list(faces),
                        "side": side,
                        "rolls_left": self.rolls_left,
                    }
                )
                self._net_send_state(force=True)
            else:
                self.turn_idx = side
                if "rolls_left" in action:
                    try:
                        self.rolls_left = int(action.get("rolls_left", self.rolls_left))
                    except Exception:
                        pass
                self._start_roll(n, side_idx=side, faces=faces)
            return
        if not self.is_authority:
            return
        side = int(action.get("side", self.turn_idx))
        if kind == "toggle":
            self._apply_toggle(side, int(action.get("tile", 0)))
            self._net_send_state(force=True)
            return
        if kind == "confirm":
            self._apply_confirm(side)
            if self.net_enabled:
                self._net_send_state(force=True)
            return
        if kind == "pass":
            self._apply_pass(side)
            if self.net_enabled:
                self._net_send_state(force=True)
            return
        if kind == "finish":
            win = action.get("winner")
            lose = action.get("loser")
            outcome = action.get("outcome")
            mapped = outcome
            if win == self.local_id:
                mapped = "win"
            elif lose == self.local_id:
                mapped = "lose"
            self.pending_payload = {"winner": win, "loser": lose}
            self._pending_outcome = mapped
            return


# ---------- contract ----------
def launch(manager, context=None, callback=None, **kwargs):
    return ShutTheBoxScene(manager, context, callback, **kwargs)
