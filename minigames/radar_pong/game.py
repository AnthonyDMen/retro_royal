# minigames/radar_pong/game.py
# ---- Custom Vector PONG: rectangular court + BOOST WALL ZONES + BOOSTER CIRCLES ----
# Classic paddles; left/right are goals; top/bottom are walls with special
# boost segments. Booster circles inside the court give a forward speed boost
# (now stronger and exposed as a simple variable). Every 10 seconds the ball
# speeds up globally. First to 3 wins. Fits the minigame format (Scene + TITLE + launch()).

import math
import random
import time
import pygame

from game_context import GameContext
from minigames.shared.end_banner import EndBanner
from scene_manager import Scene

MULTIPLAYER_ENABLED = True

try:
    from content_registry import load_game_fonts
except Exception:

    def load_game_fonts():
        big = pygame.font.SysFont(None, 48)
        med = pygame.font.SysFont(None, 28)
        small = pygame.font.SysFont(None, 20)
        return big, med, small


TITLE = "Radar Pong"
MAX_SCORE = 5

# Colors (vector look)
COL_BG = (8, 10, 12)
COL_LINES = (36, 200, 160)
COL_P1 = (230, 250, 250)
COL_P2 = (255, 230, 120)
COL_BALL = (120, 255, 200)
COL_UI = (220, 235, 240)
COL_BOOST = (120, 200, 255)
COL_NODE = (90, 210, 255)


