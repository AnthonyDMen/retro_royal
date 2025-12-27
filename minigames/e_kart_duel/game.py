# minigames/e_kart_duel/game.py
# Single-player Time Attack with gate barrier + tight twisty loop track.
# Win condition: set any lap <= TARGET_LAP_SEC within LAPS_TOTAL laps.
# Controls: W/S throttle/brake, A/D steer, Esc pause.

import math
import random
import pygame
from pygame.math import Vector2 as Vec2
from .graphics import TileAtlas, TILE_SIZE, COL_GRASS, Kart
from scene_manager import Scene
from game_context import GameContext

TITLE = "E-Kart Duel"
MINIGAME_ID = "e_kart_duel"

# ===================== Config / Modes =====================

MODE_SOLO = "solo"  # time-attack (current implementation)
MODE_MP = "multiplayer"  # ghost race head-to-head
MODE_VERSUS_WIP = "versus"  # placeholder for future multiplayer

LAPS_TOTAL = 2
LAPS_MP = 1  # single lap race for multiplayer (increase to 2 if too short)
TARGET_LAP_SEC = 80.0  # 01:20.00 target

# ---- Kart feel (simple arcade) ----
ACCEL = 330.0  # px/s^2
BRAKE = 520.0  # px/s^2
VMAX = 620.0  # px/s
LIN_DRAG = 0.985  # per 60Hz step (applied ^(dt*60))
TURN_MIN = 2.2  # rad/s at v=0 (steer=1)
TURN_MAX = 3.4  # rad/s near VMAX

# Collision responses
OFF_TRACK_SLOW = 0.55  # keep % of speed if you hit grass
SEAM_TOL = 2.5  # px tolerance when testing track seams

# Camera follow
CAM_LERP = 10.0  # higher = snappier follow

# HUD
COL_TEXT = (235, 238, 245)
COL_GOOD = (120, 232, 145)
COL_BAD = (242, 109, 109)

# Barrier & gate
BARRIER_SLOW = 0.40  # speed clamp when hitting barrier
GATE_COOLDOWN = 1.0  # seconds min between lap counts
GATE_MARGIN = 36.0  # expand gate segment for robustness

# ===================== Track builder (twisty, few straights) =====================


def _build_tight_sloop(w=10, h=8, k=1, stra_pad=1, gap_between_pairs=1):
    """
    Compact twisty loop around a w×h rectangle using S-bends (back-to-back turns).
    Returns: list[(tile_id, gx, gy, rot)]
    """
    layout = []
    occ = set()
    gx, gy, dir_ = 0, 0, 0  # 0=E,1=S,2=W,3=N
    step = [(1, 0), (0, 1), (-1, 0), (0, -1)]

    def place(tile_id, rot, x, y):
        key = (x, y)
        if key in occ:
            return False
        occ.add(key)
        layout.append((tile_id, x, y, rot))
        return True

    def straights(n):
        nonlocal gx, gy, dir_
        rot = (dir_ * 90) % 360
        dx, dy = step[dir_]
        for _ in range(max(0, n)):
            place("STRAIGHT", rot, gx, gy)
            gx += dx
            gy += dy

    def right():
        nonlocal gx, gy, dir_
        rot = (dir_ * 90) % 360
        place("TURN90_R", rot, gx, gy)
        dir_ = (dir_ + 1) % 4
        dx, dy = step[dir_]
        gx += dx
        gy += dy

    def left():
        nonlocal gx, gy, dir_
        rot = (dir_ * 90) % 360
        place("TURN90_L", rot, gx, gy)
        dir_ = (dir_ + 3) % 4
        dx, dy = step[dir_]
        gx += dx
        gy += dy

    def s_bend(first_left=True):
        if first_left:
            left()
            straights(k)
            right()
            right()
            straights(k)
            left()
        else:
            right()
            straights(k)
            left()
            left()
            straights(k)
            right()

    def side_twisty(total_forward_len):
        fwd_remaining = total_forward_len
        straights(stra_pad)
        fwd_remaining -= stra_pad
        first_left = True
        while fwd_remaining > (stra_pad + 1):
            s_bend(first_left)
            fwd_remaining -= 2
            first_left = not first_left
            if gap_between_pairs > 0 and fwd_remaining > (stra_pad + 1):
                straights(1)
                fwd_remaining -= 1
        straights(max(0, fwd_remaining))

    side_twisty(w)
    right()
    side_twisty(h)
    right()
    side_twisty(w)
    right()
    side_twisty(h)
    right()

    # First straight becomes FINISH (gate)
    for i, (tid, x, y, rot) in enumerate(layout):
        if tid == "STRAIGHT":
            layout[i] = ("FINISH", x, y, rot)
            break
    return layout


