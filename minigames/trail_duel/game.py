import random, collections, time
import pygame
from scene_manager import Scene
from game_context import GameContext

MULTIPLAYER_ENABLED = True

# Try to import sibling graphics module
try:
    from minigames.trail_duel import graphics as G
except Exception:
    G = None

TITLE = "TrailWorm"

# Grid
GRID_W, GRID_H = 80, 60
CELL_PX = 12  # default; scene will auto-fit a per-instance cell size
SAFE_MARGIN_PX = 6
SCALE_TO_FIT = True   # draw arena to an off-screen surface and scale to fill

# Timing
TICK_HZ = 30.0  # fixed-step simulation
BASE_SPEED_CPS = 10.0  # base cells/sec (tuned via difficulty)

# Tail & items
L0 = 12
GROWTH_PER = 6
L_MAX = 60

SPAWN_PERIOD_S = 6.0
ITEM_MAX = 2
ITEM_LIFETIME = 10.0
MIN_HEAD_DIST = 4
TAIL_CLEARANCE = 2
SPAWN_TRIES = 400

# Turbo (shorter)
TURBO_RECHARGE_S = 5.0
TURBO_RAMP_S = 0.12
TURBO_HOLD_S = 0.36
TURBO_FALL_S = 0.12
TURBO_TOTAL_S = TURBO_RAMP_S + TURBO_HOLD_S + TURBO_FALL_S
TURBO_MULT_MAX = 2.2

# Match
BEST_OF = 3
READY_COUNTDOWN_S = 2.5     # a little longer at match start
END_BANNER_MS = 2500        # longer end banner
INTERMISSION_MS = 3000      # 3s pause between rounds (no countdown)
ROUND_TIME_S = 60.0         # hard cap; longer trail wins on time

# HUD text
CONTROLS = "Arrows/WASD move - Space Turbo - Esc Pause"

# Directions
LEFT = (-1, 0)
RIGHT = (1, 0)
UP = (0, -1)
DOWN = (0, 1)
DIRS = [RIGHT, DOWN, LEFT, UP]


def dir_left(d):
    return DIRS[(DIRS.index(d) - 1) % 4]


def dir_right(d):
    return DIRS[(DIRS.index(d) + 1) % 4]