class RadarPongScene(Scene):
    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.minigame_id = "radar_pong"
        self.big, self.font, self.small = load_game_fonts()
        self.mode = kwargs.get("mode", "solo")  # "solo" | "versus" | "mp"
        self._completed = False
        self.pending_outcome = None
        banner_titles = {
            "win": "Radar Pong Cleared!",
            "lose": "Radar Pong Failed",
        }
        banner_duration = float(kwargs.get("banner_duration", 2.0))
        self.banner = EndBanner(duration=banner_duration, titles=banner_titles)

        # --- Court (simple rectangle) ---
        margin_x = int(self.w * 0.08)
        margin_y = int(self.h * 0.08)
        self.court = pygame.Rect(
            margin_x, margin_y, self.w - 2 * margin_x, self.h - 2 * margin_y
        )
        self.center = self.court.center
        self.LEFT_X = float(self.court.left)
        self.RIGHT_X = float(self.court.right)
        self.TOP_Y = float(self.court.top)
        self.BOT_Y = float(self.court.bottom)

        # --- Paddles (rectangles with beveled ends) ---
        self.pw = max(12, self.court.w // 80)
        self.ph = max(64, self.court.h // 5)
        pad_off = self.court.w // 18
        self.p1 = pygame.Rect(
            self.court.left + pad_off, self.center[1] - self.ph // 2, self.pw, self.ph
        )
        self.p2 = pygame.Rect(
            self.court.right - pad_off - self.pw,
            self.center[1] - self.ph // 2,
            self.pw,
            self.ph,
        )
        self.paddle_speed = 360.0
        # beveled ends: top/bottom zones and angle strength
        self.bevel_frac = 0.22  # top/bottom 22% are beveled
        self.bevel_angle_deg = 16

        # --- AI (beatable) ---
        self.difficulty = max(0.5, float(kwargs.get("difficulty", 1.0)))
        self.ai_speed = 300.0 * (0.8 + 0.25 * (self.difficulty - 1.0))

        # --- Ball ---
        self.ball_r = max(6, self.court.w // 120)
        self.ball_speed = 400.0
        self.ball_speed_min = 320.0
        self.ball_speed_max = 760.0
        self.ball_pos = pygame.Vector2(*self.center)
        self.ball_vel = pygame.Vector2(0, 0)
        self.serve_dir = random.choice([-1, 1])  # +1 → right, -1 → left
        self.between_points = True
        self.serve_timer = 0.8
        self.last_state_sync = time.perf_counter()

        # --- Boost wall zones on TOP/BOTTOM walls ---
        # Each zone: (side, x0, x1, boost_mult). Side: 'top' or 'bot'
        span = self.court.w * 0.18  # width of a boost segment
        pad = self.court.w * 0.06  # gap from side goals
        cx = float(self.court.centerx)
        L = float(self.court.left)
        R = float(self.court.right)
        self.boost_zones = [
            ("top", L + pad, L + pad + span, 1.30),
            ("top", cx - span * 0.5, cx + span * 0.5, 1.22),
            ("top", R - pad - span, R - pad, 1.30),
            ("bot", L + pad * 1.1, L + pad * 1.1 + span, 1.30),
            ("bot", cx - span * 0.45, cx + span * 0.45, 1.22),
            ("bot", R - pad - span * 1.0, R - pad, 1.30),
        ]
        # Random deflection range when hitting a boost zone (radians)
        self.boost_jitter = math.radians(22)

        # --- Booster circles (permanent, evenly spaced) ---
        # IMPORTANT: the node boost multiplier is a single, easy knob.
        # You can also pass node_boost_mult via launch(..., node_boost_mult=1.4)
        self.node_boost_mult = float(kwargs.get("node_boost_mult", 1.65))
        self.node_rows = 3
        self.node_cols = 3
        self.node_r = max(10, self.court.w // 70)
        self.node_mult = self.node_boost_mult  # alias used by physics
        self.nodes = []  # dicts: {pos: Vector2, active: bool}
        # center grid inside ~60% of the court area (more hits)
        inner_w = self.court.w * 0.60
        inner_h = self.court.h * 0.60
        inner_left = self.court.centerx - inner_w * 0.5
        inner_top = self.court.centery - inner_h * 0.5
        xs = [
            inner_left + i * (inner_w) / (self.node_cols - 1)
            for i in range(self.node_cols)
        ]
        ys = [
            inner_top + j * (inner_h) / (self.node_rows - 1)
            for j in range(self.node_rows)
        ]
        for y in ys:
            for x in xs:
                self.nodes.append({"pos": pygame.Vector2(x, y), "active": True})

        # --- Global speed-up every 10s while ball is in play ---
        self.speedup_period = 10.0
        self.speedup_mult = 1.10
        self._speedup_timer = self.speedup_period
        # active boost state (persists for a while after a hit)
        self.boost_duration_wall = 1.6
        self.boost_duration_node = 1.4
        self.boost_timer = 0.0
        self.boost_active_mult = 1.0
        # node hit cooldown to prevent rapid re-triggers when skimming a node
        self.node_cd = 0.12
        self._node_cd_timer = 0.0

        # --- Physics integrator (fixed step) ---
        self.fixed_dt = 1.0 / 300.0
        self._accum = 0.0
        # Client-side smoothing targets (used on non-authority peers)
        self.ball_target = pygame.Vector2(self.ball_pos)
        self.p1_target_y = self.p1.y
        self.p2_target_y = self.p2.y

        # --- Score/state ---
        self.score_l = 0
        self.score_r = 0
        self.end_timer = 0.0

        # Multiplayer plumbing
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or flags.get("duel_id")
        self.participants = kwargs.get("participants") or flags.get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or flags.get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or flags.get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.is_authority = (not self.net_enabled) or (self.local_idx == 0)  # host/solo authoritative
        if self.net_enabled:
            self.mode = "mp"
        self.net_timer = 0.0
        self.net_interval = 1.0 / 20.0
        self.remote_input = {"up": False, "down": False}
        self.remote_last = time.perf_counter()
        self.pending_payload = {}
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ---------------- helpers ----------------
    @staticmethod
    def _clamp_paddle(rect, top, bottom):
        if rect.top < top:
            rect.top = top
        if rect.bottom > bottom:
            rect.bottom = bottom

    def _rand_node_pos(self):
        # Keep nodes away from edges and paddle lanes a bit
        mX = max(60, int(self.court.w * 0.10))
        mY = max(40, int(self.court.h * 0.10))
        x = random.uniform(self.court.left + mX, self.court.right - mX)
        y = random.uniform(self.court.top + mY, self.court.bottom - mY)
        return pygame.Vector2(x, y)

    def _reset_ball(self):
        self.ball_pos.update(self.center)
        self.ball_vel.update(0, 0)
        self.between_points = True
        self.serve_timer = 0.7
        self._speedup_timer = self.speedup_period

    def _start_serve_if_ready(self):
        if self.between_points and self.serve_timer <= 0:
            ang = 0 if self.serve_dir > 0 else math.pi
            ang += random.uniform(-0.25, 0.25)
            v = pygame.Vector2()
            v.from_polar((self.ball_speed, math.degrees(ang)))
            self.ball_vel = v
            self.between_points = False

    # First time (>=0) the moving point p0 + v*t reaches circle radius R around c
    def _circle_enter_time(self, p0, v, c, R):
        u = pygame.Vector2(p0.x - c.x, p0.y - c.y)
        a = v.x * v.x + v.y * v.y
        if a <= 1e-9:
            return False, 0.0
        b = 2 * (u.x * v.x + u.y * v.y)
        cc = u.x * u.x + u.y * u.y - R * R
        disc = b * b - 4 * a * cc
        if disc < 0:
            return False, 0.0
        s = math.sqrt(disc)
        t0 = (-b - s) / (2 * a)
        t1 = (-b + s) / (2 * a)
        t = None
        if 0 <= t0:
            t = t0
        if (t is None or t1 < t) and 0 <= t1:
            t = t1
        if t is None:
            return False, 0.0
        return True, t

    def _score_left(self):
        self.score_l += 1
        self.serve_dir = 1
        if self.score_l >= MAX_SCORE:
            self.ball_vel.update(0, 0)
            self.between_points = True
            self._queue_outcome("win", winner_idx=0)
        else:
            self._reset_ball()

    def _score_right(self):
        self.score_r += 1
        self.serve_dir = -1
        if self.score_r >= MAX_SCORE:
            self.ball_vel.update(0, 0)
            self.between_points = True
            self._queue_outcome("lose", winner_idx=1)
        else:
            self._reset_ball()

    def _queue_outcome(self, outcome: str, winner_idx: int | None = None):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        score_line = f"{self.score_l} - {self.score_r}"
        self.banner.show(outcome, subtitle=score_line)
        if self.net_enabled:
            if winner_idx is None:
                winner_idx = 0 if outcome == "win" else 1
            loser_idx = 1 - winner_idx
            winner_id = self.participants[winner_idx] if len(self.participants) > winner_idx else None
            loser_id = self.participants[loser_idx] if len(self.participants) > loser_idx else None
            self.pending_payload = {"winner": winner_id, "loser": loser_id}
            if self.is_authority:
                self._net_send_state(kind="finish", force=True, winner=winner_id, loser=loser_id, outcome=outcome)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[RadarPong] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome: str):
        if self._completed:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        if self.net_enabled and self.local_idx == 1:
            score_payload = {"player": self.score_r, "opponent": self.score_l}
        else:
            score_payload = {"player": self.score_l, "opponent": self.score_r}
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "score": score_payload,
        }
        if self.pending_payload:
            self.context.last_result.update(self.pending_payload)
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[RadarPong] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[RadarPong] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._completed:
            return
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            self._finalize("forfeit")

    # --------------- networking ---------------
    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[RadarPong] net send failed: {exc}")

    def _pack_state(self):
        return {
            "ball": [self.ball_pos.x, self.ball_pos.y],
            "vel": [self.ball_vel.x, self.ball_vel.y],
            "p1y": self.p1.y,
            "p2y": self.p2.y,
            "score": [self.score_l, self.score_r],
            "between": self.between_points,
            "serve_timer": self.serve_timer,
            "serve_dir": self.serve_dir,
            "boost_timer": self.boost_timer,
            "boost_mult": self.boost_active_mult,
            "speedup_timer": self._speedup_timer,
        }

    def _apply_state(self, st: dict):
        if not st:
            return
        try:
            b = st.get("ball")
            v = st.get("vel")
            if b and len(b) == 2:
                self.ball_target.update(float(b[0]), float(b[1]))
            if v and len(v) == 2:
                self.ball_vel.update(float(v[0]), float(v[1]))
            if "p1y" in st:
                self.p1_target_y = float(st.get("p1y"))
            if "p2y" in st:
                self.p2_target_y = float(st.get("p2y"))
            sc = st.get("score") or [self.score_l, self.score_r]
            if len(sc) == 2:
                self.score_l, self.score_r = int(sc[0]), int(sc[1])
            self.between_points = bool(st.get("between", self.between_points))
            self.serve_timer = float(st.get("serve_timer", self.serve_timer))
            self.serve_dir = int(st.get("serve_dir", self.serve_dir))
            self.boost_timer = float(st.get("boost_timer", self.boost_timer))
            self.boost_active_mult = float(st.get("boost_mult", self.boost_active_mult))
            self._speedup_timer = float(st.get("speedup_timer", self._speedup_timer))
        except Exception:
            pass

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        now = time.perf_counter()
        if not force and (now - self.net_timer) < self.net_interval:
            return
        self.net_timer = now
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
            action = msg.get("action") or {}
            self._apply_remote_action(action)

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind == "input":
            self.remote_input["up"] = bool(action.get("up"))
            self.remote_input["down"] = bool(action.get("down"))
            self.remote_last = time.perf_counter()
            return
        if kind == "serve":
            if self.is_authority and self.between_points:
                self.serve_timer = 0
            return
        if kind == "state":
            st = action.get("state") or {}
            if not self.is_authority:
                self._apply_state(st)
            return
        if kind == "finish":
            win_id = action.get("winner")
            lose_id = action.get("loser")
            outcome = action.get("outcome")
            mapped = outcome
            if win_id or lose_id:
                if win_id == self.local_id:
                    mapped = "win"
                elif lose_id == self.local_id:
                    mapped = "lose"
            self.pending_payload = {"winner": win_id, "loser": lose_id}
            self.pending_outcome = mapped
            self.banner.show(mapped, subtitle=f"{self.score_l} - {self.score_r}")
            return

    # ---------------- scene API ----------------
    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if (
            event.type == pygame.KEYDOWN
            and self.between_points
            and event.key == pygame.K_SPACE
        ):
            if self.is_authority:
                self.serve_timer = 0
            elif self.net_enabled:
                self._net_send_action({"kind": "serve"})

    def _ai_tick(self, dt):
        # Simple proportional follow on Y for rectangle paddle
        target = self.ball_pos.y
        if abs(target - self.p2.centery) > 2:
            vel = self.ai_speed if target > self.p2.centery else -self.ai_speed
            self.p2.y += vel * dt
            self._clamp_paddle(self.p2, self.court.top, self.court.bottom)

    def _apply_boost(self, side, x_at):
        """Top/Bottom wall boost segments: if hit within a segment, add speed and jitter.
        Also arm a timed boost so speed floor persists for a while.
        """
        for s, x0, x1, mult in self.boost_zones:
            if s == side and x0 <= x_at <= x1:
                speed = min(self.ball_vel.length() * mult, self.ball_speed_max)
                ang = math.atan2(self.ball_vel.y, self.ball_vel.x)
                ang += random.uniform(-self.boost_jitter, self.boost_jitter)
                self.ball_vel.from_polar((speed, math.degrees(ang)))
                # persistent boost
                self.boost_timer = max(self.boost_timer, self.boost_duration_wall)
                self.boost_active_mult = max(self.boost_active_mult, mult)
                return True
        # keep a floor so rallies don't stall
        if self.ball_vel.length() < self.ball_speed_min:
            self.ball_vel.scale_to_length(self.ball_speed_min)
        return False

    def _enforce_boost_floor(self):
        if self.boost_timer > 0:
            floor_speed = min(
                self.ball_speed_max,
                max(self.ball_speed_min, self.ball_speed * self.boost_active_mult),
            )
            if self.ball_vel.length() < floor_speed:
                self.ball_vel.scale_to_length(floor_speed)

    def _circle_hit_time(self, p0, v, c, Rc):
        """Earliest >=0 time when point p0+v*t hits circle center c with radius Rc.
        Returns (hit, t, normal, point). Normal points from center to contact.
        """
        u = pygame.Vector2(p0.x - c.x, p0.y - c.y)
        a = v.x * v.x + v.y * v.y
        if a <= 1e-9:
            return False, 0.0, pygame.Vector2(), pygame.Vector2()
        b = 2 * (u.x * v.x + u.y * v.y)
        cc = u.x * u.x + u.y * u.y - Rc * Rc
        disc = b * b - 4 * a * cc
        if disc < 0:
            return False, 0.0, pygame.Vector2(), pygame.Vector2()
        s = math.sqrt(disc)
        t0 = (-b - s) / (2 * a)
        t1 = (-b + s) / (2 * a)
        t = None
        if 0 <= t0:
            t = t0
        if (t is None or t1 < t) and 0 <= t1:
            t = t1
        if t is None:
            return False, 0.0, pygame.Vector2(), pygame.Vector2()
        hit_vec = u + v * t
        if hit_vec.length_squared() == 0:
            n = pygame.Vector2(1, 0)
        else:
            n = hit_vec.normalize()
        pt = pygame.Vector2(c.x + n.x * Rc, c.y + n.y * Rc)
        return True, t, n, pt

    # Continuous collision (swept) vs paddles, goals (left/right planes), top/bottom walls, and node boosts
    def _swept_step(self, dt):
        remain = dt
        MAX_SWEEPS = 6
        while remain > 1e-6 and MAX_SWEEPS > 0:
            MAX_SWEEPS -= 1
            p0 = pygame.Vector2(self.ball_pos)
            vx, vy = self.ball_vel.x, self.ball_vel.y
            step = remain
            events = []

            # Top / Bottom walls
            if vy < 0:
                t = (self.TOP_Y + self.ball_r - p0.y) / vy
                if 0 <= t <= step:
                    events.append((t, ("wall", "top")))
            elif vy > 0:
                t = (self.BOT_Y - self.ball_r - p0.y) / vy
                if 0 <= t <= step:
                    events.append((t, ("wall", "bot")))

            # Left paddle and left goal (past left edge)
            if vx < 0:
                # left paddle face
                face_x = self.p1.right + self.ball_r
                t = (face_x - p0.x) / vx
                if 0 <= t <= step:
                    y_at = p0.y + vy * t
                    if (
                        (self.p1.top - self.ball_r)
                        <= y_at
                        <= (self.p1.bottom + self.ball_r)
                    ):
                        events.append((t, "p1"))
                # left goal plane
                t_goal = ((self.LEFT_X - self.ball_r) - p0.x) / vx
                if 0 <= t_goal <= step:
                    events.append((t_goal, "goal_left"))

            # Right paddle and right goal
            if vx > 0:
                face_x = self.p2.left - self.ball_r
                t = (face_x - p0.x) / vx
                if 0 <= t <= step:
                    y_at = p0.y + vy * t
                    if (
                        (self.p2.top - self.ball_r)
                        <= y_at
                        <= (self.p2.bottom + self.ball_r)
                    ):
                        events.append((t, "p2"))
                t_goal = ((self.RIGHT_X + self.ball_r) - p0.x) / vx
                if 0 <= t_goal <= step:
                    events.append((t_goal, "goal_right"))

            # Booster circle entries (earliest wins)
            if self._node_cd_timer <= 0:
                Rsum = self.node_r + self.ball_r
                for i, node in enumerate(self.nodes):
                    if not node["active"]:
                        continue
                    hit, tN = self._circle_enter_time(
                        p0, self.ball_vel, node["pos"], Rsum
                    )
                    if hit and 0 <= tN <= step:
                        events.append((tN, ("node", i)))

            if not events:
                self.ball_pos = p0 + self.ball_vel * step
                break

            tmin, kind = min(events, key=lambda e: e[0])
            self.ball_pos = p0 + self.ball_vel * tmin
            remain -= tmin

            if kind == "p1":
                # Base reflection off vertical face
                y_at = self.ball_pos.y
                offset = (y_at - self.p1.centery) / (self.p1.height * 0.5)
                offset = max(-1.0, min(1.0, offset))
                vx = abs(self.ball_vel.x)
                vy = self.ball_speed * 0.55 * offset
                # Beveled ends add a small extra rotation if hit in the top/bottom zones
                zone = self.p1.height * self.bevel_frac
                in_top = y_at <= (self.p1.top + zone)
                in_bot = y_at >= (self.p1.bottom - zone)
                if in_top or in_bot:
                    ang = math.atan2(vy, vx)
                    ang += (-1 if in_top else 1) * math.radians(self.bevel_angle_deg)
                    v = pygame.Vector2()
                    v.from_polar(
                        (
                            max(
                                self.ball_speed_min,
                                min(self.ball_speed_max, self.ball_speed),
                            ),
                            math.degrees(ang),
                        )
                    )
                    self.ball_vel = v
                else:
                    self.ball_vel.x = vx
                    self.ball_vel.y = vy
                    self.ball_vel.scale_to_length(
                        max(
                            self.ball_speed_min,
                            min(self.ball_vel.length(), self.ball_speed_max),
                        )
                    )
                self.ball_pos.x = self.p1.right + self.ball_r
                self._enforce_boost_floor()
                continue
            if kind == "p2":
                y_at = self.ball_pos.y
                offset = (y_at - self.p2.centery) / (self.p2.height * 0.5)
                offset = max(-1.0, min(1.0, offset))
                vx = -abs(self.ball_vel.x)
                vy = self.ball_speed * 0.55 * offset
                zone = self.p2.height * self.bevel_frac
                in_top = y_at <= (self.p2.top + zone)
                in_bot = y_at >= (self.p2.bottom - zone)
                if in_top or in_bot:
                    ang = math.atan2(vy, vx)
                    ang += (-1 if in_top else 1) * math.radians(self.bevel_angle_deg)
                    v = pygame.Vector2()
                    v.from_polar(
                        (
                            max(
                                self.ball_speed_min,
                                min(self.ball_speed_max, self.ball_speed),
                            ),
                            math.degrees(ang),
                        )
                    )
                    self.ball_vel = v
                else:
                    self.ball_vel.x = vx
                    self.ball_vel.y = vy
                    self.ball_vel.scale_to_length(
                        max(
                            self.ball_speed_min,
                            min(self.ball_vel.length(), self.ball_speed_max),
                        )
                    )
                self.ball_pos.x = self.p2.left - self.ball_r
                self._enforce_boost_floor()
                continue
            if kind == "goal_left":
                self._score_right()
                return
            if kind == "goal_right":
                self._score_left()
                return

            tag, side = kind
            if tag == "wall":
                # reflect on horizontal wall
                self.ball_vel.y *= -1
                # place back inside
                self.ball_pos.y = max(
                    self.TOP_Y + self.ball_r,
                    min(self.BOT_Y - self.ball_r, self.ball_pos.y),
                )
                # apply boost if inside a boost segment
                x_at = self.ball_pos.x
                self._apply_boost(side, x_at)
                self._enforce_boost_floor()
            elif tag == "node":
                idx = side
                # small forward boost (no angle change) and persist timer
                speed = min(
                    self.ball_vel.length() * self.node_mult, self.ball_speed_max
                )
                if speed > 0:
                    self.ball_vel.scale_to_length(speed)
                self.boost_timer = max(self.boost_timer, self.boost_duration_node)
                self.boost_active_mult = max(self.boost_active_mult, self.node_mult)
                # nudge forward slightly so we don't immediately re-trigger
                if self.ball_vel.length_squared() > 0:
                    u = self.ball_vel.normalize()
                    self.ball_pos += u * (self.node_r * 0.3)
                # short cooldown to avoid repeated hits from the same position
                self._node_cd_timer = self.node_cd
                self._enforce_boost_floor()
                # nodes are permanent; no deactivation

    def update(self, dt):
        dt = float(dt)
        self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return

        # Input
        keys = pygame.key.get_pressed()
        # Local paddle control (allow WASD or arrows)
        local_left = self.local_idx == 0
        lp = self.p1 if local_left else self.p2
        up = keys[pygame.K_w] or keys[pygame.K_UP]
        down = keys[pygame.K_s] or keys[pygame.K_DOWN]
        if up:
            lp.y -= self.paddle_speed * dt
        if down:
            lp.y += self.paddle_speed * dt
        self._clamp_paddle(lp, self.court.top, self.court.bottom)

        # Remote paddle control (authority uses remote inputs; solo/versus fallback)
        if self.net_enabled:
            rp = self.p2 if local_left else self.p1
            if self.is_authority:
                if self.remote_input.get("up"):
                    rp.y -= self.paddle_speed * dt
                if self.remote_input.get("down"):
                    rp.y += self.paddle_speed * dt
                self._clamp_paddle(rp, self.court.top, self.court.bottom)
            # non-authority doesn't move remote paddle; state sync will place it
        else:
            if self.mode == "versus":
                if keys[pygame.K_UP]:
                    self.p2.y -= self.paddle_speed * dt
                if keys[pygame.K_DOWN]:
                    self.p2.y += self.paddle_speed * dt
                self._clamp_paddle(self.p2, self.court.top, self.court.bottom)
            else:
                self._ai_tick(dt)

        # Send local input to authority if needed
        if self.net_enabled and not self.is_authority:
            self._net_send_action({"kind": "input", "up": bool(up), "down": bool(down)})

        # Serve timing
        if self.between_points and self.is_authority:
            self.serve_timer -= dt
            self._start_serve_if_ready()

        # Global speed-up while in play
        if not self.between_points and self.is_authority:
            self._speedup_timer -= dt
            if self._speedup_timer <= 0:
                speed = min(
                    self.ball_vel.length() * self.speedup_mult, self.ball_speed_max
                )
                if speed > 0:
                    self.ball_vel.scale_to_length(speed)
                self._speedup_timer += self.speedup_period

        # Decay timed boost
        if self.boost_timer > 0 and self.is_authority:
            self.boost_timer -= dt
            if self.boost_timer <= 0:
                self.boost_active_mult = 1.0

        # Node cooldown timer
        if self._node_cd_timer > 0 and self.is_authority:
            self._node_cd_timer -= dt

        # Nodes are permanent; no respawn

        # Physics
        if self.is_authority:
            self._accum += dt
            steps = 0
            while self._accum >= self.fixed_dt:
                self._accum -= self.fixed_dt
                steps += 1
                if steps > 8:
                    break
                if not self.between_points:
                    self._swept_step(self.fixed_dt)
        else:
            # Non-authority: smooth towards host state for paddles/ball
            lerp_k = min(1.0, dt * 8.0)
            self.ball_pos += (self.ball_target - self.ball_pos) * lerp_k
            self.p1.y += (self.p1_target_y - self.p1.y) * lerp_k
            self.p2.y += (self.p2_target_y - self.p2.y) * lerp_k
            # lightweight prediction between packets
            if not self.between_points and self.ball_vel.length_squared() > 0:
                self.ball_pos += self.ball_vel * dt

        # Sync state to remote if authoritative
        if self.net_enabled and self.is_authority and not self.pending_outcome:
            self._net_send_state()

    # ---------------- drawing ----------------
    def _draw_boost_zones(self):
        # subtle markers on top/bottom
        for side, x0, x1, mult in self.boost_zones:
            y = self.TOP_Y if side == "top" else self.BOT_Y
            h = 6 if side == "top" else -6
            rect = pygame.Rect(
                int(x0), int(y - (h if h > 0 else 0)), int(x1 - x0), abs(h)
            )
            pygame.draw.rect(self.screen, COL_BOOST, rect, 1)

    def _draw_nodes(self):
        for node in self.nodes:
            if node["active"]:
                pygame.draw.circle(
                    self.screen,
                    COL_NODE,
                    (int(node["pos"].x), int(node["pos"].y)),
                    self.node_r,
                    1,
                )

    def draw(self):
        self.screen.fill(COL_BG)
        # Court lines
        pygame.draw.rect(self.screen, COL_LINES, self.court, 2, border_radius=6)
        cx = self.court.centerx
        pygame.draw.line(self.screen, COL_LINES, (cx, self.TOP_Y), (cx, self.BOT_Y), 1)
        pygame.draw.circle(self.screen, COL_LINES, self.court.center, 24, 1)
        # Goals visual (thin verticals at edges)
        pygame.draw.line(
            self.screen,
            COL_LINES,
            (self.LEFT_X, self.TOP_Y),
            (self.LEFT_X, self.BOT_Y),
            1,
        )
        pygame.draw.line(
            self.screen,
            COL_LINES,
            (self.RIGHT_X, self.TOP_Y),
            (self.RIGHT_X, self.BOT_Y),
            1,
        )
        # Visual guides
        self._draw_boost_zones()
        self._draw_nodes()
        # Paddles & ball
        pygame.draw.rect(self.screen, COL_P1, self.p1, border_radius=4)
        pygame.draw.rect(self.screen, COL_P2, self.p2, border_radius=4)
        pygame.draw.circle(
            self.screen,
            COL_BALL,
            (int(self.ball_pos.x), int(self.ball_pos.y)),
            self.ball_r,
        )
        # HUD
        score_text = f"{self.score_l} : {self.score_r}"
        t = self.big.render(score_text, True, COL_UI)
        self.screen.blit(
            t, t.get_rect(center=(self.court.centerx, self.court.top - 28))
        )
        sub = []
        if self.between_points and self.end_timer <= 0 and (not self.net_enabled or self.is_authority):
            sub.append("Space to serve")
        if self.net_enabled:
            sub.append("Move: W/S or Up/Down • First to 5")
        else:
            sub.append("Move: W/S or Up/Down • First to 5")
        for i, line in enumerate(sub):
            s = self.small.render(line, True, COL_UI)
            self.screen.blit(
                s,
                s.get_rect(
                    center=(self.court.centerx, self.court.top - 8 + 18 * (i + 1))
                ),
            )
        if self.pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.w, self.h))

# factory per minigame format

def launch(manager, context, callback, **kwargs):
    return RadarPongScene(manager, context, callback, **kwargs)