# ===================== Geometry & helpers =====================


def _tile_world_offset(gx: int, gy: int) -> Vec2:
    return Vec2(gx * TILE_SIZE + TILE_SIZE * 0.5, gy * TILE_SIZE + TILE_SIZE * 0.5)


def _poly_world(atlas: TileAtlas, tid: str, rot: int, gx: int, gy: int):
    meta = atlas.get_meta(tid, rot)
    off = _tile_world_offset(gx, gy)
    return [Vec2(p.x + off.x, p.y + off.y) for p in meta["road_poly"]]


def _point_in_poly(pt: Vec2, poly) -> bool:
    x, y = pt.x, pt.y
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i].x, poly[i].y
        x2, y2 = poly[(i + 1) % n].x, poly[(i + 1) % n].y
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1 + 1e-12)
            xint = x1 + t * (x2 - x1)
            if xint > x:
                inside = not inside
    return inside


def _on_track_with_tolerance(pt: Vec2, polys, tol=SEAM_TOL) -> bool:
    if any(_point_in_poly(pt, P) for P in polys):
        return True
    offs = (Vec2(tol, 0), Vec2(-tol, 0), Vec2(0, tol), Vec2(0, -tol))
    for d in offs:
        q = pt + d
        if any(_point_in_poly(q, P) for P in polys):
            return True
    return False


# ----- robust segment intersection-based gate tests -----


def _ccw(a: Vec2, b: Vec2, c: Vec2) -> bool:
    return (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x)


def _segments_intersect(a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2) -> bool:
    return (_ccw(a1, b1, b2) != _ccw(a2, b1, b2)) and (
        _ccw(a1, a2, b1) != _ccw(a1, a2, b2)
    )


def _expand_gate(a: Vec2, b: Vec2, margin: float) -> tuple[Vec2, Vec2]:
    g = b - a
    L = g.length()
    if L <= 1e-6:
        return a, b
    g /= L
    return a - g * margin, b + g * margin


def _forward_cross(
    prev: Vec2, curr: Vec2, a: Vec2, b: Vec2, tangent: Vec2, margin: float
) -> bool:
    a2, b2 = _expand_gate(a, b, margin)
    if not _segments_intersect(prev, curr, a2, b2):
        return False
    move = curr - prev
    if move.length_squared() <= 1e-9:
        return False
    return (
        move.normalize().dot(
            tangent if tangent.length_squared() == 0 else tangent.normalize()
        )
        >= 0.0
    )


def _backward_hit(
    prev: Vec2, curr: Vec2, a: Vec2, b: Vec2, tangent: Vec2, margin: float
) -> bool:
    a2, b2 = _expand_gate(a, b, margin)
    if not _segments_intersect(prev, curr, a2, b2):
        return False
    move = curr - prev
    if move.length_squared() <= 1e-9:
        return False
    return (
        move.normalize().dot(
            tangent if tangent.length_squared() == 0 else tangent.normalize()
        )
        < 0.0
    )


def _fmt_time(sec: float) -> str:
    if sec is None:
        return "--:--.--"
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


