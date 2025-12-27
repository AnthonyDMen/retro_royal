# minigames/vectorsurvival/game.py
import math, random, pygame, time
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from minigames.shared.end_banner import EndBanner

# --- Module metadata & tunables ---
TITLE = "Vector Survival"
MINIGAME_ID = "vectorsurvival"
MULTIPLAYER_ENABLED = True
PALETTE = {
    "bg": (12, 14, 22),
    "frame": (70, 82, 105),
    "rock": (215, 230, 255),
    "ship": (120, 255, 235),
    "ship2": (255, 120, 255),
    "ship_blink": (90, 100, 120),
    "flame": (255, 210, 120),
    "ui_title": (255, 235, 140),
    "ui_text": (210, 210, 220),
    "ui_hint": (200, 200, 210),
    "dim": (0, 0, 0, 170),
    "panel": (35, 38, 55),
    "panel_stroke": (180, 180, 220),
}


SHIP_COLORS = [
    (255, 255, 255),  # white
    (255, 100, 100),  # red
    (100, 255, 100),  # green
    (100, 100, 255),  # blue
    (255, 255, 100),  # yellow
    (255, 100, 255),  # magenta
    (100, 255, 255),  # cyan
    (255, 165, 0),  # orange
]


SURVIVE_SECONDS = 45
PLAYER_LIVES = 2
DIFFICULTY = 1.0
ALLOW_RUNTIME_DIFFICULTY = False
DIFF_MIN, DIFF_MAX, DIFF_STEP = 0.4, 3.0, 0.1

# --- Utility functions ---
def clamp(x, a, b):
    return a if x < a else b if x > b else x

def wrap(x, y, w, h):
    if x < 0:
        x += w
    if x >= w:
        x -= w
    if y < 0:
        y += h
    if y >= h:
        y -= h
    return x, y

