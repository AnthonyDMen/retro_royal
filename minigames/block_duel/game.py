import pygame, random, time, uuid
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner
from .graphics import *

MULTIPLAYER_ENABLED = True

TITLE = "Block Duel"
FALL_SPEED = 500
ENEMY_FALL_SPEED = 550
MATCH_TIME = 150  # 2.5 minutes to keep rounds snappy
SCREEN = None
BASE_W, BASE_H = 1280, 720

# --- Button layout ------------------------------------------------------------
BUTTON_SLOTS = [
    pygame.Rect(300, 665, 120, 64),
    pygame.Rect(440, 665, 120, 64),
    pygame.Rect(580, 665, 120, 64),
    pygame.Rect(720, 665, 120, 64),
    pygame.Rect(860, 665, 120, 64),
]

# Powers
P_GARBAGE1 = "garbage1"
P_SPEED_EN = "speed_enemy"
P_FORCEDROP = "forcedrop"
P_CLEANSELF = "cleanself"
P_STEAL_NEXT = "steal_next"

POWER_LABEL = {
    P_SPEED_EN: "Speed Boost",
    P_FORCEDROP: "Force Drop",
    P_GARBAGE1: "Garbage +1",
    P_STEAL_NEXT: "Next Swap",
    P_CLEANSELF: "Clean Sweep",
}

POWER_COST = {
    P_SPEED_EN: 10,
    P_FORCEDROP: 14,
    P_GARBAGE1: 16,
    P_STEAL_NEXT: 18,
    P_CLEANSELF: 24,
}

CONFIRM_WINDOW_MS = 1200
FLASH_DURATION_MS = 300