class EKartDuelScene(Scene):
    def __init__(
        self,
        manager,
        context=None,
        callback=None,
        mode: str = MODE_SOLO,
        target_lap_sec: float = TARGET_LAP_SEC,
        seed: int | None = None,
        **kwargs,
    ):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.mode = mode
        self.target_lap_sec = target_lap_sec
        self.seed = seed
        self.minigame_id = MINIGAME_ID
        self.pending_payload = {}
        self._completed = False
        self._banner_timer = 0.0
        self._banner_text = ""
        self._banner_sub = ""
        self._final_outcome = None
        self.forfeited = False
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
        self.remote_id = (
            self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        )
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        if self.net_enabled:
            self.mode = MODE_MP
        self.net_timer = 0.0
        self.net_interval = 1.0 / 15.0
        self.local_finished = False
        self.local_finish_time = None
        self.remote_finished = False
        self.remote_finish_time = None
        self.winner_id = None
        self.loser_id = None
        self._init_display()
        self._setup_game()
        subtitle = "First to finish wins!" if self.net_enabled else "Beat the target lap!"
        self._push_banner("E-Kart Duel", 1.5, subtitle)

    def _init_display(self):
        self.screen = getattr(self.manager, "screen", None)
        if self.screen is None:
            raise RuntimeError("E-Kart Duel requires an existing display surface.")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas,monospace", 18)
        self.bigf = pygame.font.SysFont("consolas,monospace", 28)
        if self.seed is not None:
            random.seed(self.seed)

    def _setup_game(self):
        if self.mode == MODE_VERSUS_WIP:
            self._final_outcome = "forfeit"
            self._push_banner("Versus mode WIP", 2.0, "Press Esc to exit")
            return

        self.atlas = TileAtlas()
        self.layout = _build_tight_sloop(w=10, h=8, k=1, stra_pad=0, gap_between_pairs=0)
        self.road_polys = [
            _poly_world(self.atlas, tid, rot, gx, gy) for (tid, gx, gy, rot) in self.layout
        ]
        self.finish_a = self.finish_b = self.finish_t = None
        self.finish_mid = None
        for tid, gx, gy, rot in self.layout:
            if tid == "FINISH":
                gate = self.atlas.gate_world(tid, rot, gx, gy)
                if gate:
                    self.finish_a, self.finish_b, self.finish_t = gate
                    self.finish_mid = (self.finish_a + self.finish_b) * 0.5
                break
        if self.finish_a is not None:
            heading = math.atan2(self.finish_t.y, self.finish_t.x)
            spawn = self.finish_mid - self.finish_t * (TILE_SIZE * 0.35)
        else:
            tid0, gx0, gy0, rot0 = self.layout[0]
            heading = math.radians(rot0)
            spawn = _tile_world_offset(gx0, gy0)
        self.spawn = spawn
        self.heading = heading
        scheme = random.choice(["player", "alt"])
        self.kart = Kart(Vec2(spawn), heading, scheme=scheme)
        self.ghost_kart = Kart(Vec2(spawn), heading, scheme="alt")
        self.ghost_visible = False
        self.cam_pos = Vec2(self.kart.p)
        self.race_started = False
        self.laps_completed = 0
        self.finished = False
        self.gate_cool = 0.0
        self.race_time = 0.0
        self.lap_time = 0.0
        self.best_lap = None
        self.last_lap = None
        self.outcome_win = False
        self.laps_goal = LAPS_MP if self.net_enabled else LAPS_TOTAL
        self.finish_time = None
        self.net_timer = 0.0
        if self.net_enabled:
            self._net_send_state(force=True)

    # If later we add real MP, switch by 'mode' here.
    def step_kart(self, throttle: float, brake: float, steer: float, dt: float):
        v01 = max(0.0, min(1.0, self.kart.v / VMAX))
        turn_rate = TURN_MIN + (TURN_MAX - TURN_MIN) * v01
        self.kart.h += steer * turn_rate * dt
        a = ACCEL * throttle - BRAKE * brake
        self.kart.v = max(0.0, min(VMAX, self.kart.v + a * dt))
        self.kart.v *= LIN_DRAG ** (dt * 60.0)
        fwd = Vec2(math.cos(self.kart.h), math.sin(self.kart.h))
        self.kart.prev = Vec2(self.kart.p)
        self.kart.p += fwd * self.kart.v * dt
        s_k = 1.0 - math.exp(-10.0 * dt)
        b_k = 1.0 - math.exp(-12.0 * dt)
        self.kart.steer_vis += (steer - self.kart.steer_vis) * s_k
        self.kart.brake_vis += (brake - self.kart.brake_vis) * b_k

    def cam_update(self, target: Vec2, dt: float):
        k = 1.0 - math.exp(-CAM_LERP * dt)
        self.cam_pos += (target - self.cam_pos) * k

    def draw_hud(self):
        mode_txt = "SOLO  -  Time Attack"
        if self.net_enabled:
            mode_txt = "MULTI  -  Ghost Race"
        self.screen.blit(self.font.render(mode_txt, True, COL_TEXT), (12, 10))
        lap_txt = f"Laps {self.laps_completed}/{self.laps_goal}"
        self.screen.blit(self.font.render(lap_txt, True, COL_TEXT), (12, 34))
        rt = _fmt_time(self.race_time if self.race_started else 0.0)
        lt = _fmt_time(self.lap_time if self.race_started and not self.finished else 0.0)
        bt = _fmt_time(self.best_lap)
        tgt_label = "Target" if not self.net_enabled else "Opponent"
        if self.net_enabled:
            tgt = _fmt_time(self.remote_finish_time if self.remote_finished else None)
        else:
            tgt = _fmt_time(self.target_lap_sec)
        right_x = self.screen.get_width() - 12
        y = 10
        for label, val, col in [
            (tgt_label, tgt, COL_GOOD),
            ("Race", rt, COL_TEXT),
            ("Lap", lt, COL_TEXT),
            ("Best", bt, COL_TEXT),
        ]:
            s = self.font.render(f"{label} {val}", True, col)
            self.screen.blit(s, (right_x - s.get_width(), y))
            y += 22
        if self.net_enabled:
            opp_line = "Opponent: waiting..."
            if self.remote_finished and self.remote_finish_time is not None:
                opp_line = f"Opponent: finished {_fmt_time(self.remote_finish_time)}"
            elif self.ghost_visible:
                opp_line = "Opponent: racing"
            o = self.font.render(opp_line, True, COL_TEXT)
            self.screen.blit(o, (12, 58))
        help_txt = "W/S throttle/brake  A/D steer   Esc pause"
        s = self.font.render(help_txt, True, (210, 214, 223))
        self.screen.blit(s, (12, self.screen.get_height() - 28))

    def draw_finish_banner(self):
        if self.net_enabled:
            msg = "Race Won!" if self._final_outcome == "win" else "Race Lost."
            sub = "Press Esc"
        else:
            msg = (
                "NEW LAP UNDER TARGET!  You win."
                if self.outcome_win
                else "Session over."
            )
            sub = "Press Esc to exit pause menu" if not self.outcome_win else "Press Esc"
        panel = pygame.Surface((self.screen.get_width(), 90), pygame.SRCALPHA)
        pygame.draw.rect(panel, (0, 0, 0, 160), panel.get_rect())
        txt = self.bigf.render(msg, True, COL_GOOD if self.outcome_win else COL_BAD)
        subr = self.font.render(sub, True, COL_TEXT)
        panel.blit(txt, ((panel.get_width() - txt.get_width()) // 2, 16))
        panel.blit(subr, ((panel.get_width() - subr.get_width()) // 2, 54))
        self.screen.blit(panel, (0, 40))
    def _reset_session(self):
        self.kart.p = Vec2(self.spawn)
        self.kart.h = self.heading
        self.kart.v = 0.0
        self.race_started = False
        self.laps_completed = 0
        self.finished = False
        self.gate_cool = 0.0
        self.race_time = 0.0
        self.lap_time = 0.0
        self.best_lap = None
        self.last_lap = None
        self.outcome_win = False
        self.finish_time = None
        self.local_finished = False
        self.local_finish_time = None
        self.remote_finished = False
        self.remote_finish_time = None
        self.winner_id = None
        self.loser_id = None
        if self.ghost_kart:
            self.ghost_kart.p = Vec2(self.spawn)
            self.ghost_kart.h = self.heading
            self.ghost_kart.v = 0.0
        self.ghost_visible = False

    def handle_event(self, event):
        if self._final_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._banner_timer = 0.0
                self._finalize(self._final_outcome)
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._pause_game()
        # no reset or color toggle; kart colors are randomized on start

    def update(self, dt):
        # Always tick down transient banners
        if self._banner_timer > 0:
            self._banner_timer = max(0.0, self._banner_timer - dt)

        if self._final_outcome:
            if self._banner_timer <= 0:
                self._finalize(self._final_outcome)
            return
        if self.mode == MODE_VERSUS_WIP:
            return
        dt = max(1e-4, dt)
        # Poll network first so we reflect opponent state early in the frame.
        self._net_poll_actions(dt)
        self.gate_cool = max(0.0, self.gate_cool - dt)
        if self.race_started and not self.finished:
            self.race_time += dt
            self.lap_time += dt
        keys = pygame.key.get_pressed()
        throttle = 1.0 if (keys[pygame.K_w] or keys[pygame.K_UP]) else 0.0
        brake = 1.0 if (keys[pygame.K_s] or keys[pygame.K_DOWN]) else 0.0
        steer = (-1.0 if (keys[pygame.K_a] or keys[pygame.K_LEFT]) else 0.0) + (
            1.0 if (keys[pygame.K_d] or keys[pygame.K_RIGHT]) else 0.0
        )
        steer = max(-1.0, min(1.0, steer))
        prev = Vec2(self.kart.p)
        self.step_kart(throttle, brake, steer, dt)
        if not _on_track_with_tolerance(self.kart.p, self.road_polys, SEAM_TOL):
            self.kart.p = Vec2(prev)
            self.kart.v *= OFF_TRACK_SLOW
        if self.finish_a is not None:
            if _backward_hit(
                prev, self.kart.p, self.finish_a, self.finish_b, self.finish_t, GATE_MARGIN
            ):
                self.kart.p = Vec2(prev)
                self.kart.v *= BARRIER_SLOW
            if self.gate_cool <= 0.0 and _forward_cross(
                prev, self.kart.p, self.finish_a, self.finish_b, self.finish_t, GATE_MARGIN
            ):
                self.gate_cool = GATE_COOLDOWN
                if not self.race_started:
                    self.race_started = True
                    self.lap_time = 0.0
                else:
                    self.last_lap = self.lap_time
                    if (self.best_lap is None) or (self.last_lap < self.best_lap):
                        self.best_lap = self.last_lap
                    self.laps_completed += 1
                    self.lap_time = 0.0
                    if self.net_enabled:
                        self.finished = True
                        self.finish_time = self.race_time
                        self._on_local_finish()
                    else:
                        if self.last_lap <= self.target_lap_sec:
                            self.finished = True
                            self.outcome_win = True
                            self._queue_finish("win", "Lap under target! You win.")
                        elif self.laps_completed >= self.laps_goal:
                            self.finished = True
                            self.outcome_win = (
                                self.best_lap is not None and self.best_lap <= self.target_lap_sec
                            )
                            if self.outcome_win:
                                self._queue_finish("win", "Best lap beats target!")
                            else:
                                self._queue_finish("lose", "Session over. Target not met.")
        if self.finished and not self._final_outcome:
            if self.net_enabled:
                self._maybe_resolve_result()
            else:
                if self.outcome_win:
                    self._queue_finish("win", "Session complete.")
                else:
                    self._queue_finish("lose", "Session over.")
        self.cam_update(self.kart.p, dt)
        # Send state updates for ghost rendering.
        self.net_timer += dt
        self._net_send_state()

    # ---------- Multiplayer helpers ----------
    def _on_local_finish(self):
        self.local_finished = True
        if self.finish_time is None:
            self.finish_time = self.race_time
        self.local_finish_time = self.finish_time
        subtitle = f"Your time {_fmt_time(self.local_finish_time)}"
        wait_text = "Finished! Waiting for opponent..."
        if self.remote_finished and self.remote_finish_time is not None:
            wait_text = "Finished!"
        self._push_banner(wait_text, 2.5, subtitle)
        self._net_send_state(
            kind="finish",
            force=True,
            finish_time=self.local_finish_time,
            laps=self.laps_completed,
            race_time=self.race_time,
        )
        self._maybe_resolve_result()

    def _maybe_resolve_result(self):
        if not self.net_enabled:
            return
        if self.winner_id or self._final_outcome:
            return
        if not self.local_finished or self.local_finish_time is None:
            return
        if not self.remote_finished or self.remote_finish_time is None:
            return
        # Decide winner by lowest finish time; tie-break by player id for determinism.
        local_time = self.local_finish_time
        remote_time = self.remote_finish_time
        # smaller time wins; tie => lexicographic
        if abs(local_time - remote_time) < 1e-3:
            if self.remote_id:
                winner = self.local_id if (self.local_id and self.local_id < self.remote_id) else self.remote_id
            else:
                winner = self.local_id
        else:
            winner = self.local_id if local_time < remote_time else (self.remote_id or self.local_id)
        loser = self.remote_id if winner == self.local_id else self.local_id
        self.winner_id = winner
        self.loser_id = loser
        outcome = "win" if winner == self.local_id else "lose"
        self.outcome_win = outcome == "win"
        subtitle = f"{_fmt_time(local_time)} vs {_fmt_time(remote_time)}"
        extra = {
            "local_finish": local_time,
            "opponent_finish": remote_time,
            "laps_goal": self.laps_goal,
            "winner": winner,
            "loser": loser,
        }
        title = "You win the race!" if outcome == "win" else "You lost the race."
        self._queue_finish(outcome, title, subtitle=subtitle, extra_payload=extra)

    def _apply_remote_state(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        pos = action.get("pos")
        heading = action.get("heading")
        vel = action.get("v")
        if pos and len(pos) == 2:
            try:
                self.ghost_kart.p = Vec2(float(pos[0]), float(pos[1]))
                self.ghost_visible = True
            except Exception:
                pass
        if heading is not None:
            try:
                self.ghost_kart.h = float(heading)
            except Exception:
                pass
        if vel is not None:
            try:
                self.ghost_kart.v = float(vel)
            except Exception:
                pass
        if kind == "finish":
            ft = action.get("finish_time", action.get("race_time"))
            if ft is not None:
                try:
                    self.remote_finish_time = float(ft)
                    self.remote_finished = True
                except Exception:
                    pass
        else:
            if action.get("finished"):
                ft = action.get("finish_time", action.get("race_time"))
                if ft is not None:
                    try:
                        self.remote_finish_time = float(ft)
                        self.remote_finished = True
                    except Exception:
                        pass
        # If we just learned opponent finished, check resolution.
        if self.remote_finished and self.local_finished:
            self._maybe_resolve_result()

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
            self._apply_remote_state(action)

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[EKartDuel] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        interval = self.net_interval
        # We accumulate using update(dt) before calling this; enforce interval here.
        if not force and self.net_timer < interval:
            return
        self.net_timer = 0.0
        payload = {
            "kind": kind,
            "pos": [float(self.kart.p.x), float(self.kart.p.y)],
            "heading": float(self.kart.h),
            "v": float(self.kart.v),
            "lap": self.laps_completed,
            "race_time": self.race_time,
            "lap_time": self.lap_time,
            "finished": self.local_finished,
            "finish_time": self.local_finish_time,
        }
        if extra:
            payload.update(extra)
        self._net_send_action(payload)

    def _queue_finish(self, outcome, text, subtitle=None, extra_payload=None):
        if self._final_outcome:
            return
        self._final_outcome = outcome
        self.pending_payload = {
            "best_lap": self.best_lap,
            "laps_completed": self.laps_completed,
            "target": self.target_lap_sec,
            "forfeit": self.forfeited,
            "last_lap": self.last_lap,
        }
        if extra_payload:
            self.pending_payload.update(extra_payload)
        sub = subtitle
        if sub is None:
            sub = (
                f"Best lap {_fmt_time(self.best_lap)}"
                if self.best_lap is not None
                else "No lap completed"
            )
        self._push_banner(text, 2.5, sub)

    def draw(self):
        if self.mode == MODE_VERSUS_WIP:
            self.screen.fill((12, 14, 18))
            msg = "Versus mode (online) — WIP stub."
            self.screen.blit(self.bigf.render(msg, True, COL_TEXT), (40, 40))
            pygame.display.flip()
            return
        cam_tl = Vec2(
            self.cam_pos.x - self.screen.get_width() * 0.5,
            self.cam_pos.y - self.screen.get_height() * 0.5,
        )
        self.screen.fill(COL_GRASS)
        view_rect = pygame.Rect(
            int(cam_tl.x), int(cam_tl.y), self.screen.get_width(), self.screen.get_height()
        )
        for tid, gx, gy, rot in self.layout:
            tx = gx * TILE_SIZE
            ty = gy * TILE_SIZE
            if (
                tx + TILE_SIZE < view_rect.left
                or ty + TILE_SIZE < view_rect.top
                or tx > view_rect.right
                or ty > view_rect.bottom
            ):
                continue
            surf, rect = self.atlas.get_sprite(tid, rot)
            dest = pygame.Rect(
                int(tx - cam_tl.x), int(ty - cam_tl.y), TILE_SIZE, TILE_SIZE
            )
            self.screen.blit(surf, dest, rect)
        if self.net_enabled and self.ghost_visible:
            ghost_surface = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
            self.ghost_kart.draw(ghost_surface, cam_tl)
            ghost_surface.set_alpha(150)
            self.screen.blit(ghost_surface, (0, 0))
        self.kart.draw(self.screen, cam_tl)
        self.draw_hud()
        if self.finished and self._final_outcome:
            self.draw_finish_banner()
        if self._banner_timer > 0 and self._banner_text:
            dim = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 160))
            self.screen.blit(dim, (0, 0))
            panel = pygame.Surface((self.screen.get_width(), 120), pygame.SRCALPHA)
            pygame.draw.rect(panel, (20, 22, 26, 230), panel.get_rect())
            txt = self.bigf.render(self._banner_text, True, COL_TEXT)
            panel.blit(txt, ((panel.get_width() - txt.get_width()) // 2, 30))
            if self._banner_sub:
                sub = self.font.render(self._banner_sub, True, COL_TEXT)
                panel.blit(sub, ((panel.get_width() - sub.get_width()) // 2, 72))
            self.screen.blit(panel, (0, 40))

    def _push_banner(self, text, seconds, subtitle=None):
        self._banner_text = text
        self._banner_sub = subtitle or ""
        self._banner_timer = float(seconds)

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[EKartDuel] Pause menu unavailable: {exc}")
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
                "best_lap": self.best_lap,
                "laps_completed": self.laps_completed,
                "target": self.target_lap_sec,
                "forfeit": self.forfeited,
                "last_lap": self.last_lap,
            }
        result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if self.duel_id:
            result["duel_id"] = self.duel_id
        if self.winner_id:
            result["winner"] = self.winner_id
        if self.loser_id:
            result["loser"] = self.loser_id
        self.context.last_result = result
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[EKartDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[EKartDuel] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._final_outcome:
            self._finalize(self._final_outcome)
            return
        self.forfeited = True
        if self.net_enabled:
            self.winner_id = self.remote_id
            self.loser_id = self.local_id
        self._queue_finish("forfeit", "Session forfeited.")


def launch(manager, context=None, callback=None, **kwargs):
    return EKartDuelScene(manager, context, callback, **kwargs)