def rot(vx, vy, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    return (vx * ca - vy * sa, vx * sa + vy * ca)

class VectorSurvivalScene(Scene):
    def __init__(self, manager, context, callback, mode="solo", **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or flags.get("duel_id")
        raw_participants = kwargs.get("participants") or flags.get("participants") or []
        self.participants = [str(p) for p in raw_participants]
        self.net_client = kwargs.get("multiplayer_client") or flags.get("multiplayer_client")
        local_id = kwargs.get("local_player_id") or flags.get("local_player_id")
        self.local_id = str(local_id) if local_id is not None else None
        self.local_idx = 0
        if self.participants and self.local_id:
            if self.local_id in self.participants:
                self.local_idx = self.participants.index(self.local_id)
            else:
                for idx, pid in enumerate(self.participants):
                    if self.local_id in pid or pid in self.local_id:
                        self.local_idx = idx
                        self.local_id = pid
                        break
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = (
            self.participants[self.remote_idx]
            if len(self.participants) > self.remote_idx
            else None
        )
        self.net_enabled = bool(
            self.duel_id and self.participants and self.net_client and self.local_id in self.participants
        )
        self.is_authority = not self.net_enabled or self.local_idx == 0
        self.mode = "mp" if self.net_enabled else mode
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.big, self.font, self.small = load_game_fonts()
        seed_val = self.duel_id if self.net_enabled else time.time()
        try:
            self.rng = random.Random(int(seed_val, 16))
        except Exception:
            try:
                self.rng = random.Random(int(seed_val))
            except Exception:
                self.rng = random.Random(str(seed_val))
        # difficulty-driven params (auto-scaling)
        self._difficulty = float(DIFFICULTY)
        self._apply_difficulty()
        self.minigame_id = MINIGAME_ID
        self.pending_outcome = None
        self._completed = False
        self.banner = EndBanner(
            duration=2.5,
            titles={
                "win": "Vector Survival Cleared!",
                "lose": "Vector Survival Failed",
                "forfeit": "Vector Survival Forfeit",
            },
        )

        # player ships with random colors
        self.p2 = None
        if self.mode in ("versus", "mp"):
            colors = self.rng.sample(SHIP_COLORS, 2) if len(SHIP_COLORS) >= 2 else [SHIP_COLORS[0], SHIP_COLORS[-1]]
            self.p1 = self._make_ship(self.w * 0.35, self.h * 0.65, color=colors[0])
            self.p1["lives"] = PLAYER_LIVES
            self.p1["inv_ms"] = 1200
            self.p1["name"] = "P1"
            self.p2 = self._make_ship(self.w * 0.65, self.h * 0.5, color=colors[1])
            self.p2["lives"] = PLAYER_LIVES
            self.p2["inv_ms"] = 1200
            self.p2["name"] = "P2"
        else:
            self.p1 = self._make_ship(self.w * 0.35, self.h * 0.65, color=random.choice(SHIP_COLORS))
            self.p1["lives"] = PLAYER_LIVES
            self.p1["inv_ms"] = 1200
            self.p1["name"] = "P1"
        self.ship = self.p1  # legacy reference
        self._p1_target = {"pos": (self.p1["x"], self.p1["y"]), "ang": self.p1["ang"], "vx": 0.0, "vy": 0.0}
        self._p2_target = {"pos": (self.p2["x"], self.p2["y"]), "ang": self.p2["ang"], "vx": 0.0, "vy": 0.0} if self.p2 else None
        self._asteroid_targets = []
        self._got_state = False

        # asteroids
        self.asteroids = []
        for _ in range(self.start_rocks):
            self._spawn_asteroid()

        # timers & state
        self.elapsed = 0.0
        self.spawn_timer = 0.0
        self.speed_scale = 1.0
        self.ramp_timer = 0.0
        self.state = "play"
        self.net_interval = 1.0 / 20.0
        self.net_last = 0.0
        self.remote_input = {"turn": 0.0, "thrust": 0.0}
        self._input_last = {"turn": 0.0, "thrust": 0.0}
        self._input_last_time = 0.0
        self.pending_payload = {}
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ---------- difficulty helpers ----------

    def _apply_difficulty(self):
        d = self._difficulty
        self.start_rocks = max(3, int(4 + 2 * d))
        self.spawn_interval = max(1.4, 6.0 / d)
        self.rock_speed_lo = 40 * d
        self.rock_speed_hi = 110 * d
        self.speed_bump = 0.05 * d
        self.turn_rate = 3.7
        self.accel = 180.0
        self.drag = 0.45
        self.max_speed = 280.0

    def _set_difficulty(self, new_d):
        self._difficulty = max(DIFF_MIN, min(DIFF_MAX, float(new_d)))
        self._apply_difficulty()

    def _apply_controls(self):
        keys = pygame.key.get_pressed()
        if not self.net_enabled:
            # Solo or hotseat
            self.p1["turn"] = (
                -1.0 if (keys[pygame.K_a] or keys[pygame.K_LEFT]) else 0.0
            ) + (1.0 if (keys[pygame.K_d] or keys[pygame.K_RIGHT]) else 0.0)
            self.p1["thrust"] = 1.0 if (keys[pygame.K_w] or keys[pygame.K_UP]) else 0.0
            if self.p2:
                self.p2["turn"] = (
                    -1.0 if keys[pygame.K_LEFT] else 0.0
                ) + (1.0 if keys[pygame.K_RIGHT] else 0.0)
                self.p2["thrust"] = 1.0 if keys[pygame.K_UP] else 0.0
            return

        # Multiplayer: map local/remote
        local_ship = self.p1 if self.local_idx == 0 else self.p2
        remote_ship = self.p2 if self.local_idx == 0 else self.p1
        # Local input (WASD/Arrows)
        local_ship["turn"] = (
            -1.0 if (keys[pygame.K_a] or keys[pygame.K_LEFT]) else 0.0
        ) + (1.0 if (keys[pygame.K_d] or keys[pygame.K_RIGHT]) else 0.0)
        local_ship["thrust"] = 1.0 if (keys[pygame.K_w] or keys[pygame.K_UP]) else 0.0
        # Remote input from network
        remote_ship["turn"] = float(self.remote_input.get("turn", 0.0))
        remote_ship["thrust"] = float(self.remote_input.get("thrust", 0.0))
        if not self.is_authority:
            self._send_local_input(local_ship["turn"], local_ship["thrust"])
    # ---------- entity creators ----------
    def _make_ship(self, x, y, color):
        return {
            "x": x,
            "y": y,
            "vx": 0.0,
            "vy": 0.0,
            "ang": -math.pi / 2,
            "r": 12,
            "color": color,
            "dead": False,
            "inv_ms": 0,
            "lives": PLAYER_LIVES,
            "turn": 0.0,
            "thrust": 0.0,
        }

    def _spawn_asteroid(self):
        # spawn near edges, head inward-ish
        side = self.rng.choice(["top", "bottom", "left", "right"])
        if side == "top":
            x, y = self.rng.uniform(0, self.w), -24
            vx, vy = self.rng.uniform(-50, 50), self.rng.uniform(
                self.rock_speed_lo, self.rock_speed_hi
            )
        elif side == "bottom":
            x, y = self.rng.uniform(0, self.w), self.h + 24
            vx, vy = self.rng.uniform(-50, 50), -self.rng.uniform(
                self.rock_speed_lo, self.rock_speed_hi
            )
        elif side == "left":
            x, y = -24, self.rng.uniform(0, self.h)
            vx, vy = self.rng.uniform(
                self.rock_speed_lo, self.rock_speed_hi
            ), self.rng.uniform(-50, 50)
        else:
            x, y = self.w + 24, self.rng.uniform(0, self.h)
            vx, vy = -self.rng.uniform(
                self.rock_speed_lo, self.rock_speed_hi
            ), self.rng.uniform(-50, 50)

        r = self.rng.uniform(16, 36)
        poly = self._jagged_ngon(int(self.rng.uniform(7, 10)), r)
        self.asteroids.append(
            {"x": x, "y": y, "vx": vx, "vy": vy, "r": r, "poly": poly}
        )

    def _jagged_ngon(self, n, r):
        pts = []
        for i in range(n):
            a = i * (2 * math.pi / n) + self.rng.uniform(-0.08, 0.08)
            rr = r * self.rng.uniform(0.8, 1.15)
            pts.append((rr * math.cos(a), rr * math.sin(a)))
        return pts

    # ---------- input ----------

    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        # Difficulty is auto-ramping; no runtime adjustments

    # ---------- update ----------

    def update(self, dt):
        dt = float(dt)
        if self.net_enabled:
            self._net_poll_actions(dt)
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return
        if self.net_enabled and not self.is_authority:
            # Still gather/send local input before smoothing view.
            self._apply_controls()
            self._client_update(dt)
            return

        self.elapsed += dt
        # Global difficulty ramp: steadily climbs over time for more pressure
        self.ramp_timer += dt
        if self.ramp_timer >= 5.0:
            self.ramp_timer -= 5.0
            self._set_difficulty(self._difficulty + 0.15)

        # controls
        self._apply_controls()

        # physics for both ships
        for s in [self.p1] + ([self.p2] if self.p2 else []):
            s["ang"] += s["turn"] * self.turn_rate * dt
            ax, ay = rot(0, -s["thrust"] * self.accel, s["ang"])
            s["vx"] = (s["vx"] + ax * dt) * (1.0 - self.drag * dt)
            s["vy"] = (s["vy"] + ay * dt) * (1.0 - self.drag * dt)
            sp = math.hypot(s["vx"], s["vy"])
            if sp > self.max_speed:
                k = self.max_speed / max(1e-6, sp)
                s["vx"] *= k
                s["vy"] *= k
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            s["x"], s["y"] = wrap(s["x"], s["y"], self.w, self.h)
            if s["inv_ms"] > 0:
                s["inv_ms"] = max(0, s["inv_ms"] - int(dt * 1000))

        # asteroids
        for a in self.asteroids:
            a["x"] += a["vx"] * dt
            a["y"] += a["vy"] * dt
            a["x"], a["y"] = wrap(a["x"], a["y"], self.w, self.h)

        # pacing
        self.spawn_timer += dt
        if self.spawn_timer >= self.spawn_interval:
            self.spawn_timer -= self.spawn_interval
            self._spawn_asteroid()
            self.speed_scale = min(2.0, self.speed_scale + self.speed_bump)

        # collisions and win check
        if self.mode == "solo":
            if self.state == "play" and self.elapsed >= SURVIVE_SECONDS:
                self.state = "result"
                self.p1["inv_ms"] = 999999
                self._queue_outcome("win", f"Survived {SURVIVE_SECONDS}s")
                return
            self._check_collisions_solo()
        elif self.mode in ("versus", "mp"):
            self._check_collisions_mp()

        if self.net_enabled and self.is_authority:
            self._net_send_state()

    def _check_collisions_solo(self):
        s = self.p1
        if s["inv_ms"] > 0:
            return
        sx, sy, sr = s["x"], s["y"], s["r"]
        for a in self.asteroids:
            dx = sx - a["x"]
            dy = sy - a["y"]
            if abs(dx) > self.w / 2:
                dx -= math.copysign(self.w, dx)
            if abs(dy) > self.h / 2:
                dy -= math.copysign(self.h, dy)
            if dx * dx + dy * dy <= (sr + a["r"]) ** 2:
                s["lives"] -= 1
                s["inv_ms"] = 1200
                if s["lives"] <= 0:
                    self.state = "result"
                    s["inv_ms"] = 999999
                    self._queue_outcome("lose", "Out of lives")
                return

    def _check_collisions_mp(self):
        if self.state != "play":
            return
        for s in [self.p1, self.p2]:
            if not s:
                continue
            if s["inv_ms"] > 0 or s["lives"] <= 0:
                continue
            sx, sy, sr = s["x"], s["y"], s["r"]
            for a in self.asteroids:
                dx = sx - a["x"]
                dy = sy - a["y"]
                if abs(dx) > self.w / 2:
                    dx -= math.copysign(self.w, dx)
                if abs(dy) > self.h / 2:
                    dy -= math.copysign(self.h, dy)
                if dx * dx + dy * dy <= (sr + a["r"]) ** 2:
                    s["lives"] -= 1
                    s["inv_ms"] = 1200
                    if s["lives"] <= 0:
                        s["inv_ms"] = 999999
                        self._resolve_last_man()
                    return

    def _resolve_last_man(self):
        alive = [s for s in (self.p1, self.p2) if s and s.get("lives", 0) > 0]
        if len(alive) >= 2:
            return
        winner_ship = alive[0] if alive else None
        winner_id = loser_id = None
        outcome = "lose"
        reason = "Last ship standing"
        if winner_ship is self.p1:
            winner_id = self.local_id if self.local_idx == 0 else self.remote_id
            loser_id = self.remote_id if self.local_idx == 0 else self.local_id
        elif winner_ship is self.p2:
            winner_id = self.local_id if self.local_idx == 1 else self.remote_id
            loser_id = self.remote_id if self.local_idx == 1 else self.local_id
        if winner_ship is None:
            outcome = "lose"
            reason = "Both ships destroyed"
        else:
            if self.net_enabled:
                if self._ship_is_local(winner_ship):
                    outcome = "win"
                else:
                    outcome = "lose"
            else:
                outcome = "win" if winner_ship is self.p1 else "lose"
        self.pending_payload = {"winner": winner_id, "loser": loser_id, "reason": reason}
        self._queue_outcome(outcome, reason, send_finish=self.net_enabled and self.is_authority)

    # ---------- helpers ----------
    def _ship_is_local(self, ship) -> bool:
        return (ship is self.p1 and self.local_idx == 0) or (ship is self.p2 and self.local_idx == 1)

    def _client_update(self, dt: float):
        # smooth toward targets; advance asteroids a bit for in-between frames
        self.elapsed += dt
        self._smooth_ship(self.p1, self._p1_target, dt)
        if self.p2:
            self._smooth_ship(self.p2, self._p2_target, dt)
        self._smooth_asteroids(dt)
        if self.state == "result" and self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)

    def _smooth_ship(self, ship, target, dt: float):
        if not ship or not target:
            return
        tx, ty = target.get("pos", (ship["x"], ship["y"]))
        lerp_k = min(1.0, dt * 10.0)
        ship["x"] += (tx - ship["x"]) * lerp_k
        ship["y"] += (ty - ship["y"]) * lerp_k
        try:
            tang = float(target.get("ang", ship["ang"]))
            diff = (tang - ship["ang"] + math.pi) % (2 * math.pi) - math.pi
            ship["ang"] += diff * min(1.0, dt * 8.0)
        except Exception:
            pass
        ship["vx"] = float(target.get("vx", ship.get("vx", 0.0)))
        ship["vy"] = float(target.get("vy", ship.get("vy", 0.0)))

    def _smooth_asteroids(self, dt: float):
        if not self._asteroid_targets:
            # fallback: advance locally with vel for minimal motion
            for a in self.asteroids:
                a["x"] += a["vx"] * dt
                a["y"] += a["vy"] * dt
                a["x"], a["y"] = wrap(a["x"], a["y"], self.w, self.h)
            return
        # Snap asteroids to host positions to avoid wrap jitter.
        self.asteroids = []
        for t in self._asteroid_targets:
            self.asteroids.append(
                {
                    "x": t["x"],
                    "y": t["y"],
                    "vx": t["vx"],
                    "vy": t["vy"],
                    "r": t["r"],
                    "poly": t["poly"],
                }
            )

    # ---------- net helpers ----------
    def _pack_ship(self, s):
        return {
            "x": s["x"],
            "y": s["y"],
            "vx": s["vx"],
            "vy": s["vy"],
            "ang": s["ang"],
            "inv_ms": s["inv_ms"],
            "lives": s["lives"],
            "color": s["color"],
            "name": s.get("name"),
        }

    def _apply_ship(self, s, data, target=None, snap=False):
        if not data or not s:
            return
        if snap or not target:
            s["x"] = float(data.get("x", s["x"]))
            s["y"] = float(data.get("y", s["y"]))
            s["ang"] = float(data.get("ang", s["ang"]))
        else:
            target["pos"] = (float(data.get("x", s["x"])), float(data.get("y", s["y"])))
            target["ang"] = float(data.get("ang", s["ang"]))
        s["vx"] = float(data.get("vx", s["vx"]))
        s["vy"] = float(data.get("vy", s["vy"]))
        s["inv_ms"] = int(data.get("inv_ms", s["inv_ms"]))
        s["lives"] = int(data.get("lives", s["lives"]))
        s["color"] = tuple(data.get("color", s["color"]))
        if "name" in data:
            s["name"] = data.get("name", s.get("name"))

    def _pack_state(self):
        ast = [
            {"x": a["x"], "y": a["y"], "vx": a["vx"], "vy": a["vy"], "r": a["r"], "poly": a["poly"]}
            for a in self.asteroids[:80]
        ]
        return {
            "state": self.state,
            "elapsed": self.elapsed,
            "difficulty": self._difficulty,
            "speed_scale": self.speed_scale,
            "spawn_timer": self.spawn_timer,
            "p1": self._pack_ship(self.p1),
            "p2": self._pack_ship(self.p2) if self.p2 else None,
            "asteroids": ast,
            "pending": self.pending_outcome,
            "payload": self.pending_payload,
        }

    def _apply_state(self, st):
        if not st or self._completed:
            return
        self.state = st.get("state", self.state)
        try:
            self.elapsed = float(st.get("elapsed", self.elapsed))
        except Exception:
            pass
        try:
            self._set_difficulty(float(st.get("difficulty", self._difficulty)))
        except Exception:
            pass
        try:
            self.speed_scale = float(st.get("speed_scale", self.speed_scale))
            self.spawn_timer = float(st.get("spawn_timer", self.spawn_timer))
        except Exception:
            pass
        snap = not self._got_state
        self._apply_ship(self.p1, st.get("p1"), target=self._p1_target, snap=snap)
        if self.p2:
            self._apply_ship(self.p2, st.get("p2"), target=self._p2_target, snap=snap)
        ast_targets = []
        for a in st.get("asteroids", []):
            ast_targets.append(
                {
                    "x": float(a.get("x", 0.0)),
                    "y": float(a.get("y", 0.0)),
                    "vx": float(a.get("vx", 0.0)),
                    "vy": float(a.get("vy", 0.0)),
                    "r": float(a.get("r", 0.0)),
                    "poly": a.get("poly", []),
                }
            )
        self._asteroid_targets = ast_targets
        pending = st.get("pending")
        if pending and not self.pending_outcome:
            reason = ""
            payload = st.get("payload") or {}
            if isinstance(payload, dict):
                reason = payload.get("reason", "")
                self.pending_payload = payload
            self._queue_outcome(pending, reason)
        self._got_state = True

    def _net_send_action(self, payload):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[VectorSurvival] Failed to send action: {exc}")

    def _net_send_state(self, force=False):
        if not self.net_enabled or not self.is_authority:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
        self._net_send_action({"kind": "state", "state": self._pack_state()})

    def _net_send_finish(self, outcome: str, reason: str = ""):
        if not self.net_enabled or not self.is_authority:
            return
        winner = None
        loser = None
        if self.local_id and self.remote_id:
            if outcome == "win":
                winner, loser = self.local_id, self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner, loser = self.remote_id, self.local_id
        payload = {"kind": "finish", "outcome": outcome, "winner": winner, "loser": loser, "reason": reason}
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

    def _apply_remote_action(self, action):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "input" and self.is_authority:
            try:
                turn = float(action.get("turn", 0.0))
            except Exception:
                turn = 0.0
            try:
                thrust = float(action.get("thrust", 0.0))
            except Exception:
                thrust = 0.0
            self.remote_input = {"turn": clamp(turn, -1.0, 1.0), "thrust": clamp(thrust, 0.0, 1.0)}
            return
        if kind == "forfeit" and self.is_authority:
            self.pending_payload = {"winner": self.local_id, "loser": self.remote_id, "reason": "opponent forfeit"}
            self._queue_outcome("win", "Opponent forfeited", send_finish=True)
            return
        if kind == "finish":
            if self.is_authority:
                return
            winner = action.get("winner")
            loser = action.get("loser")
            outcome = action.get("outcome") or "lose"
            reason = action.get("reason") or ""
            mapped = outcome
            if winner and self.local_id:
                if winner == self.local_id:
                    mapped = "win"
                elif loser == self.local_id:
                    mapped = "lose"
            payload = {"winner": winner, "loser": loser, "reason": reason}
            self.pending_payload = payload
            self._queue_outcome(mapped, reason)

    def _send_local_input(self, turn: float, thrust: float):
        if not self.net_enabled or self.is_authority:
            return
        turn = clamp(turn, -1.0, 1.0)
        thrust = clamp(thrust, 0.0, 1.0)
        now = time.perf_counter()
        changed = (
            turn != self._input_last.get("turn", 0.0)
            or thrust != self._input_last.get("thrust", 0.0)
        )
        if not changed and (now - self._input_last_time) < self.net_interval:
            return
        self._input_last = {"turn": turn, "thrust": thrust}
        self._input_last_time = now
        self._net_send_action({"kind": "input", "turn": turn, "thrust": thrust})

    # --- wrap-aware drawing helpers ---

    def _screen_rect(self):
        return pygame.Rect(0, 0, self.w, self.h)

    def _poly_bbox(self, pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return pygame.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _draw_poly_wrapped(self, base_pts, color, width=1):
        rect_screen = self._screen_rect()
        for ox in (-self.w, 0, self.w):
            for oy in (-self.h, 0, self.h):
                pts = [(x + ox, y + oy) for (x, y) in base_pts]
                if self._poly_bbox(pts).colliderect(rect_screen):
                    pygame.draw.polygon(
                        self.screen, color, pts, width
                    )

    # ---------- draw ----------

    def draw(self):
        self.screen.fill(PALETTE["bg"])
        pygame.draw.rect(
            self.screen,
            PALETTE["frame"],
            pygame.Rect(6, 6, self.w - 12, self.h - 12),
            1,
        )
        # asteroids (wire polys)
        for a in self.asteroids:
            base_pts = [(a["x"] + px, a["y"] + py) for (px, py) in a["poly"]]
            self._draw_poly_wrapped(base_pts, PALETTE["rock"], 1)

        # ships
        self._draw_ship(self.p1)
        if self.p2:
            self._draw_ship(self.p2)

        # HUD
        t = self.big.render(TITLE, True, PALETTE["ui_title"])
        self.screen.blit(t, (16, 12))

        if self.mode == "solo":
            remain = max(0, int(SURVIVE_SECONDS - self.elapsed + 0.999))
            info = f"Time: {remain}s  •  Lives: {self.p1['lives']}  •  Diff: {self._difficulty:.1f}"
        else:
            info = f"P1 Lives: {self.p1['lives']}  •  P2 Lives: {self.p2['lives']}  •  Diff: {self._difficulty:.1f}"
        hint = self.font.render(
            "Rotate: A/D or Left/Right • Thrust: W or Up • Esc pauses",
            True,
            PALETTE["ui_hint"],
        )
        self.screen.blit(self.font.render(info, True, PALETTE["ui_text"]), (16, 50))
        self.screen.blit(hint, (16, 74))
        if self.pending_outcome:
            self.banner.draw(self.screen, self.big, self.small, (self.w, self.h))

    def _draw_ship(self, s):
        # Dart-like hull
        hull = [(0, -16), (-8, 8), (0, 12), (8, 8)]
        base_pts = []
        for lx, ly in hull:
            rx, ry = rot(lx, ly, s["ang"])
            base_pts.append((s["x"] + rx, s["y"] + ry))

        color = s["color"]
        if s["inv_ms"] > 0 and (pygame.time.get_ticks() // 120) % 2 == 0:
            color = PALETTE["ship_blink"]

        self._draw_poly_wrapped(base_pts, color, 1)

        # Thrust flicker when accelerating
        if s.get("thrust", 0.0) > 0.0 and self.state == "play":
            back_mid = (
                (base_pts[1][0] + base_pts[3][0]) * 0.5,
                (base_pts[1][1] + base_pts[3][1]) * 0.5,
            )
            flick = 1.0 + 0.3 * math.sin(pygame.time.get_ticks() * 0.025)
            fx, fy = rot(0, 18 * flick, s["ang"])
            flame = [base_pts[1], base_pts[3], (back_mid[0] + fx, back_mid[1] + fy)]
            self._draw_poly_wrapped(flame, PALETTE["flame"], 1)

    def _queue_outcome(self, outcome, subtitle="", send_finish=False):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.state = "result"
        self.banner.show(outcome, subtitle=subtitle)
        if send_finish:
            self._net_send_finish(outcome, subtitle)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[VectorSurvival] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed:
            return
        self._completed = True
        if self.context is None:
            self.context = GameContext()
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "mode": self.mode,
            "difficulty": self._difficulty,
            "time": round(self.elapsed, 2),
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.pending_payload:
            result.update({k: v for k, v in self.pending_payload.items() if v is not None})
        if (
            self.net_enabled
            and self.participants
            and len(self.participants) >= 2
            and "winner" not in result
        ):
            winner = None
            loser = None
            if outcome == "win":
                winner, loser = self.local_id, self.remote_id
            elif outcome in ("lose", "forfeit"):
                winner, loser = self.remote_id, self.local_id
            if winner:
                result["winner"] = winner
            if loser:
                result["loser"] = loser
        self.context.last_result = result
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[VectorSurvival] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[VectorSurvival] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            if self.net_enabled and not self.is_authority:
                self._net_send_action({"kind": "forfeit"})
                self._queue_outcome("forfeit", "Forfeit")
            else:
                self._queue_outcome("forfeit", "Forfeit", send_finish=self.net_enabled and self.is_authority)


def launch(manager, context, callback, mode="solo", **kwargs):
    return VectorSurvivalScene(manager, context, callback, mode=mode, **kwargs)