def is_opposite(a, b):
    return (a[0] + b[0] == 0) and (a[1] + b[1] == 0)


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def cheby(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def now_ms():
    return pygame.time.get_ticks()


class Turbo:
    __slots__ = ("charge", "active", "t", "buffered_turn", "buffered_abs", "locked_dir")

    def __init__(self):
        self.charge = 1.0  # start ready (fun)
        self.active = False
        self.t = 0.0
        self.buffered_turn = None  # 'L' or 'R'
        self.buffered_abs = None  # absolute (dx,dy) to apply after turbo
        self.locked_dir = None

    def start(self, current_dir):
        self.active = True
        self.t = 0.0
        self.charge = 0.0
        self.locked_dir = current_dir
        self.buffered_turn = None
        self.buffered_abs = None

    def end(self):
        self.active = False
        self.t = 0.0
        ld = self.locked_dir
        self.locked_dir = None
        return ld

    def mult(self):
        if not self.active:
            return 1.0
        t = self.t
        if t < TURBO_RAMP_S:
            k = t / TURBO_RAMP_S
            return 1.0 + (TURBO_MULT_MAX - 1.0) * k
        elif t < TURBO_RAMP_S + TURBO_HOLD_S:
            return TURBO_MULT_MAX
        elif t < TURBO_TOTAL_S:
            k = (t - (TURBO_RAMP_S + TURBO_HOLD_S)) / TURBO_FALL_S
            return TURBO_MULT_MAX + (1.0 - TURBO_MULT_MAX) * k
        else:
            return 1.0


class Worm:
    __slots__ = (
        "id",
        "head",
        "dir",
        "trail",
        "occ",
        "target_len",
        "move_accum",
        "turbo",
        "turns",
        "items",
        "turbo_uses",
    )

    def __init__(self, id, head, dir, start_len):
        self.id = id
        self.head = head
        self.dir = dir
        self.trail = collections.deque()
        self.occ = collections.Counter()  # cell -> count (self-collision safe)
        back = (-dir[0], -dir[1])
        cur = head
        for _ in range(start_len):
            self.trail.append(cur)
            self.occ[cur] += 1
            cur = add(cur, back)
        self.target_len = start_len
        self.move_accum = 0.0
        self.turbo = Turbo()
        self.turns = 0
        self.items = 0
        self.turbo_uses = 0

    def current_len(self):
        return len(self.trail)


class Item:
    __slots__ = ("pos", "expires_at")

    def __init__(self, pos, expires_at):
        self.pos = pos
        self.expires_at = expires_at


class TrailDuelScene(Scene):
    def __init__(self, manager, context=None, callback=None, difficulty=1.0, seed=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.minigame_id = "trail_duel"
        flags = getattr(self.context, "flags", {}) if self.context else {}
        self.duel_id = kwargs.get("duel_id") or (flags or {}).get("duel_id")
        raw_participants = kwargs.get("participants") or (flags or {}).get("participants") or []
        self.participants = [str(p) for p in raw_participants]
        self.net_client = kwargs.get("multiplayer_client") or (flags or {}).get("multiplayer_client")
        local_id_raw = kwargs.get("local_player_id") or (flags or {}).get("local_player_id")
        self.local_id = str(local_id_raw) if local_id_raw is not None else None
        self.local_idx = 0
        if self.participants and self.local_id:
            if self.local_id in self.participants:
                self.local_idx = self.participants.index(self.local_id)
            else:
                # Fuzzy match for ID formatting differences.
                for idx, pid in enumerate(self.participants):
                    if self.local_id in pid or pid in self.local_id:
                        self.local_idx = idx
                        self.local_id = pid
                        break
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id in self.participants)
        if not self.net_enabled and self.duel_id:
            print(f"[TrailDuel] Multiplayer disabled: local_id {self.local_id} not in participants {self.participants}")
        self.is_authority = not self.net_enabled or self.local_idx == 0
        seed_val = seed if seed is not None else (self.duel_id or time.time())
        try:
            self.rng = random.Random(int(seed_val))
        except Exception:
            self.rng = random.Random(str(seed_val))

        # Effective params from difficulty
        self.base_speed = clamp(8.0 + 2.0 * difficulty, 8.0, 14.0)
        self.turbo_mult = 2.0 + 0.2 * difficulty
        self.turbo_recharge = max(3.5, TURBO_RECHARGE_S - 0.4 * difficulty)
        self.start_len = L0
        self.growth_per = GROWTH_PER
        self.lmax = L_MAX
        self.spawn_period = SPAWN_PERIOD_S
        self.item_lifetime = ITEM_LIFETIME
        self.npc_rand = 0.03
        self.npc_turbo_threshold = 18

        # Apply turbo mult override
        global TURBO_MULT_MAX
        TURBO_MULT_MAX = self.turbo_mult

        # View / sizing
        self.screen = manager.screen
        self.scr_w, self.scr_h = manager.size
        self.w, self.h = self.scr_w, self.scr_h
        self.cell_px = CELL_PX
        self.field_surf = None  # off-screen playfield surface (created in _fit_to_window)
        self._fit_to_window()

        # Match/round state
        self.accum = 0.0
        self.state = "ready"  # ready | play | ending | ended
        self.ready_until = now_ms() + int(READY_COUNTDOWN_S * 1000)
        self.score_p = 0
        self.score_n = 0

        # End banner
        self.ending = False
        self.end_until = 0
        self.end_result = None  # "win" | "lose" | "forfeit"
        self.forfeited = False
        self._finalized = False
        self.pending_payload = {}
        self.pending_outcome = None

        # Intermission timer between rounds
        self.between_until = 0

        # Telemetry
        self.telemetry_on = False
        self.sim_crashes = 0
        self.turns_player = 0
        self.turns_npc = 0
        self.round_time_left = ROUND_TIME_S

        # Net timing
        self.net_last = 0.0
        self.net_interval = 1.0 / 15.0

        self._init_round()

    # -------- layout --------
    def _fit_to_window(self):
        hud_h = G.TOP_BAR_H if G else 0
        # Full target area the arena should occupy (everything under the HUD)
        self.avail_rect = pygame.Rect(
            SAFE_MARGIN_PX,
            hud_h + SAFE_MARGIN_PX,
            self.scr_w - 2*SAFE_MARGIN_PX,
            self.scr_h - hud_h - 2*SAFE_MARGIN_PX,
        )

        if SCALE_TO_FIT:
            # Keep a crisp internal grid size, then scale to the available area.
            self.cell_px = CELL_PX
            self.play_w  = GRID_W * self.cell_px
            self.play_h  = GRID_H * self.cell_px
            # Off-screen rect starts at (0,0)
            self.play_rect = pygame.Rect(0, 0, self.play_w, self.play_h)
            # (Re)create off-screen surface if needed
            if (self.field_surf is None or
                self.field_surf.get_width()  != self.play_w or
                self.field_surf.get_height() != self.play_h):
                self.field_surf = pygame.Surface((self.play_w, self.play_h)).convert_alpha()
        else:
            # Integer-fit (fallback): may leave small gutters; centered under HUD.
            avail_w, avail_h = self.avail_rect.w, self.avail_rect.h
            new_cell = max(6, min(avail_w // GRID_W, avail_h // GRID_H))
            self.cell_px = int(new_cell)
            self.play_w  = GRID_W * self.cell_px
            self.play_h  = GRID_H * self.cell_px
            left = self.avail_rect.left + (avail_w - self.play_w)//2
            top  = self.avail_rect.top  + (avail_h - self.play_h)//2
            self.play_rect = pygame.Rect(left, top, self.play_w, self.play_h)

    # -------- round flow --------
    def _init_round(self):
        py = GRID_H // 2
        ny = GRID_H // 2
        self.player = Worm("P", (GRID_W // 4, py), RIGHT, self.start_len)
        self.npc = Worm("N", (GRID_W - GRID_W // 4, ny), LEFT, self.start_len)
        self.items = []
        self.last_spawn_ms = now_ms()
        self.state = "ready"
        self.ready_until = now_ms() + int(READY_COUNTDOWN_S * 1000)
        self.round_time_left = ROUND_TIME_S
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _start_play(self):
        self.state = "play"
        self.player.move_accum = 0.0
        self.npc.move_accum = 0.0

    def _round_over(self, player_won):
        if player_won:
            self.score_p += 1
        else:
            self.score_n += 1

        need = (BEST_OF + 1)//2
        if self.score_p >= need or self.score_n >= need:
            # Match finished: show longer banner, then fire callback once in _tick_fixed
            self.ending = True
            self._result_fired = False
            self.end_result = "win" if self.score_p > self.score_n else "lose"
            self.end_until = now_ms() + END_BANNER_MS
            self.state = "ending"
            if self.net_enabled and self.is_authority:
                winner_id = self.local_id if self.end_result == "win" else self.remote_id
                loser_id = self.remote_id if winner_id == self.local_id else self.local_id
                self._net_send_action(
                    {"kind": "finish", "winner": winner_id, "loser": loser_id, "outcome": self.end_result}
                )
            return

        # Not finished → 3s intermission (no countdown; just show score)
        self.state = "between"
        self.between_until = now_ms() + INTERMISSION_MS

    # -------- input --------
    def handle_event(self, e):
        if self.pending_outcome and e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            self._finalize(self.pending_outcome)
            return
        if self.state in ("ending", "ended"):
            if (
                self.end_result
                and e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN)
                and (e.type != pygame.MOUSEBUTTONDOWN or e.button == 1)
            ):
                self._finalize(self.end_result)
            return
        if self.state == "between":
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._init_round()
                self._start_play()
            return
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._pause_game()
                return
            key_to_dir = {
                pygame.K_LEFT: LEFT,
                pygame.K_a: LEFT,
                pygame.K_RIGHT: RIGHT,
                pygame.K_d: RIGHT,
                pygame.K_UP: UP,
                pygame.K_w: UP,
                pygame.K_DOWN: DOWN,
                pygame.K_s: DOWN,
            }
            if e.key in key_to_dir:
                new_dir = key_to_dir[e.key]
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "dir", "dir": new_dir})
                else:
                    self._queue_abs_dir(self.player, new_dir)
                return
            if e.key == pygame.K_SPACE:
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "turbo"})
                else:
                    self._try_turbo(self.player)
                return
            if e.key == pygame.K_F2:
                self.telemetry_on = not self.telemetry_on

    def _queue_abs_dir(self, worm, new_dir):
        if is_opposite(new_dir, worm.dir):
            return
        if worm.turbo.active:
            worm.turbo.buffered_abs = new_dir
            return
        if new_dir != worm.dir:
            worm.dir = new_dir
            worm.turns += 1
            if worm.id == "P":
                self.turns_player += 1

    def _apply_buffered_after_turbo(self, worm):
        if worm.turbo.buffered_abs:
            nd = worm.turbo.buffered_abs
            worm.turbo.buffered_abs = None
            if not is_opposite(nd, worm.dir) and nd != worm.dir:
                worm.dir = nd
                worm.turns += 1
                if worm.id == "P":
                    self.turns_player += 1
            worm.turbo.buffered_turn = None
            return
        if worm.turbo.buffered_turn:
            lr = worm.turbo.buffered_turn
            nd = dir_left(worm.dir) if lr == "L" else dir_right(worm.dir)
            worm.dir = nd
            worm.turns += 1
            if worm.id == "P":
                self.turns_player += 1
            worm.turbo.buffered_turn = None

    def _try_turbo(self, worm):
        if not worm.turbo.active and worm.turbo.charge >= 1.0:
            worm.turbo.start(worm.dir)
            worm.turbo_uses += 1

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[TrailDuel] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def forfeit_from_pause(self):
        if self._finalized:
            return
        self.forfeited = True
        self.end_result = "forfeit"
        self._finalize("forfeit")

    # ----------------------- networking helpers ----------------------
    def _pack_state(self):
        return {
            "state": self.state,
            "score": (self.score_p, self.score_n),
            "player": {"head": self.player.head, "dir": self.player.dir, "trail": list(self.player.trail)},
            "npc": {"head": self.npc.head, "dir": self.npc.dir, "trail": list(self.npc.trail)},
            "items": [(it.pos, it.expires_at) for it in self.items],
            "ready_until": self.ready_until,
            "between_until": self.between_until,
            "end_until": self.end_until,
            "end_result": self.end_result,
            "round_time_left": self.round_time_left,
        }

    def _apply_state(self, st: dict):
        if not st:
            return
        self.state = st.get("state", self.state)
        sc = st.get("score", (self.score_p, self.score_n))
        if isinstance(sc, (list, tuple)) and len(sc) == 2:
            self.score_p, self.score_n = int(sc[0]), int(sc[1])
        self.player.head = tuple(st.get("player", {}).get("head", self.player.head))
        self.player.dir = tuple(st.get("player", {}).get("dir", self.player.dir))
        ptrail = st.get("player", {}).get("trail")
        if ptrail:
            self.player.trail = collections.deque([tuple(p) for p in ptrail])
            self.player.occ = collections.Counter(self.player.trail)
        self.npc.head = tuple(st.get("npc", {}).get("head", self.npc.head))
        self.npc.dir = tuple(st.get("npc", {}).get("dir", self.npc.dir))
        ntrail = st.get("npc", {}).get("trail")
        if ntrail:
            self.npc.trail = collections.deque([tuple(p) for p in ntrail])
            self.npc.occ = collections.Counter(self.npc.trail)
        self.items = [Item(tuple(pos), exp) for pos, exp in (st.get("items") or [])]
        self.ready_until = st.get("ready_until", self.ready_until)
        self.between_until = st.get("between_until", self.between_until)
        self.end_until = st.get("end_until", self.end_until)
        self.end_result = st.get("end_result", self.end_result)
        self.round_time_left = st.get("round_time_left", self.round_time_left)
        if self.net_enabled and not self.is_authority and not self.pending_outcome:
            # Wait for the finish payload to map win/lose locally.
            self.end_result = None
        # Map host's P/N to local perspective: host always sends P=host, N=remote
        if self.local_idx == 1:
            self.player, self.npc = self.npc, self.player

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[TrailDuel] send failed: {exc}")

    def _net_send_state(self, force=False):
        if not self.net_enabled or not self.is_authority:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
        self._net_send_action({"kind": "state", "state": self._pack_state()})

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
            self.pending_outcome = mapped
            return
        if not self.is_authority:
            # Non-authority should not process host actions beyond state/finish.
            return
        if kind == "dir":
            new_dir = tuple(action.get("dir") or RIGHT)
            self._queue_abs_dir(self.npc, new_dir)
            return
        if kind == "turbo":
            self._try_turbo(self.npc)
            return
            if win == self.local_id:
                mapped = "win"
            elif lose == self.local_id:
                mapped = "lose"
            self.pending_payload = {"winner": win, "loser": lose}
            self.pending_outcome = mapped

    # -------- update --------
    def update(self, dt):
        self._net_poll_actions(float(dt))
        if not self.is_authority and self.net_enabled:
            return
        self.accum += dt
        step = 1.0 / TICK_HZ
        while self.accum >= step:
            self.accum -= step
            self._tick_fixed(step)
            if self.net_enabled and self.is_authority:
                self._net_send_state()

    def _tick_fixed(self, step_s):
        # End banner gate (freeze logic; fire result once)
        if self.state in ("ending", "ended"):
            if self.state == "ending" and now_ms() >= self.end_until:
                self.state = "ended"
                if self.end_result:
                    self._finalize(self.end_result)
            return

        # Between-round pause (no countdown; score is visible on HUD)
        if self.state == "between":
            if now_ms() >= self.between_until:
                # Reposition for next round and start immediately
                self._init_round()
                self._start_play()
            return

        # Ready → Play
        if self.state == "ready":
            if now_ms() >= self.ready_until:
                self._start_play()
            return
        if self.state != "play":
            return
        # Round timer
        self.round_time_left -= step_s
        if self.round_time_left <= 0:
            # Higher length wins; tie = replay round
            lp = self.player.current_len()
            ln = self.npc.current_len()
            if lp == ln:
                self._init_round()
                return
            self._round_over(player_won=lp > ln)
            return

        # Recharge / advance turbo timers
        for w in (self.player, self.npc):
            if not w.turbo.active:
                w.turbo.charge = clamp(
                    w.turbo.charge + (step_s / self.turbo_recharge), 0.0, 1.0
                )
            else:
                w.turbo.t += step_s
                if w.turbo.t >= TURBO_TOTAL_S:
                    w.turbo.end()
                    self._apply_buffered_after_turbo(w)

        # Items
        self._maybe_spawn_items()

        # NPC decision
        if not self.net_enabled:
            self._npc_decide()

        # Movement accumulation
        for w in (self.player, self.npc):
            speed = self.base_speed * (w.turbo.mult() if w.turbo.active else 1.0)
            w.move_accum += speed / TICK_HZ

            # Substep loop (simultaneous)
            while self.player.move_accum >= 1.0 or self.npc.move_accum >= 1.0:
                p_will = self.player.move_accum >= 1.0
                n_will = self.npc.move_accum >= 1.0
                if not (p_will or n_will):
                    break
                crash_p, crash_n, sim = self._advance_both(p_will, n_will)
                if crash_p and crash_n:
                    # TIEBREAKER: longer trail wins; equal length = true tie (redo round)
                    lp = self.player.current_len()
                    ln = self.npc.current_len()
                    if lp > ln:
                        self._round_over(player_won=True); return
                    elif ln > lp:
                        self._round_over(player_won=False); return
                    else:
                        self.sim_crashes += 1
                        self._init_round(); return
                if crash_p or crash_n:
                    self._round_over(player_won=crash_n); return

    # -------- movement & items --------
    def _advance_both(self, p_move, n_move):
        p, n = self.player, self.npc
        p_next = add(p.head, p.dir) if p_move else p.head
        n_next = add(n.head, n.dir) if n_move else n.head

        # OOB
        p_oob = p_move and (
            p_next[0] < 0 or p_next[0] >= GRID_W or p_next[1] < 0 or p_next[1] >= GRID_H
        )
        n_oob = n_move and (
            n_next[0] < 0 or n_next[0] >= GRID_W or n_next[1] < 0 or n_next[1] >= GRID_H
        )

        # Head-on into same free cell
        head_on = p_move and n_move and (p_next == n_next)

        # Items
        p_take = p_move and self._item_at(p_next) is not None
        n_take = n_move and self._item_at(n_next) is not None

        # Self-collision SAFE; other-body lethal
        p_hits_other = p_move and (n.occ[p_next] > 0)
        n_hits_other = n_move and (p.occ[n_next] > 0)

        p_crash = p_oob or head_on or p_hits_other
        n_crash = n_oob or head_on or n_hits_other

        if p_crash and n_crash:
            return True, True, True

        # Advance heads
        if not p_crash and p_move:
            p.head = p_next
            p.trail.append(p_next)
            p.occ[p_next] += 1
        if not n_crash and n_move:
            n.head = n_next
            n.trail.append(n_next)
            n.occ[n_next] += 1

        # Apply item/tail
        if not p_crash and p_move:
            if p_take:
                self._pickup_item(p_next)
                p.target_len = min(p.target_len + self.growth_per, self.lmax)
                p.items += 1
            else:
                while len(p.trail) > p.target_len:
                    tail = p.trail.popleft()
                    p.occ[tail] -= 1
                    if p.occ[tail] <= 0:
                        del p.occ[tail]
            p.move_accum -= 1.0

        if not n_crash and n_move:
            if n_take:
                self._pickup_item(n_next)
                n.target_len = min(n.target_len + self.growth_per, self.lmax)
                n.items += 1
            else:
                while len(n.trail) > n.target_len:
                    tail = n.trail.popleft()
                    n.occ[tail] -= 1
                    if n.occ[tail] <= 0:
                        del n.occ[tail]
            n.move_accum -= 1.0

        return p_crash, n_crash, False

    def _maybe_spawn_items(self):
        now = now_ms()
        # prune expired
        self.items = [it for it in self.items if it.expires_at > now]
        # cadence
        if now - self.last_spawn_ms < int(self.spawn_period * 1000):
            return
        self.last_spawn_ms = now
        if len(self.items) >= ITEM_MAX:
            return
        # attempt place
        tries = 0
        while tries < SPAWN_TRIES:
            tries += 1
            x, y = self.rng.randrange(GRID_W), self.rng.randrange(GRID_H)
            pos = (x, y)
            if self.player.occ[pos] > 0 or self.npc.occ[pos] > 0:
                continue
            if cheby(pos, self.player.head) < MIN_HEAD_DIST:
                continue
            if cheby(pos, self.npc.head) < MIN_HEAD_DIST:
                continue
            near_tail = any(
                cheby(pos, seg) <= TAIL_CLEARANCE for seg in self.player.trail
            ) or any(cheby(pos, seg) <= TAIL_CLEARANCE for seg in self.npc.trail)
            if near_tail:
                continue
            self.items.append(Item(pos, now + int(self.item_lifetime * 1000)))
            break

    def _item_at(self, pos):
        for i, it in enumerate(self.items):
            if it.pos == pos:
                return i
        return None

    def _pickup_item(self, pos):
        idx = self._item_at(pos)
        if idx is not None:
            self.items.pop(idx)

    # -------- NPC --------
    def _npc_decide(self):
        if self.state != "play":
            return
        base = self.npc.dir
        candidates = [("L", dir_left(base)), ("S", base), ("R", dir_right(base))]
        scored = []
        for code, d in candidates:
            head2 = add(self.npc.head, d)
            if not (0 <= head2[0] < GRID_W and 0 <= head2[1] < GRID_H):
                s = -999
            elif self.player.occ[head2] > 0 or (
                self.npc.occ[head2] > 0
                and head2 != (self.npc.trail[0] if self.npc.trail else None)
            ):
                s = -500
            else:
                s = self._flood_score(head2, d, cap=400) + self._item_bias(head2)
            s += self.rng.random() * (self.npc_rand * 5.0)
            scored.append((s, code, d))
        scored.sort(reverse=True, key=lambda t: t[0])
        best_s, best_code, best_dir = scored[0]
        if len(scored) > 1 and self.rng.random() < self.npc_rand:
            best_s, best_code, best_dir = scored[1]
        if best_code == "L":
            self.npc.dir = dir_left(self.npc.dir)
            self.turns_npc += 1
        elif best_code == "R":
            self.npc.dir = dir_right(self.npc.dir)
            self.turns_npc += 1
        # Turbo if boxed-in
        projected = max(scored[0][0], 0)
        if self.npc.turbo.charge >= 1.0 and projected < self.npc_turbo_threshold:
            self._try_turbo(self.npc)

    def _flood_score(self, start_cell, start_dir, cap=400):
        solid = {c for c, k in self.player.occ.items() if k > 0} | {
            c for c, k in self.npc.occ.items() if k > 0
        }
        q = collections.deque([start_cell])
        seen = {start_cell}
        count = 0
        while q and count < cap:
            c = q.popleft()
            if c in solid:
                continue
            count += 1
            for d in DIRS:
                nx = (c[0] + d[0], c[1] + d[1])
                if 0 <= nx[0] < GRID_W and 0 <= nx[1] < GRID_H and nx not in seen:
                    seen.add(nx)
                    q.append(nx)
        return count

    def _item_bias(self, head2):
        diff = self.player.current_len() - self.npc.current_len()
        if diff < 6 or not self.items:
            return 0.0
        best = min(cheby(head2, it.pos) for it in self.items)
        return max(0, 30 - best * 4)

    # -------- draw --------
    def draw(self):
        if not G:
            return
        # Window resize refit
        if (self.scr_w, self.scr_h) != self.manager.size:
            self.scr_w, self.scr_h = self.manager.size
            self._fit_to_window()

        G.draw_background(self.screen, self.scr_w, self.scr_h)

        # End banner overlay only (avoid HUD glitching)
        if self.state in ("ending", "ended") or self.pending_outcome:
            result = self.pending_outcome or self.end_result
            if self.net_enabled and not self.is_authority and not result:
                return
            if result == "win":
                msg = "YOU WIN!"
            elif result == "lose":
                msg = "YOU LOSE"
            elif result == "forfeit":
                msg = "FORFEIT"
            else:
                msg = "RESULT"
            G.draw_end_banner(self.screen, self.screen.get_rect(), msg)
            return

        if SCALE_TO_FIT:
            # Draw arena to the off-screen surface at (0,0) coords
            field_rect = self.play_rect  # (0,0,w,h)
            surf = self.field_surf
            # Clear by drawing the playfield background (it covers the full rect)
            G.draw_playfield(surf, field_rect, GRID_W, GRID_H, self.cell_px)
            G.draw_items(surf, field_rect, self.items, self.cell_px)
            G.draw_worm(surf, field_rect, self.player, self.cell_px, color_key="player")
            G.draw_worm(surf, field_rect, self.npc,    self.cell_px, color_key="npc")

            # Scale to the exact available area under the HUD and blit
            scaled = pygame.transform.smoothscale(surf, (self.avail_rect.w, self.avail_rect.h))
            self.screen.blit(scaled, self.avail_rect.topleft)

            # HUD on top, aligned to the top of the available area
            G.draw_hud(self.screen, self.scr_w, self.avail_rect.top,
                       score_p=self.score_p, score_n=self.score_n, best_of=BEST_OF,
                       len_p=self.player.current_len(),
                       turbo_p=self.player.turbo.charge, turbo_n=self.npc.turbo.charge,
                       controls=CONTROLS, state=self.state, ready_until=self.ready_until)
            return

        # Normal render
        G.draw_playfield(self.screen, self.play_rect, GRID_W, GRID_H, self.cell_px)
        G.draw_items(self.screen, self.play_rect, self.items, self.cell_px)
        G.draw_worm(
            self.screen, self.play_rect, self.player, self.cell_px, color_key="player"
        )
        G.draw_worm(
            self.screen, self.play_rect, self.npc, self.cell_px, color_key="npc"
        )
        G.draw_hud(
            self.screen,
            self.scr_w,
            self.play_rect.top,
            score_p=self.score_p,
            score_n=self.score_n,
            best_of=BEST_OF,
            len_p=self.player.current_len(),
            turbo_p=self.player.turbo.charge,
            turbo_n=self.npc.turbo.charge,
            controls=CONTROLS,
            state=self.state,
            ready_until=self.ready_until,
        )

    def _result_details(self):
        return {
            "player_score": self.score_p,
            "npc_score": self.score_n,
            "best_of": BEST_OF,
            "forfeit": self.forfeited or (self.end_result == "forfeit"),
            "turns_player": self.turns_player,
            "turns_npc": self.turns_npc,
            "player_items": getattr(self.player, "items", 0),
            "npc_items": getattr(self.npc, "items", 0),
            "sim_crashes": self.sim_crashes,
            "timer": ROUND_TIME_S,
        }

    def _finalize(self, outcome):
        if self._finalized:
            return
        self._finalized = True
        if self.context is None:
            self.context = GameContext()
        details = self.pending_payload or self._result_details()
        if self.net_enabled and self.is_authority and outcome in ("win", "lose"):
            winner_id = self.local_id if outcome == "win" else self.remote_id
            loser_id = self.remote_id if winner_id == self.local_id else self.local_id
            details.setdefault("winner", winner_id)
            details.setdefault("loser", loser_id)
            self._net_send_action(
                {"kind": "finish", "winner": winner_id, "loser": loser_id, "outcome": outcome}
            )
        elif self.net_enabled and not self.is_authority and outcome in ("win", "lose"):
            # Avoid duplicating finish messages from client side.
            pass
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": details,
        }
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[TrailDuel] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[TrailDuel] Callback error: {exc}")


def launch(manager, context, callback, **kwargs):
    return TrailDuelScene(manager, context, callback, **kwargs)