# --- Main Scene ---------------------------------------------------------------
class BlockDuelScene(Scene):
    def __init__(self, manager, context=None, callback=None, difficulty=1.0, duel_id=None, participants=None, multiplayer_client=None, local_player_id=None):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.base_surf = pygame.Surface((BASE_W, BASE_H)).convert()
        self.bg = load_background()
        self.font_big, self.font, self.font_small = load_game_fonts()
        self.minigame_id = "block_duel"
        self.banner = EndBanner(
            duration=2.0,
            titles={
                "win": "Block Duel Cleared!",
                "lose": "Block Duel Failed",
                "tie": "Block Duel Tie",
                "forfeit": "Block Duel Forfeit",
            },
        )
        self.pending_outcome = None
        self.pending_payload = {}
        self._completed = False
        self._outcome_sent = False
        flags = getattr(context, "flags", {}) if context else {}
        self.duel_id = duel_id or flags.get("duel_id")
        self.participants = participants or flags.get("participants")
        self.local_id = local_player_id or flags.get("duel_local_id")
        self.net_client = multiplayer_client or flags.get("multiplayer_client")
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.opponent_id = None
        if self.participants and self.local_id:
            for pid in self.participants:
                if pid != self.local_id:
                    self.opponent_id = pid
                    break
        # Fallback: if no opponent resolved but participants exist, pick first.
        if not self.opponent_id and self.participants:
            self.opponent_id = self.participants[0]
        # NPC opponents use local single-player flow; disable net sync for them.
        if self.opponent_id and str(self.opponent_id).startswith("npc-"):
            self.net_enabled = False

        # Shared deterministic stream so both players draw from the same middle chute.
        self._rng_seed = self.duel_id or "block-duel"
        self._rng = random.Random(self._rng_seed)

        self._net_state_timer = 0.0
        self._net_state_interval = 0.20
        self.reset()
        self.start_time = time.time()
        self._blit_scale = 1.0
        self._blit_offset = (0, 0)

    # -------------------------------------------------------------------------
    def reset(self):
        self.pending_payload = {}
        self.pending_outcome = None
        self._completed = False
        self.player_board = [[None for _ in range(GRID_W)] for _ in range(GRID_H)]
        self.enemy_board = [[None for _ in range(GRID_W)] for _ in range(GRID_H)]
        self.bag = []
        self.shared_queue = [self._new_shape() for _ in range(6)]
        self.player_next = self._draw_piece()
        self.enemy_next = self._draw_piece()

        # player + enemy current pieces
        self.shape = self.player_next
        self.player_next = self._draw_piece()
        self.pos = [3, 0]
        self.rot = 0

        self.enemy_shape = self.enemy_next
        self.enemy_next = self._draw_piece()
        self.enemy_pos = [3, 0]
        self.enemy_rot = 0

        # timers and stats
        self.timer = 0
        self.enemy_timer = 0
        self.drop_interval = FALL_SPEED / 1000.0
        self.enemy_interval = ENEMY_FALL_SPEED / 1000.0
        self.enemy_boost_until = 0

        self.score = 0
        self.enemy_score = 0
        self.credits = 0
        self.enemy_credits = 0
        self.match_ended = False
        self.game_over = False
        self.enemy_game_over = False
        self.final_winner = None
        self._outcome_sent = False

        # tallies (now 2 instead of 3)
        self.attack_count = 0
        self.enemy_attack_count = 0
        self.flash_active_p = False
        self.flash_timer_p = 0
        self.flash_active_e = False
        self.flash_timer_e = 0

        # button & visual state
        self.pending_btn = None
        self.pending_started = 0
        self.flash_effects = {}
        # Net state cache for opponent rendering
        self.enemy_ready = not self.net_enabled
        self.battle_started = not self.net_enabled
        self._net_state_timer = 0.0

    # -------------------------------------------------------------------------
    def _new_shape(self):
        if not self.bag:
            self.bag = list(SHAPES.keys())
            self._rng.shuffle(self.bag)
        return self.bag.pop()

    def _cells(self, shape, pos, rot):
        pts = []
        for x, y in SHAPES[shape]:
            for _ in range(rot % 4):
                x, y = y, -x
            pts.append((pos[0] + x, pos[1] + y))
        return pts

    def _valid(self, board, cells):
        for x, y in cells:
            if x < 0 or x >= GRID_W or y >= GRID_H:
                return False
            if y >= 0 and board[y][x]:
                return False
        return True

    def _lock(self, board, shape, pos, rot):
        for x, y in self._cells(shape, pos, rot):
            if 0 <= y < GRID_H and 0 <= x < GRID_W:
                board[y][x] = shape

    def _add_garbage(self, board, n=1):
        for _ in range(n):
            del board[0]
            board.append(["X"] * GRID_W)

    def _draw_piece(self):
        """Draw the next piece from the shared chute; broadcast to opponent if multiplayer."""
        if not self.shared_queue:
            self.shared_queue = [self._new_shape() for _ in range(6)]
        piece = self.shared_queue.pop(0)
        self.shared_queue.append(self._new_shape())
        if self.net_enabled and self.net_client and self.duel_id:
            try:
                self._net_send_action({"kind": "draw", "duel_id": self.duel_id, "piece": piece})
            except Exception:
                pass
        return piece

    def _spawn_player(self):
        self.shape = self.player_next
        self.player_next = self._draw_piece()
        self.pos = [3, 0]
        self.rot = 0
        if not self._valid(
            self.player_board, self._cells(self.shape, self.pos, self.rot)
        ):
            self.game_over = True

    def _spawn_enemy(self):
        self.enemy_shape = self.enemy_next
        self.enemy_next = self._draw_piece()
        self.enemy_pos = [3, 0]
        self.enemy_rot = 0
        if not self._valid(
            self.enemy_board,
            self._cells(self.enemy_shape, self.enemy_pos, self.enemy_rot),
        ):
            self.enemy_game_over = True

    def _move(self, board, shape, pos, rot, dx, dy):
        new_pos = [pos[0] + dx, pos[1] + dy]
        if self._valid(board, self._cells(shape, new_pos, rot)):
            return new_pos
        return pos

    def _rotate(self, board, shape, pos, rot):
        new_rot = (rot + 1) % 4
        if self._valid(board, self._cells(shape, pos, new_rot)):
            return new_rot
        for kick in (-1, 1, -2, 2):
            if self._valid(board, self._cells(shape, [pos[0] + kick, pos[1]], new_rot)):
                pos[0] += kick
                return new_rot
        return rot

    def _clear_lines(self, board, owner="player"):
        cleared = 0
        r = GRID_H - 1
        while r >= 0:
            row = board[r]
            # Only clear lines that are full and NOT all garbage
            if all(row) and all(cell != "X" for cell in row):
                del board[r]
                board.insert(0, [None if cell != "X" else "X" for cell in row])
                cleared += 1
            else:
                r -= 1

        if cleared:
            gain = {1: 1, 2: 3, 3: 5, 4: 8}.get(cleared, 0)
            if owner == "player":
                self.score += cleared * 100
                self.credits += gain
                if cleared == 4:
                    self.attack_count += 1
                    if self.attack_count >= 2:
                        self._add_garbage(self.enemy_board, 1)
                        self.attack_count = 0
                        self.flash_active_p = True
                        self.flash_timer_p = pygame.time.get_ticks()
            else:
                self.enemy_score += cleared * 100
                self.enemy_credits += gain
                if cleared == 4:
                    self.enemy_attack_count += 1
                    if self.enemy_attack_count >= 2:
                        self._add_garbage(self.player_board, 1)
                        self.enemy_attack_count = 0
                        self.flash_active_e = True
                        self.flash_timer_e = pygame.time.get_ticks()

    # --- Power logic ----------------------------------------------------------
    def _apply_power(self, pid):
        now = pygame.time.get_ticks()
        if self.credits < POWER_COST[pid]:
            return False
        self.credits -= POWER_COST[pid]
        self.flash_effects[pid] = now
        # In PvP, mirror powers to opponent via net; only apply local effects that target self.
        if self.net_enabled and self.opponent_id:
            self._net_send_action({"kind": "power", "pid": pid})
            if pid == P_CLEANSELF:
                self.player_board = [[None for _ in range(GRID_W)] for _ in range(GRID_H)]
            elif pid == P_STEAL_NEXT:
                self.player_next, self.enemy_next = self.enemy_next, self.player_next
            # Enemy-targeting effects will be applied on the opponent side; skip local enemy mutations.
            return True
        if pid == P_GARBAGE1:
            self._add_garbage(self.enemy_board, 1)
        elif pid == P_SPEED_EN:
            # Speed up enemy fall rate by 33% for 3 seconds
            self.enemy_boost_until = now + 3000
            self.enemy_interval = (ENEMY_FALL_SPEED / 1000.0) / 1.33
        elif pid == P_FORCEDROP:
            while True:
                new_pos = self._move(
                    self.enemy_board,
                    self.enemy_shape,
                    self.enemy_pos,
                    self.enemy_rot,
                    0,
                    1,
                )
                if new_pos == self.enemy_pos:
                    self._lock(
                        self.enemy_board,
                        self.enemy_shape,
                        self.enemy_pos,
                        self.enemy_rot,
                    )
                    self._clear_lines(self.enemy_board, "enemy")
                    self._spawn_enemy()
                    break
                self.enemy_pos = new_pos
        elif pid == P_CLEANSELF:
            # Only clear non-garbage cells; leave garbage ('X') untouched
            for y in range(GRID_H):
                for x in range(GRID_W):
                    if self.player_board[y][x] is not None and self.player_board[y][x] != "X":
                        self.player_board[y][x] = None
        elif pid == P_STEAL_NEXT:
            # Swap the next piece between player and enemy (always)
            self.player_next, self.enemy_next = self.enemy_next, self.player_next
        return True

    # --- Update ---------------------------------------------------------------
    def update(self, dt):
        self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return
        if self.match_ended:
            return

        elapsed = time.time() - self.start_time
        remaining = max(0, MATCH_TIME - elapsed)
        if remaining <= 0 or self.game_over or self.enemy_game_over:
            self._end_match()
            return

        # player gravity
        if not self.game_over:
            self.timer += dt
            if self.timer >= self.drop_interval:
                new_pos = self._move(
                    self.player_board, self.shape, self.pos, self.rot, 0, 1
                )
                if new_pos == self.pos:
                    self._lock(self.player_board, self.shape, self.pos, self.rot)
                    self._clear_lines(self.player_board, "player")
                    self._spawn_player()
                else:
                    self.pos = new_pos
                self.timer = 0

        # enemy AI
        if self.net_enabled and self.opponent_id and not self.enemy_ready:
            pass
        elif self.net_enabled and self.opponent_id:
            # Enemy state driven by network; do not run AI.
            pass
        elif not self.enemy_game_over:
            base = ENEMY_FALL_SPEED / 1000.0
            if pygame.time.get_ticks() < self.enemy_boost_until:
                base = base * 0.8
            self.enemy_interval = base
            self.enemy_timer += dt
            if self.enemy_timer >= self.enemy_interval:
                self.enemy_timer = 0
                if (
                    not hasattr(self, "enemy_plan")
                    or self.enemy_plan.get("shape") != self.enemy_shape
                ):
                    best_x, best_rot, best_score = (
                        self.enemy_pos[0],
                        self.enemy_rot,
                        9999,
                    )
                    for test_rot in range(4):
                        for test_x in range(-2, GRID_W + 2):
                            test_pos = [test_x, 0]
                            while self._valid(
                                self.enemy_board,
                                self._cells(self.enemy_shape, test_pos, test_rot),
                            ):
                                test_pos[1] += 1
                            test_pos[1] -= 1
                            if test_pos[1] < 0:
                                continue
                            temp = [row[:] for row in self.enemy_board]
                            for x, y in self._cells(
                                self.enemy_shape, test_pos, test_rot
                            ):
                                if 0 <= x < GRID_W and 0 <= y < GRID_H:
                                    temp[y][x] = self.enemy_shape
                            heights = [0] * GRID_W
                            holes = 0
                            for x in range(GRID_W):
                                block_seen = False
                                for y in range(GRID_H):
                                    if temp[y][x]:
                                        block_seen = True
                                    elif block_seen:
                                        holes += 1
                                for y in range(GRID_H):
                                    if temp[y][x]:
                                        heights[x] = GRID_H - y
                                        break
                            max_h = max(heights)
                            bump = sum(
                                abs(heights[i] - heights[i + 1])
                                for i in range(GRID_W - 1)
                            )
                            score = max_h + holes * 2 + bump * 0.5
                            if score < best_score:
                                best_score, best_x, best_rot = score, test_x, test_rot
                    self.enemy_plan = {
                        "shape": self.enemy_shape,
                        "x": best_x,
                        "rot": best_rot,
                        "rest": 0,
                    }
                plan = self.enemy_plan
                if self.enemy_rot != plan["rot"]:
                    self.enemy_rot = self._rotate(
                        self.enemy_board,
                        self.enemy_shape,
                        self.enemy_pos,
                        self.enemy_rot,
                    )
                elif abs(plan["x"] - self.enemy_pos[0]) > 0:
                    step = 1 if plan["x"] > self.enemy_pos[0] else -1
                    self.enemy_pos = self._move(
                        self.enemy_board,
                        self.enemy_shape,
                        self.enemy_pos,
                        self.enemy_rot,
                        step,
                        0,
                    )
                else:
                    new_pos = self._move(
                        self.enemy_board,
                        self.enemy_shape,
                        self.enemy_pos,
                        self.enemy_rot,
                        0,
                        1,
                    )
                    if new_pos == self.enemy_pos:
                        plan["rest"] += 1
                        if plan["rest"] >= 2:
                            self._lock(
                                self.enemy_board,
                                self.enemy_shape,
                                self.enemy_pos,
                                self.enemy_rot,
                            )
                            self._clear_lines(self.enemy_board, "enemy")
                            self._spawn_enemy()
                            del self.enemy_plan
                    else:
                        plan["rest"] = 0
                        self.enemy_pos = new_pos

        now = pygame.time.get_ticks()
        if self.flash_active_p and now - self.flash_timer_p > 600:
            self.flash_active_p = False
        if self.flash_active_e and now - self.flash_timer_e > 600:
            self.flash_active_e = False

        # Periodically send state in PvP.
        if self.net_enabled and self.opponent_id and not self.pending_outcome:
            self._net_state_timer += dt
            if self._net_state_timer >= self._net_state_interval:
                self._net_state_timer = 0.0
                self._net_send_state()

    # --- End match ------------------------------------------------------------
    def _end_match(self):
        if self.pending_outcome:
            return
        self.match_ended = True
        reason = "time_up"
        if self.game_over and not self.enemy_game_over:
            reason = "player_top_out"
        elif self.enemy_game_over and not self.game_over:
            reason = "enemy_top_out"
        if self.game_over and not self.enemy_game_over:
            winner = "enemy"
        elif self.enemy_game_over and not self.game_over:
            winner = "player"
        else:
            if self.score > self.enemy_score:
                winner = "player"
            elif self.enemy_score > self.score:
                winner = "enemy"
            else:
                # In multiplayer, avoid unresolved ties by picking a deterministic winner.
                if self.net_enabled and self.local_id and self.opponent_id:
                    pair = sorted([self.local_id, self.opponent_id])
                    # winner is lexicographically first id; swap to player/enemy perspective.
                    winner_id = pair[0]
                    winner = "player" if winner_id == self.local_id else "enemy"
                else:
                    winner = "tie"
        self.final_winner = winner
        outcome = "win" if winner == "player" else "lose" if winner == "enemy" else "tie"
        elapsed = max(0.0, time.time() - self.start_time)
        payload = {
            "player_score": self.score,
            "enemy_score": self.enemy_score,
            "player_credits": self.credits,
            "enemy_credits": self.enemy_credits,
            "elapsed": round(elapsed, 2),
            "reason": reason,
        }
        if self.duel_id and self.opponent_id and self.local_id:
            if outcome == "win":
                payload["winner_id"] = self.local_id
                payload["loser_id"] = self.opponent_id
            elif outcome == "lose":
                payload["winner_id"] = self.opponent_id
                payload["loser_id"] = self.local_id
        self.pending_payload = payload
        self.pending_outcome = outcome
        subtitle = f"{self.score} - {self.enemy_score}"
        self.banner.show(outcome, subtitle=subtitle)
        # Notify opponent and host.
        if self.net_enabled and self.net_client:
            try:
                self._net_send_action(
                    {
                        "kind": "outcome",
                        "duel_id": self.duel_id,
                        "outcome": outcome,
                        "player_score": self.score,
                        "enemy_score": self.enemy_score,
                        "winner_id": payload.get("winner_id"),
                        "loser_id": payload.get("loser_id"),
                        "reason": reason,
                    }
                )
            except Exception:
                pass
        self._push_duel_result(outcome, send_net_result=True)
        if self.net_enabled:
            # In multiplayer, finish immediately so both sides stay in sync.
            self._finalize(outcome)
            return

    # --- Input ---------------------------------------------------------------
    def handle_event(self, e):
        if self.pending_outcome:
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._pause_game()
            elif e.key in (pygame.K_LEFT, pygame.K_a):
                self.pos = self._move(
                    self.player_board, self.shape, self.pos, self.rot, -1, 0
                )
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                self.pos = self._move(
                    self.player_board, self.shape, self.pos, self.rot, 1, 0
                )
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                self.pos = self._move(
                    self.player_board, self.shape, self.pos, self.rot, 0, 1
                )
            elif e.key in (pygame.K_UP, pygame.K_w):
                self.rot = self._rotate(
                    self.player_board, self.shape, self.pos, self.rot
                )
            elif e.key == pygame.K_SPACE:
                while True:
                    new_pos = self._move(
                        self.player_board, self.shape, self.pos, self.rot, 0, 1
                    )
                    if new_pos == self.pos:
                        self._lock(self.player_board, self.shape, self.pos, self.rot)
                        self._clear_lines(self.player_board, "player")
                        self._spawn_player()
                        break
                    self.pos = new_pos

        # Keyboard shortcuts for powers: 1-5
        if e.type == pygame.KEYDOWN and not self.match_ended:
            key_to_pid = {
                pygame.K_1: P_SPEED_EN,
                pygame.K_2: P_FORCEDROP,
                pygame.K_3: P_GARBAGE1,
                pygame.K_4: P_STEAL_NEXT,
                pygame.K_5: P_CLEANSELF,
            }
            if e.key in key_to_pid:
                pid = key_to_pid[e.key]
                now = pygame.time.get_ticks()
                if (
                    self.pending_btn == pid
                    and now - self.pending_started <= CONFIRM_WINDOW_MS
                ):
                    self.pending_btn = None
                    self.pending_started = 0
                    self._apply_power(pid)
                else:
                    self.pending_btn = pid
                    self.pending_started = now
                return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not self.match_ended:
            mx, my = e.pos
            try:
                scale = self._blit_scale
                ox, oy = self._blit_offset
            except Exception:
                scale, ox, oy = 1.0, 0, 0
            bx, by = (mx - ox) / scale, (my - oy) / scale
            for idx, rect in enumerate(BUTTON_SLOTS):
                pid = [P_SPEED_EN, P_FORCEDROP, P_GARBAGE1, P_STEAL_NEXT, P_CLEANSELF][idx]
                if rect.collidepoint(bx, by):
                    now = pygame.time.get_ticks()
                    if (
                        self.pending_btn == pid
                        and now - self.pending_started <= CONFIRM_WINDOW_MS
                    ):
                        self.pending_btn = None
                        self.pending_started = 0
                        self._apply_power(pid)
                    else:
                        self.pending_btn = pid
                        self.pending_started = now
                    break

    # --- Draw ----------------------------------------------------------------
    def draw(self):
        if (self.w, self.h) != self.manager.size:
            self.w, self.h = self.manager.size
            self.screen = self.manager.screen
        s = self.screen
        bg = self.bg
        if bg.get_size() != (self.w, self.h):
            bg = pygame.transform.smoothscale(bg, (self.w, self.h))
        base = self.base_surf
        base.blit(self.bg, (0, 0))
        draw_board(base, self.player_board, origin=(BOARD_X, BOARD_Y), cell_px=CELL)
        enemy_offset_x = BASE_W - BOARD_X - GRID_W * CELL
        for r in range(GRID_H):
            for c in range(GRID_W):
                val = self.enemy_board[r][c]
                if val:
                    x = enemy_offset_x + c * CELL
                    y = BOARD_Y + r * CELL
                    pygame.draw.rect(
                        base,
                        COLORS.get(val, (200, 200, 200)),
                        (x + 1, y + 1, CELL - 2, CELL - 2),
                    )
        draw_dispenser(base, self.shared_queue, center_x=BASE_W // 2, scale=1.0)
        draw_next_slots(base, self.player_next, self.enemy_next, center_x=BASE_W // 2, scale=1.0)

        for shape, pos, rot, col, offx in [
            (self.shape, self.pos, self.rot, COLORS.get(self.shape), BOARD_X),
            (
                self.enemy_shape,
                self.enemy_pos,
                self.enemy_rot,
                COLORS.get(self.enemy_shape),
                enemy_offset_x,
            ),
        ]:
            for x, y in self._cells(shape, pos, rot):
                if 0 <= x < GRID_W and 0 <= y < GRID_H:
                    px = offx + x * CELL
                    py = BOARD_Y + y * CELL
                    pygame.draw.rect(base, col, (px + 1, py + 1, CELL - 2, CELL - 2))

        # timer
        elapsed = time.time() - self.start_time
        remaining = max(0, MATCH_TIME - elapsed)
        mins, secs = int(remaining // 60), int(remaining % 60)
        timer_text = self.font_big.render(f"{mins:02}:{secs:02}", True, (255, 255, 255))
        base.blit(timer_text, (BASE_W // 2 - timer_text.get_width() // 2, 420))

        # credits + score
        base.blit(
            self.font.render(f"Score: {self.score}", True, (255, 255, 255)),
            (BOARD_X + 60, BOARD_Y - 44),
        )
        base.blit(
            self.font.render(f"Score: {self.enemy_score}", True, (255, 100, 100)),
            (enemy_offset_x + 50, BOARD_Y - 44),
        )
        base.blit(
            self.font_big.render(f"{self.credits}¢", True, (255, 255, 0)),
            (BOARD_X + GRID_W * CELL + 70, BOARD_Y + GRID_H * CELL - 40),
        )
        base.blit(
            self.font_big.render(f"{self.enemy_credits}¢", True, (255, 100, 100)),
            (enemy_offset_x - 110, BOARD_Y + GRID_H * CELL - 40),
        )

        # power labels
        now = pygame.time.get_ticks()
        for idx, rect in enumerate(BUTTON_SLOTS):
            pid = [P_SPEED_EN, P_FORCEDROP, P_GARBAGE1, P_STEAL_NEXT, P_CLEANSELF][idx]
            armed = (
                self.pending_btn == pid
                and now - self.pending_started <= CONFIRM_WINDOW_MS
            )
            flashing = (
                pid in self.flash_effects
                and now - self.flash_effects[pid] < FLASH_DURATION_MS
            )
            color = (255, 255, 255)
            if armed:
                color = (255, 255, 120)
            if flashing:
                pulse = 255 - int(
                    (now - self.flash_effects[pid]) / FLASH_DURATION_MS * 255
                )
                color = (255, pulse, pulse)

            text1 = self.font_small.render(POWER_LABEL[pid], True, color)
            text2 = self.font_small.render(f"{POWER_COST[pid]}¢", True, (255, 255, 0))
            base.blit(text1, (rect.centerx - text1.get_width() // 2 - 6, rect.y))
            base.blit(text2, (rect.centerx - text2.get_width() // 2 - 6, rect.y + 20))

        # --- 2-tally indicators ---
        p_base_x = BOARD_X + GRID_W * CELL + 100
        p_base_y = BOARD_Y + GRID_H * CELL - 100
        for i in range(2):
            filled = i < (self.attack_count % 2)
            color = (255, 255, 0) if filled else (80, 80, 80)
            if self.flash_active_p:
                color = (255, 255, 255)
            pygame.draw.circle(base, color, (p_base_x + i * 24, p_base_y), 8)

        e_base_x = enemy_offset_x - 120
        e_base_y = BOARD_Y + GRID_H * CELL - 100
        for i in range(2):
            filled = i < (self.enemy_attack_count % 2)
            color = (255, 100, 100) if filled else (80, 80, 80)
            if self.flash_active_e:
                color = (255, 255, 255)
            pygame.draw.circle(base, color, (e_base_x + i * 24, e_base_y), 8)

        if self.pending_outcome:
            self.banner.draw(base, self.font_big, self.font_small, (BASE_W, BASE_H))

        # scale base surface to screen while preserving aspect ratio
        scale = min(self.w / BASE_W, self.h / BASE_H) if self.w and self.h else 1.0
        scaled_size = (int(BASE_W * scale), int(BASE_H * scale))
        blit_surf = pygame.transform.smoothscale(base, scaled_size)
        ox = (self.w - scaled_size[0]) // 2
        oy = (self.h - scaled_size[1]) // 2
        self._blit_scale = scale
        self._blit_offset = (ox, oy)
        self.screen.blit(blit_surf, (ox, oy))

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[BlockDuel] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    # --------- Net helpers ----------
    def _pack_board(self, board):
        return ["".join(cell if cell else "." for cell in row) for row in board]

    def _unpack_board(self, packed):
        board = []
        for row in packed or []:
            board.append([ch if ch != "." else None for ch in row])
        return board

    def _net_send_action(self, action: dict):
        if not self.net_enabled or not self.net_client or not action:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": action})
        except Exception as exc:
            print(f"[BlockDuel] Failed to send action: {exc}")

    def _net_send_state(self, force=False):
        if not self.net_enabled or not self.net_client:
            return
        state = {
            "kind": "state",
            "board": self._pack_board(self.player_board),
            "shape": self.shape,
            "pos": list(self.pos),
            "rot": int(self.rot),
            "next": self.player_next,
            "game_over": self.game_over,
            "score": self.score,
            "credits": self.credits,
            "attack_count": self.attack_count,
        }
        if force:
            state["force"] = True
        self._net_send_action(state)

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            sender = msg.get("from")
            if sender and sender == self.local_id:
                continue
            action = msg.get("action") or {}
            kind = action.get("kind")
            if kind == "state":
                self._net_apply_state(action)
            elif kind == "power":
                self._net_apply_power_from_opponent(action.get("pid"))
            elif kind == "outcome":
                self._net_apply_outcome(action, sender)
            elif kind == "draw":
                if action.get("duel_id") and self.duel_id and action["duel_id"] != self.duel_id:
                    continue
                piece = action.get("piece")
                if not piece:
                    continue
                # Pop and append a new shape so both queues stay aligned.
                if not self.shared_queue:
                    self.shared_queue = [self._new_shape() for _ in range(6)]
                expected = self.shared_queue.pop(0)
                self.shared_queue.append(self._new_shape())
                if expected != piece:
                    # Realign by inserting the received piece at the front if out of sync.
                    self.shared_queue.insert(0, piece)

    def _net_apply_state(self, action: dict):
        # Incoming opponent state drives enemy board visuals.
        self.enemy_ready = True
        self.battle_started = True
        packed = action.get("board")
        if packed:
            self.enemy_board = self._unpack_board(packed)
        self.enemy_shape = action.get("shape", self.enemy_shape)
        self.enemy_pos = list(action.get("pos", self.enemy_pos))
        self.enemy_rot = int(action.get("rot", self.enemy_rot))
        self.enemy_next = action.get("next", self.enemy_next)
        self.enemy_game_over = bool(action.get("game_over", False))
        self.enemy_score = int(action.get("score", self.enemy_score))
        self.enemy_credits = int(action.get("credits", self.enemy_credits))
        self.enemy_attack_count = int(action.get("attack_count", self.enemy_attack_count))
        # If opponent reports game over, trigger end-match resolution.
        if self.enemy_game_over and not self.match_ended and not self.pending_outcome:
            self._end_match()

    def _net_apply_power_from_opponent(self, pid):
        if not pid:
            return
        now = pygame.time.get_ticks()
        # Apply as if the opponent targeted us.
        if pid == P_GARBAGE1:
            self._add_garbage(self.player_board, 1)
        elif pid == P_SPEED_EN:
            # Speed up our fall rate (penalty).
            self.drop_interval = max(0.05, (FALL_SPEED / 1000.0) * 0.75)
            self.enemy_boost_until = now + 3000
        elif pid == P_FORCEDROP:
            while True:
                new_pos = self._move(
                    self.player_board,
                    self.shape,
                    self.pos,
                    self.rot,
                    0,
                    1,
                )
                if new_pos == self.pos:
                    self._lock(
                        self.player_board,
                        self.shape,
                        self.pos,
                        self.rot,
                    )
                    self._clear_lines(self.player_board, "player")
                    self._spawn_player()
                    break
                self.pos = new_pos
        elif pid == P_STEAL_NEXT:
            # Swap next pieces.
            self.player_next, self.enemy_next = self.enemy_next, self.player_next
        elif pid == P_CLEANSELF:
            # Clean our own board (opponent used self-clean)
            self.player_board = [[None for _ in range(GRID_W)] for _ in range(GRID_H)]

    def _net_apply_outcome(self, action: dict, sender=None):
        if self.pending_outcome or self.match_ended:
            return
        # Ignore outcomes for other duels.
        if action.get("duel_id") and self.duel_id and action["duel_id"] != self.duel_id:
            return
        # Use opponent-provided scores if present (swap to our perspective).
        if sender and sender == self.opponent_id:
            if "player_score" in action:
                val = action.get("player_score")
                if val is not None:
                    self.enemy_score = int(val)
            if "enemy_score" in action:
                val = action.get("enemy_score")
                if val is not None:
                    self.score = int(val)
        else:
            if "player_score" in action:
                val = action.get("player_score")
                if val is not None:
                    self.score = int(val)
            if "enemy_score" in action:
                val = action.get("enemy_score")
                if val is not None:
                    self.enemy_score = int(val)

        outcome = action.get("outcome")
        if sender and sender == self.opponent_id:
            if outcome == "win":
                outcome_local = "lose"
            elif outcome == "lose":
                outcome_local = "win"
            else:
                outcome_local = outcome or "tie"
        else:
            outcome_local = outcome or "tie"

        winner_id = action.get("winner_id")
        loser_id = action.get("loser_id")
        if not outcome_local or outcome_local not in ("win", "lose", "tie", "forfeit"):
            if winner_id:
                outcome_local = "win" if winner_id == self.local_id else "lose"
            else:
                outcome_local = "tie"

        self.match_ended = True
        self.final_winner = (
            "player" if outcome_local == "win" else "enemy" if outcome_local == "lose" else "tie"
        )
        self.pending_payload = {
            "player_score": self.score,
            "enemy_score": self.enemy_score,
        }
        if action.get("reason"):
            self.pending_payload["reason"] = action["reason"]
        if winner_id:
            self.pending_payload["winner_id"] = winner_id
        if loser_id:
            self.pending_payload["loser_id"] = loser_id
        self.pending_outcome = outcome_local
        subtitle = f"{self.score} - {self.enemy_score}"
        self.banner.show(outcome_local, subtitle=subtitle)
        self._push_duel_result(outcome_local, send_net_result=False)
        # Finish immediately to keep both sides in sync.
        self._finalize(outcome_local)
    def _finalize(self, outcome):
        if self._completed:
            return
        self._completed = True
        self.pending_outcome = None
        if self.context is None:
            self.context = GameContext()
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if self.duel_id:
            self.context.last_result["duel_id"] = self.duel_id
            if self.opponent_id and self.local_id:
                if outcome == "win":
                    self.context.last_result["winner"] = self.local_id
                    self.context.last_result["loser"] = self.opponent_id
                elif outcome in ("lose", "forfeit"):
                    self.context.last_result["winner"] = self.opponent_id
                    self.context.last_result["loser"] = self.local_id
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[BlockDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[BlockDuel] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self.pending_payload = {"reason": "forfeit"}
        self.pending_outcome = "forfeit"
        self.match_ended = True
        self.banner.show("forfeit", subtitle="Forfeit")
        if self.net_enabled:
            self._push_duel_result("forfeit", send_net_result=True)
            self._finalize("forfeit")

    # ---------- Result / networking helpers ----------
    def _push_duel_result(self, outcome: str, send_net_result: bool = True):
        """Package and send duel result to the host so arenas resolve like RPS."""
        if self._outcome_sent and send_net_result:
            return
        # Send an explicit duel_result up to the host so elimination happens even if the opponent lingers.
        if send_net_result and self.net_enabled and self.net_client and self.duel_id:
            try:
                winner_id = self.pending_payload.get("winner_id")
                loser_id = self.pending_payload.get("loser_id")
                if winner_id and loser_id:
                    outcome = "win" if winner_id == self.local_id else "lose"
                payload = {
                    "duel_id": self.duel_id,
                    "minigame": self.minigame_id,
                    "outcome": outcome,
                    "player_id": self.local_id,
                }
                if self.opponent_id and self.local_id:
                    if winner_id and loser_id:
                        payload["winner"] = winner_id
                        payload["loser"] = loser_id
                    elif outcome == "win":
                        payload["winner"] = self.local_id
                        payload["loser"] = self.opponent_id
                    elif outcome in ("lose", "forfeit"):
                        payload["winner"] = self.opponent_id
                        payload["loser"] = self.local_id
                self.net_client.send_duel_result(payload)
                self._outcome_sent = True
            except Exception as exc:
                print(f"[BlockDuel] Failed to send duel_result: {exc}")


# -----------------------------------------------------------------------------
def launch(manager, context=None, callback=None, difficulty=1.0, **kwargs):
    global SCREEN
    SCREEN = manager.screen
    return BlockDuelScene(manager, context, callback, difficulty, **kwargs)
