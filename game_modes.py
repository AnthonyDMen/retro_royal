import random
import math
import pygame
from minigame_loader import load_minigame_module

INTERACT_RADIUS = 42


class MapProfileAPI:
    """Optional helper API for map_profile modules."""

    def get_unlock_state(self, context):
        """Return which barriers should open based on the current context."""
        return {}

    def on_barrier_open(self, barrier_name: str):
        """Called once when a named barrier transitions to open."""
        pass


class BaseModeController:
    def __init__(self, scene):
        self.scene = scene
        self.npcs = []
        self.near = None

    def initialize_spawns(self):
        raise NotImplementedError

    def handle_event(self, event):
        pass

    def update(self, dt):
        pass

    def draw_overlay(self, view):
        pass

    def _launch_minigame(self, minigame):
        mod = load_minigame_module(minigame)
        if not mod:
            print(f"[Mode] Import failed for {minigame}")
            return
        if not hasattr(mod, "launch"):
            print(f"[Mode] Missing launch() for {minigame}")
            return

        def on_exit(context):
            context.apply_result()
            result = context.last_result or {}
            print(f"[Mode] Finished {result.get('minigame')} → {result.get('outcome')}")
            self.on_minigame_complete(result)

        self.scene.manager.push(mod.launch(self.scene.manager, self.scene.context, on_exit))

    def on_minigame_complete(self, result_dict):
        pass

    def _load_idle_frame(self, skin_dir):
        if not skin_dir:
            return None
        idle_path = skin_dir / "idle.png"
        try:
            img = pygame.image.load(idle_path).convert_alpha()
            fw = max(1, img.get_width() // 4)
            fh = max(1, img.get_height() // 4)
            return img.subsurface(pygame.Rect(0, 0, fw, fh))
        except Exception:
            return None

    def _load_anim(self, skin_dir):
        """Load idle/walk strips assuming the same layout as the player (4x4)."""
        if not skin_dir:
            return None

        def slice_sheet(sheet):
            # Assume 4 directional rows, square frames sized from height/4,
            # and as many columns as fit across the width.
            rows = 4
            fh = sheet.get_height() // rows
            fw = fh
            if fw <= 0 or fh <= 0:
                return None
            cols = max(1, sheet.get_width() // fw)
            frames = []
            for r in range(rows):
                row = []
                for c in range(cols):
                    surf = pygame.Surface((fw, fh), pygame.SRCALPHA)
                    surf.blit(sheet, (0, 0), (c * fw, r * fh, fw, fh))
                    row.append(surf)
                frames.append(row)
            return frames

        idle_img = walk_img = None
        try:
            idle_img = pygame.image.load(skin_dir / "idle.png").convert_alpha()
        except Exception:
            idle_img = None
        try:
            walk_img = pygame.image.load(skin_dir / "walk.png").convert_alpha()
        except Exception:
            walk_img = None

        if not idle_img and not walk_img:
            return None

        idle_frames = slice_sheet(idle_img) if idle_img else None
        walk_frames = slice_sheet(walk_img) if walk_img else None
        if not idle_frames and walk_frames:
            idle_frames = walk_frames
        if not walk_frames and idle_frames:
            walk_frames = idle_frames
        if not idle_frames:
            return None

        foot_y = max(0, (idle_frames[0][0].get_height() if idle_frames and idle_frames[0] else 0) - 4)

        return {
            "idle": idle_frames,
            "walk": walk_frames,
            "dir_rows": {"down": 0, "up": 1, "left": 2, "right": 3},
            "speed_idle": 2.0,
            "speed_walk": 8.0,
            "foot_y": foot_y,
        }

    def _advance_anim(self, npc, vx, vy, dt):
        anim = npc.get("anim")
        if not anim:
            return
        state = npc.setdefault(
            "anim_state", {"index": 0.0, "facing": "down", "state": "idle"}
        )
        moving = abs(vx) + abs(vy) > 0
        facing = state.get("facing", "down")
        if moving:
            if abs(vx) >= abs(vy):
                facing = "right" if vx > 0 else "left"
            else:
                facing = "down" if vy > 0 else "up"
        state["facing"] = facing
        state["state"] = "walk" if moving else "idle"

        dir_row = anim.get("dir_rows", {}).get(facing, 0)
        frames = anim["walk"] if moving else anim["idle"]
        if dir_row >= len(frames):
            dir_row = 0
        row = frames[dir_row] if frames else []
        if not row:
            return
        speed = anim.get("speed_walk" if moving else "speed_idle", 4.0)
        state["index"] = (state.get("index", 0.0) + speed * dt) % max(1, len(row))
        npc["frame"] = row[int(state["index"]) % len(row)]


class ArcadeController(BaseModeController):
    def __init__(self, scene):
        super().__init__(scene)
        self.arcade_game = None
        self.is_sandbox = (getattr(scene, "mode", "") or "").lower() == "sandbox"
        self.require_second_player = bool(getattr(scene, "is_multiplayer", False))

    def initialize_spawns(self):
        mp = self.scene.map_profile
        player_xy, npc_xy = (None, None), None
        if hasattr(mp, "pick_arcade_spawn"):
            player_xy, npc_xy = mp.pick_arcade_spawn(self.scene.map_data, self.scene.tile)

        if player_xy and all(isinstance(v, int) for v in player_xy):
            self.scene.player_rect.midbottom = player_xy

        games = self.scene._discover_minigames() or ["rps_duel"]
        if self.is_sandbox:
            self._spawn_sandbox_targets(games)
        else:
            self._spawn_single_target(games, npc_xy)

    def update(self, dt):
        self.near = None
        if not self.npcs:
            return
        pcx, pcy = self.scene.player_rect.center
        for npc in self.npcs:
            rect = npc.get("rect")
            if not rect:
                continue
            rcx, rcy = rect.center
            if math.hypot(pcx - rcx, pcy - rcy) < INTERACT_RADIUS:
                self.near = npc
                break
        if self.require_second_player and self.near:
            if not self._has_partner_near(self.near.get("rect")):
                self.near = None
        # keep idle animations moving even when static
        for npc in self.npcs:
            self._advance_anim(npc, 0, 0, dt)

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_e and self.near:
            minigame = self.near.get("minigame") or self.arcade_game
            if not minigame:
                return
            if self.require_second_player and not self._has_partner_near(self.near.get("rect")):
                print("[ArcadeController] Need a second player to start this minigame.")
                return
            if getattr(self.scene, "is_multiplayer", False):
                self.scene.request_minigame_start(minigame)
            else:
                print(f"[ArcadeController] Launching {minigame}")
                self._launch_minigame(minigame)

    def draw_overlay(self, view):
        if not self.near:
            return
        font = pygame.font.SysFont(None, 22)
        label = self.near.get("label") or self.near.get("minigame") or self.arcade_game
        suffix = " (needs 2 players)" if self.require_second_player else ""
        msg = font.render(f"Press E to Practice {label}{suffix}", True, (255, 240, 150))
        rect = msg.get_rect()
        rect.midbottom = (view.get_width() // 2, view.get_height() - 28)
        view.blit(msg, rect)

    def _spawn_single_target(self, games, npc_xy=None):
        chosen = random.choice(games)
        self.arcade_game = chosen
        chars = self.scene._discover_characters()
        skin = random.choice(chars) if chars else None
        anim = self._load_anim(skin)
        frame = None
        if anim:
            frame = anim.get("idle", [[None]])[0][0] if anim.get("idle") else None
        else:
            frame = self._load_idle_frame(skin)
        player_bottom = self.scene.player_rect.midbottom
        npc_pos = npc_xy if npc_xy else (player_bottom[0] + 40, player_bottom[1])
        rect = pygame.Rect(0, 0, 20, 24)
        rect.midbottom = npc_pos
        npc = {
            "rect": rect,
            "frame": frame,
            "skin": skin.name if skin else "unknown",
            "minigame": chosen,
            "label": chosen.replace("_", " ").title(),
            "anim": anim,
            "foot_y": (anim or {}).get("foot_y"),
        }
        self.npcs = [npc]
        print(f"[ArcadeController] NPC at {rect.topleft}, game={chosen}")

    def _spawn_sandbox_targets(self, games):
        print(f"[ArcadeController] Sandbox mode: spawning {len(games)} practice NPCs.")
        chars = self.scene._discover_characters()
        spacing_x = 74
        row_gap = 58
        total = max(1, len(games))
        cols = min(6, max(1, int(math.ceil(math.sqrt(total)))))
        rows = max(1, int(math.ceil(total / cols)))
        center_x = getattr(self.scene, "map_w_px", 640) // 2
        center_y = getattr(self.scene, "map_h_px", 480) // 2
        grid_width = (cols - 1) * spacing_x
        grid_height = (rows - 1) * row_gap
        start_x = center_x - grid_width // 2
        start_y = max(80, center_y - grid_height // 2)
        npcs = []
        for idx, game in enumerate(sorted(games)):
            row = idx // cols
            col = idx % cols
            x = start_x + col * spacing_x
            y = start_y + row * row_gap
            rect = pygame.Rect(0, 0, 20, 24)
            rect.midbottom = (x, y)
            skin = random.choice(chars) if chars else None
            anim = self._load_anim(skin)
            frame = None
            if anim:
                frame = anim.get("idle", [[None]])[0][0] if anim.get("idle") else None
            else:
                frame = self._load_idle_frame(skin)
            label = game.replace("_", " ").title()
            npcs.append(
                {
                    "rect": rect,
                    "frame": frame,
                    "skin": skin.name if skin else "unknown",
                    "minigame": game,
                    "label": label,
                    "anim": anim,
                    "foot_y": (anim or {}).get("foot_y"),
                }
            )
        self.npcs = npcs
        # park the player just below the practice row so everything stays on screen
        if npcs:
            below_y = start_y + rows * row_gap + 20
            self.scene.player_rect.midbottom = (
                center_x,
                min(max(40, below_y), getattr(self.scene, "map_h_px", below_y) - 20),
            )


class TournamentController(BaseModeController):
    def __init__(self, scene):
        super().__init__(scene)
        self.active_npc = None
        self.busy = False
        self.total_wins_to_clear = 0
        self.used_minigames = set()
        self.safe_center = None
        self.safe_radius = None
        self.safe_radius_min = 180
        self.shrink_rate = 3.0  # slower shrink
        self.shrink_delay = 15.0
        self.shrink_elapsed = 0.0
        if (getattr(self.scene, "map_name", "").lower(), (getattr(self.scene, "mode", "") or "").lower()) == (
            "test_arena",
            "tournament",
        ):
            self.shrink_rate = 8.0
            self.shrink_delay = 8.0
        self.outside_timers = {"player": 0.0}
        self.npc_duel_timer = 0.0
        self.npc_duel_interval = 12.0
        self.npc_duel_chance = 0.05  # 1 in 20 chance when timer fires

    def _has_bracket(self):
        mp = getattr(self.scene, "map_profile", None)
        return bool(mp and hasattr(mp, "get_zone_data"))

    def initialize_spawns(self):
        # In multiplayer we don't spawn local roaming NPCs; safe zone still initializes.
        if getattr(self.scene, "is_multiplayer", False):
            self.npcs = []
            self.active_npc = None
            self.busy = False
            self._init_safe_zone()
            return
        mp = self.scene.map_profile
        if hasattr(mp, "pick_tournament_spawns"):
            player_xy = mp.pick_tournament_spawns(self.scene.map_data, self.scene.tile)
            if player_xy and all(isinstance(v, int) for v in player_xy):
                self.scene.player_rect.midbottom = player_xy
                print(f"[TournamentController] Player spawn {player_xy}")

        if hasattr(mp, "get_barrier_rects"):
            zones = mp.get_zone_data() if hasattr(mp, "get_zone_data") else []
            raw_rects = mp.get_barrier_rects(self.scene.map_data, self.scene.tile) or {}
            rect_map = self._normalize_barrier_rects(raw_rects, zones)
            if rect_map:
                self.scene.barrier_mgr = BarrierManager(self.scene)
                barriers = []
                for zone in zones:
                    zname = (zone.get("name") or "").strip()
                    key = zname.lower()
                    rect = rect_map.get(key)
                    if not rect:
                        continue
                    rect_obj = rect.copy() if hasattr(rect, "copy") else pygame.Rect(rect)
                    barriers.append(
                        {
                            "rect": rect_obj,
                            "zone": zname,
                            "zone_key": key,
                            "required_wins": zone.get("wins_required", 1),
                            "open": False,
                        }
                    )
                if barriers:
                    self.scene.barrier_mgr.barriers = barriers
                    print(f"[TournamentController] Loaded {len(barriers)} map barriers.")
                    self._sync_barriers_to_context(initial=True)

        self.npcs = []
        self.active_npc = None
        self.busy = False
        if self._has_bracket():
            self._spawn_tutor_gatekeepers()
        self._init_safe_zone()

    def on_minigame_complete(self, result_dict):
        context = self.scene.context
        if not hasattr(context, "score"):
            context.score = {}
        mp = self.scene.map_profile
        outcome = result_dict.get("outcome")
        if outcome == "win":
            context.score["wins"] = context.score.get("wins", 0) + 1
            print(f"[Tournament] Player wins total: {context.score['wins']}")
        else:
            print("[Tournament] Player lost — showing lose screen")
            self.busy = False
            self.active_npc = None
            lose_scene = None
            try:
                from end_screens import LoseScene
                lose_scene = LoseScene(self.scene.manager)
            except Exception as exc:
                print(f"[Tournament] Failed to load LoseScene: {exc}")
            if lose_scene:
                try:
                    from scoreboard import save_score
                except Exception:
                    save_score = None
                if save_score:
                    try:
                        save_score(context, getattr(self.scene, "map_name", "tutor_forest"), "Eliminated")
                    except Exception as exc:
                        print(f"[Tournament] Could not save loss: {exc}")
                self.scene.manager.switch(lose_scene)
            return

        self._sync_barriers_to_context()
        self._handle_post_win_state()

    def handle_event(self, event):
        if not self._has_bracket():
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_e and self.near and not self.busy:
            self._start_npc_challenge(self.near)

    def update(self, dt):
        if self._has_bracket():
            self._update_safe_zone(dt)
            self._update_npc_wander(dt)
            self._update_proximity()
            self._update_ring_penalties(dt)
            self._tick_npc_duels(dt)

    def draw_overlay(self, view):
        barrier_mgr = getattr(self.scene, "barrier_mgr", None)
        if barrier_mgr and barrier_mgr.barriers:
            camx, camy = self.scene._cam()
            barrier_mgr.draw(view, camx, camy)
        if self._has_bracket() and self.near and not self.busy:
            font = pygame.font.SysFont(None, 22)
            label = self.near.get("label") or self._friendly_minigame_name(self.near.get("minigame")) or "Challenge"
            msg = font.render(f"Press E to challenge ({label})", True, (255, 240, 150))
            rect = msg.get_rect()
            rect.midbottom = (view.get_width() // 2, view.get_height() - 28)
            view.blit(msg, rect)
        # Draw shrinking circle
        if self._storm_enabled() and self.safe_center and self.safe_radius:
            camx, camy = self.scene._cam()
            overlay = pygame.Surface(view.get_size(), pygame.SRCALPHA)
            pygame.draw.circle(
                overlay,
                (60, 180, 255, 90),
                (int(self.safe_center[0] - camx), int(self.safe_center[1] - camy)),
                int(self.safe_radius),
                width=2,
            )
            view.blit(overlay, (0, 0))
        # Warning timer if player is outside (single-player only; multiplayer handles UI overlay).
        timer = self.outside_timers.get("player", 0.0)
        if self._storm_enabled() and timer > 0 and not getattr(self.scene, "is_multiplayer", False):
            font = pygame.font.SysFont(None, 26)
            warn = font.render(f"Return inside: {max(0, 5 - int(timer))}s", True, (255, 120, 120))
            view.blit(warn, (10, view.get_height() - 40))
        # Rivals count: single-player overlay only (multiplayer renders on-screen UI to avoid scaling).
        if self._has_bracket() and not getattr(self.scene, "is_multiplayer", False):
            font = pygame.font.SysFont(None, 22)
            count = len(self.npcs)
            txt = font.render(f"Rivals Remaining: {count}", True, (200, 230, 255))
            view.blit(txt, (10, 10))

    def _normalize_barrier_rects(self, rects, zones):
        rect_map = {}
        zone_order = [
            (zone.get("name") or "").strip().lower()
            for zone in zones
            if (zone.get("name") or "").strip()
        ]

        def coerce(value):
            if value is None:
                return None
            if isinstance(value, pygame.Rect):
                return value.copy()
            if isinstance(value, (list, tuple)) and len(value) == 4:
                return pygame.Rect(*value)
            return None

        if isinstance(rects, dict):
            for name, rect in rects.items():
                key = (name or "").strip().lower()
                boxed = coerce(rect)
                if key and boxed:
                    rect_map[key] = boxed
        elif isinstance(rects, (list, tuple)):
            for idx, rect in enumerate(rects):
                if idx >= len(zone_order):
                    break
                key = zone_order[idx]
                boxed = coerce(rect)
                if key and boxed:
                    rect_map[key] = boxed
        else:
            rect_map = {}

        return rect_map

    def _sync_barriers_to_context(self, initial=False):
        barrier_mgr = getattr(self.scene, "barrier_mgr", None)
        mp = self.scene.map_profile
        if not barrier_mgr or not mp or not hasattr(mp, "get_unlock_state"):
            return

        unlocks = mp.get_unlock_state(self.scene.context) or {}
        for barrier in barrier_mgr.barriers:
            key = barrier.get("zone_key") or (barrier.get("zone") or "").strip().lower()
            should_open = bool(unlocks.get(key))
            is_open = barrier.get("open")
            if should_open and not is_open:
                barrier["open"] = True
                if not initial and hasattr(mp, "on_barrier_open"):
                    mp.on_barrier_open(barrier.get("zone"))
            elif not should_open and is_open:
                barrier["open"] = False

    def _spawn_tutor_gatekeepers(self):
        mp = getattr(self.scene, "map_profile", None)
        if not mp:
            return
        get_zones = getattr(mp, "get_zone_data", None)
        if not callable(get_zones):
            return
        try:
            zones = get_zones() or []
        except Exception as exc:
            print(f"[Tournament] Failed to read zone data: {exc}")
            return
        if hasattr(mp, "ensure_tournament_state"):
            try:
                mp.ensure_tournament_state(self.scene.context)
            except Exception as exc:
                print(f"[Tournament] ensure_tournament_state error: {exc}")
        self._refresh_minigame_claims()
        if not self.total_wins_to_clear:
            self.total_wins_to_clear = sum(zone.get("wins_to_clear", 1) for zone in zones)

        score = getattr(self.scene.context, "score", {}) or {}
        current_wins = score.get("wins", 0)

        for zone in zones:
            zone_name = (zone.get("name") or "").strip()
            if not zone_name:
                continue
            zone_key = zone_name.lower()
            required = zone.get("wins_required", 0)
            if current_wins < required:
                target = 0
            else:
                target = zone.get("npc_slots", 1)
                if hasattr(mp, "get_zone_spawn_target"):
                    try:
                        target = mp.get_zone_spawn_target(self.scene.context, zone_name)
                    except Exception as exc:
                        print(f"[Tournament] Spawn target error for {zone_name}: {exc}")
                else:
                    wins_to_clear = zone.get("wins_to_clear", 1)
                    if current_wins >= required + wins_to_clear:
                        target = 0
            self._reconcile_zone_population(zone, zone_key, max(0, int(target)))

    def _reconcile_zone_population(self, zone, zone_key, target_count):
        current_npcs = [npc for npc in self.npcs if npc.get("zone_key") == zone_key]
        owned = len(current_npcs)
        if owned > target_count:
            self._remove_random_zone_npcs(zone_key, owned - target_count)
            current_npcs = [npc for npc in self.npcs if npc.get("zone_key") == zone_key]
            owned = len(current_npcs)
        if target_count <= 0:
            return
        slot_total = max(1, target_count)
        for idx in range(owned, target_count):
            npc = self._create_gatekeeper_for_zone(zone, slot_index=idx, slot_total=slot_total)
            if npc:
                self.npcs.append(npc)

    def _remove_random_zone_npcs(self, zone_key, amount, exclude=None):
        if amount <= 0:
            return []
        pool = [
            npc
            for npc in self.npcs
            if npc.get("zone_key") == zone_key and npc is not exclude
        ]
        if not pool:
            return []
        random.shuffle(pool)
        removed = []
        for npc in pool[:amount]:
            if npc in self.npcs:
                self.npcs.remove(npc)
                self._release_minigame(npc)
                removed.append(npc)
        return removed

    def _has_remaining_challenges(self):
        if self.npcs:
            return True
        mp = getattr(self.scene, "map_profile", None)
        if not mp or not hasattr(mp, "get_zone_data"):
            return False
        if not self.total_wins_to_clear:
            try:
                self.total_wins_to_clear = sum(
                    zone.get("wins_to_clear", 1) for zone in (mp.get_zone_data() or [])
                )
            except Exception:
                self.total_wins_to_clear = 0
        score = getattr(self.scene.context, "score", {}) or {}
        return score.get("wins", 0) < self.total_wins_to_clear

    def _create_gatekeeper_for_zone(self, zone, slot_index=0, slot_total=1):
        zname = (zone.get("name") or "").strip()
        if not zname:
            return None
        minigame = self._pick_minigame()
        rect = self._place_npc_in_zone(zname, slot_index, slot_total)
        if not rect:
            print(f"[Tournament] Could not place gatekeeper for {zname}")
            self._release_minigame(minigame)
            return None
        if not self._ensure_clear_spawn(rect, zname):
            print(f"[Tournament] Spawn blocked in {zname}, skipping NPC.")
            self._release_minigame(minigame)
            return None
        chars = self.scene._discover_characters()
        skin = random.choice(chars) if chars else None
        anim = self._load_anim(skin)
        frame = None
        if anim:
            frame = anim.get("idle", [[None]])[0][0] if anim.get("idle") else None
        else:
            frame = self._load_idle_frame(skin)
        zone_rect = None
        try:
            zone_rect = pygame.Rect(self.scene._layer_rect_px(zname))
        except Exception:
            zone_rect = None
        return {
            "rect": rect,
            "frame": frame,
            "minigame": minigame,
            "label": self._friendly_minigame_name(minigame),
            "zone": zname,
            "zone_key": zname.strip().lower(),
            "wins_required": zone.get("wins_required", 0),
            "zone_rect": zone_rect,
            "anim": anim,
            "foot_y": (anim or {}).get("foot_y"),
        }

    def _pick_minigame(self):
        games = getattr(self.scene, "available_minigames", None) or ["rps_duel"]
        if not games:
            return "rps_duel"
        unused = [g for g in games if g not in self.used_minigames]
        pool = unused if unused else games
        choice = random.choice(pool)
        self.used_minigames.add(choice)
        return choice

    def _release_minigame(self, npc_or_name):
        if isinstance(npc_or_name, str):
            name = npc_or_name
        else:
            name = (npc_or_name or {}).get("minigame")
        if not name:
            return
        for npc in self.npcs:
            if npc is npc_or_name:
                continue
            if npc.get("minigame") == name:
                return
        self.used_minigames.discard(name)

    def _refresh_minigame_claims(self):
        if not self.npcs:
            self.used_minigames.clear()
            return
        active = {npc.get("minigame") for npc in self.npcs if npc.get("minigame")}
        self.used_minigames = {name for name in active if name}

    def _place_npc_in_zone(self, zone_name, slot_index=0, slot_total=1):
        mp = getattr(self.scene, "map_profile", None)
        # Map profile can supply explicit spawn points for tournament NPCs.
        if mp and hasattr(mp, "get_tournament_spawn_points"):
            try:
                pts = mp.get_tournament_spawn_points(
                    slot_total,
                    zone_name,
                    getattr(self.scene, "map_data", None),
                    getattr(self.scene, "tile", 16),
                )
            except TypeError:
                pts = mp.get_tournament_spawn_points(slot_total)
            except Exception:
                pts = None
            if pts and slot_index < len(pts):
                px, py = pts[slot_index]
                rect = pygame.Rect(0, 0, 18, 24)
                rect.midbottom = (int(px), int(py))
                if self._nudge_clear_of_colliders(rect, getattr(self.scene, "tile", 16)):
                    return rect

        try:
            zx, zy, zw, zh = self.scene._layer_rect_px(zone_name)
        except Exception:
            return None
        if zw <= 0 or zh <= 0:
            return None
        sample_cols = []
        if slot_total and slot_total > 0:
            frac = (slot_index + 1) / (slot_total + 1)
            sample_cols.append(max(0.08, min(0.92, frac)))
        sample_cols.extend([0.5, 0.35, 0.65, 0.2, 0.8])
        sample_cols.extend([random.uniform(0.2, 0.8) for _ in range(3)])
        base_y = zy + zh - 6
        tile = max(1, getattr(self.scene, "tile", 16))
        seen = set()
        for col in sample_cols:
            key = round(col, 3)
            if key in seen:
                continue
            seen.add(key)
            px = int(zx + max(12, min(zw - 12, zw * col)))
            rect = pygame.Rect(0, 0, 18, 24)
            rect.midbottom = (px, base_y)
            if self._nudge_clear_of_colliders(rect, tile):
                return rect
        return None

    def _has_partner_near(self, rect: pygame.Rect, radius: float = 48.0):
        if not rect or not hasattr(self.scene, "other_players"):
            return False
        rx, ry = rect.center
        for other in getattr(self.scene, "other_players", []):
            orect = other.get("rect")
            if not orect:
                continue
            ox, oy = orect.center
            if math.hypot(rx - ox, ry - oy) <= radius:
                return True
        return False

    def _nudge_clear_of_colliders(self, rect, tile):
        """Lift/shift the rect until it no longer intersects blockers."""
        colliders = self._collect_blockers()
        if not colliders:
            return True
        step = max(2, tile // 4)
        attempts = 0
        # First try nudging upward
        while any(rect.colliderect(c) for c in colliders) and attempts < 80:
            rect.y -= step
            attempts += 1
        if not any(rect.colliderect(c) for c in colliders):
            return True
        # Try small horizontal wiggles
        for dx in (-step * 2, step * 2, -step * 4, step * 4):
            test = rect.copy()
            test.x += dx
            if not any(test.colliderect(c) for c in colliders):
                rect.x = test.x
                rect.y = test.y
                return True
        return False

    def _ensure_clear_spawn(self, rect, zone_name=None):
        """If a spawn is blocked, try random samples within the zone."""
        colliders = self._collect_blockers()
        existing = [npc.get("rect") for npc in getattr(self, "npcs", []) if npc.get("rect") and npc.get("rect") is not rect]

        def blocked(r):
            return any(r.colliderect(c) for c in colliders) or any(r.colliderect(o) for o in existing)

        if not blocked(rect):
            return True

        try:
            zx, zy, zw, zh = self.scene._layer_rect_px(zone_name) if zone_name else (None, None, None, None)
        except Exception:
            zx = zy = zw = zh = None

        if not zw or not zh:
            return False

        for _ in range(80):
            px = int(zx + random.uniform(0.1, 0.9) * max(8, zw))
            py = int(zy + random.uniform(0.2, 0.95) * max(8, zh))
            test = rect.copy()
            test.midbottom = (px, py)
            if not blocked(test):
                rect.midbottom = test.midbottom
                return True
        return False

    def _collect_blockers(self):
        colliders = list(getattr(self.scene, "colliders", []))
        barrier_mgr = getattr(self.scene, "barrier_mgr", None)
        if barrier_mgr:
            colliders.extend(barrier_mgr.get_blockers())
        return colliders

    def _handle_post_win_state(self):
        self.busy = False
        mp = getattr(self.scene, "map_profile", None)
        if self._has_bracket():
            zone_name = None
            zone_key = None
            if self.active_npc:
                zone_name = self.active_npc.get("zone")
                zone_key = self.active_npc.get("zone_key")
                if self.active_npc in self.npcs:
                    self.npcs.remove(self.active_npc)
                self._release_minigame(self.active_npc)
            self.active_npc = None
            self.near = None

            random_culls = 0
            if mp and zone_name and hasattr(mp, "record_victory_and_culls"):
                try:
                    result = mp.record_victory_and_culls(self.scene.context, zone_name)
                except Exception as exc:
                    print(f"[Tournament] record_victory_and_culls failed: {exc}")
                    result = {}
                random_culls = result.get("random_removed")
                if random_culls is None:
                    random_culls = max(0, result.get("extra_random", 0))
                if result.get("zone_complete"):
                    print(f"[Tournament] {zone_name.title()} bracket cleared.")
                elif "remaining" in result:
                    remaining = result.get("remaining")
                    print(f"[Tournament] {remaining} rivals remain in {zone_name}.")
            if zone_key and random_culls:
                self._remove_random_zone_npcs(zone_key, random_culls)

            self._spawn_tutor_gatekeepers()

            if not self._has_remaining_challenges():
                self._declare_victory()
            return

        self.active_npc = None

    def _declare_victory(self):
        print("[Tournament] All gatekeepers cleared — declaring victory!")
        try:
            from scoreboard import save_score
        except Exception:
            save_score = None
        try:
            from end_screens import WinGameScene
        except Exception as exc:
            print(f"[Tournament] Failed to load WinGameScene: {exc}")
            return

        context = self.scene.context
        try:
            if save_score:
                save_score(context, getattr(self.scene, "map_name", "tutor_forest"), "Champion")
        except Exception as exc:
            print(f"[Tournament] Failed to save score: {exc}")

        self.scene.manager.switch(WinGameScene(self.scene.manager))

    def _update_proximity(self):
        if self.busy:
            self.near = None
            return
        self.near = None
        if not self.npcs:
            return
        px, py = self.scene.player_rect.center
        for npc in self.npcs:
            rect = npc.get("rect")
            if not rect:
                continue
            rcx, rcy = rect.center
            if math.hypot(px - rcx, py - rcy) < INTERACT_RADIUS:
                self.near = npc
                break

    def _start_npc_challenge(self, npc):
        if not npc:
            return
        minigame = npc.get("minigame") or self._pick_minigame()
        npc["minigame"] = minigame
        npc["label"] = npc.get("label") or self._friendly_minigame_name(minigame)
        self.busy = True
        self.active_npc = npc
        print(f"[Tournament] Starting gatekeeper minigame: {minigame}")
        self._launch_minigame(minigame)

    def _friendly_minigame_name(self, name):
        if not name:
            return None
        return str(name).replace("_", " ").title()

    def _storm_enabled(self):
        name = getattr(self.scene, "map_name", "").lower()
        mode = (getattr(self.scene, "mode", "") or "").lower()
        return name == "test_arena" and mode == "tournament"

    def _teleport_player_to_safe(self):
        """After winning a duel, drop player near center but inside the safe circle."""
        if not self.safe_center or not self.safe_radius:
            return
        cx, cy = self.safe_center
        r = max(self.safe_radius_min, int(self.safe_radius * 0.5))
        ang = random.uniform(0, math.tau if hasattr(math, "tau") else 6.283)
        px = int(cx + r * math.cos(ang))
        py = int(cy + r * math.sin(ang))
        self.scene.player_rect.midbottom = (px, py)

    def _init_safe_zone(self):
        """Setup shrinking circle center/radius based on map size."""
        if not self._storm_enabled():
            self.safe_center = None
            self.safe_radius = None
            self.outside_timers = {"player": 0.0}
            return
        w = getattr(self.scene, "map_w_px", 0)
        h = getattr(self.scene, "map_h_px", 0)
        if w <= 0 or h <= 0:
            return
        cx = w // 2
        cy = h // 2
        self.safe_center = (cx, cy)
        # Start large to cover most of the map, shrink slowly.
        self.safe_radius = int(max(w, h) * 0.75)
        self.safe_radius_min = max(220, min(w, h) // 3)
        self.shrink_elapsed = 0.0
        self.outside_timers = {"player": 0.0}

    def _update_safe_zone(self, dt):
        if not self._storm_enabled():
            return
        if not self.safe_radius or not self.safe_center:
            return
        self.shrink_elapsed += dt
        if self.shrink_elapsed < self.shrink_delay:
            return
        if self.safe_radius > self.safe_radius_min:
            self.safe_radius = max(self.safe_radius_min, self.safe_radius - self.shrink_rate * dt)

    def _update_ring_penalties(self, dt):
        if getattr(self.scene, "is_spectator", False):
            return
        if not self._storm_enabled():
            return
        if not self.safe_radius or not self.safe_center:
            return
        # Skip penalties if currently in a minigame.
        if self.busy:
            # Clear timers for engaged actors so they resume fresh after minigame.
            self.outside_timers["player"] = 0.0
            if self.active_npc:
                self.outside_timers[f"npc_{id(self.active_npc)}"] = 0.0
            return
        # Active duel NPC is protected while in minigame.
        protected_id = id(self.active_npc) if self.active_npc else None
        # Player
        if self._check_ring_for_actor(self.scene.player_rect.center, "player", dt):
            # eliminate player (lose)
            self.outside_timers["player"] = 0.0
            if getattr(self.scene, "is_multiplayer", False):
                setattr(self.scene, "is_spectator", True)
                self.busy = False
                self.active_npc = None
                # also set elimination banner on arena if present
                if hasattr(self.scene, "elim_banner"):
                    self.scene.elim_banner = "Eliminated — Spectating"
                    self.scene.elim_banner_time = 5.0
            else:
                result = {"minigame": "storm", "outcome": "lose"}
                self.on_minigame_complete(result)
            return
        # NPCs
        for npc in list(self.npcs):
            if id(npc) == protected_id:
                self.outside_timers[f"npc_{id(npc)}"] = 0.0
                continue
            rect = npc.get("rect")
            if not rect:
                continue
            key = f"npc_{id(npc)}"
            if self._check_ring_for_actor(rect.center, key, dt):
                self.outside_timers[key] = 0.0
                self._eliminate_npc(npc, reason="storm")

    def _check_ring_for_actor(self, pos, key, dt):
        dist = math.hypot(pos[0] - self.safe_center[0], pos[1] - self.safe_center[1])
        timer = self.outside_timers.get(key, 0.0)
        # Allow a small tolerance so touching the ring doesn't insta-eliminate.
        tol = self.safe_radius * 1.02
        if dist > tol:
            timer += dt
            self.outside_timers[key] = timer
            if timer >= 5.0:
                return True
        else:
            self.outside_timers[key] = 0.0
        return False

    def _eliminate_npc(self, npc, reason="storm"):
        if npc in self.npcs:
            self.npcs.remove(npc)
        self._release_minigame(npc)
        # award a win to the player to keep bracket progress
        context = self.scene.context
        if not hasattr(context, "score") or context.score is None:
            context.score = {}
        context.score["wins"] = context.score.get("wins", 0) + 1
        mp = getattr(self.scene, "map_profile", None)
        if mp and hasattr(mp, "record_victory_and_culls"):
            try:
                zone = npc.get("zone") or "perimeter"
                mp.record_victory_and_culls(context, zone)
            except Exception:
                pass
        self._sync_barriers_to_context()
        self._handle_post_win_state()

    def _eliminate_npc_sim(self, npc, timeout=0.0):
        """Background duel elimination without granting player wins."""
        if npc in self.npcs:
            self.npcs.remove(npc)
        self._release_minigame(npc)
        self._decrement_zone_remaining(npc)
        self._sync_barriers_to_context()
        if timeout > 0:
            # simulate a minigame downtime for the pair
            self.npc_duel_timer = max(self.npc_duel_timer, timeout)
        if not self._has_remaining_challenges() and not self.busy:
            self._declare_victory()

    def _decrement_zone_remaining(self, npc):
        mp = getattr(self.scene, "map_profile", None)
        context = getattr(self.scene, "context", None)
        if not mp or not context or not hasattr(context, "flags"):
            return
        flag = getattr(mp, "TOURNAMENT_STATE_FLAG", None)
        if not flag:
            if "test_arena_tournament" in context.flags:
                flag = "test_arena_tournament"
        if not flag:
            return
        try:
            if hasattr(mp, "ensure_tournament_state"):
                mp.ensure_tournament_state(context)
        except Exception:
            pass
        zones = context.flags.get(flag, {}).get("zones", {})
        key = (npc.get("zone") or npc.get("zone_key") or "").strip().lower()
        if key and key in zones and "remaining" in zones[key]:
            zones[key]["remaining"] = max(0, zones[key].get("remaining", 0) - 1)
    def _update_npc_wander(self, dt):
        """Lightweight wandering inside each zone for Tutor Forest NPCs."""
        if not self.npcs:
            return
        colliders = self._collect_blockers()
        speed_base = 32  # modest pace to avoid tunneling into walls
        for npc in self.npcs:
            rect = npc.get("rect")
            if not rect:
                continue
            state = npc.setdefault("wander", {})
            timer = state.get("timer", 0) - dt
            vx, vy = state.get("vel", (0, 0))
            if timer <= 0:
                dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)]
                if self._storm_enabled() and self.safe_center and self.safe_radius:
                    cx, cy = self.safe_center
                    dx = cx - rect.centerx
                    dy = cy - rect.centery
                    dist = math.hypot(dx, dy) or 1.0
                    dirs.append((dx / dist, dy / dist))
                    # Stronger steer inward if already outside or close to wall.
                    if dist > self.safe_radius * 0.95:
                        vx, vy = dx / dist, dy / dist
                    else:
                        vx, vy = random.choice(dirs)
                else:
                    vx, vy = random.choice(dirs)
                mag = speed_base + random.uniform(-12, 12)
                vx *= mag
                vy *= mag
                timer = random.uniform(0.4, 1.2)
            state["timer"] = timer
            state["vel"] = (vx, vy)

            move_x = int(round(vx * dt))
            move_y = int(round(vy * dt))
            if move_x or move_y:
                zone_rect = npc.get("zone_rect")
                future = rect.copy()
                future.x += move_x
                future.y += move_y
                blocked = any(future.colliderect(c) for c in colliders)
                if zone_rect and not zone_rect.contains(future):
                    blocked = True
                if blocked:
                    state["vel"] = (0, 0)
                    state["timer"] = 0.25
                else:
                    rect.midbottom = future.midbottom
            self._advance_anim(npc, vx, vy, dt)
            # If still intersecting something after movement, freeze and nudge up slightly.
            if colliders and any(rect.colliderect(c) for c in colliders):
                rect.y -= max(1, self.scene.tile // 4)
                state["vel"] = (0, 0)
                state["timer"] = 0.2

        self._tick_npc_duels(dt)

    def _tick_npc_duels(self, dt):
        """Let NPCs duel each other in the background to thin the field."""
        if not self.npcs or len(self.npcs) < 2:
            return
        self.npc_duel_timer -= dt
        if self.npc_duel_timer > 0:
            return
        # reset timer
        self.npc_duel_timer = self.npc_duel_interval + random.uniform(-2.0, 2.0)
        # roll chance
        if random.random() > self.npc_duel_chance:
            return
        # pick two distinct NPCs that are not overlapping
        tries = 0
        pair = None
        while tries < 10:
            a, b = random.sample(self.npcs, 2)
            if not a.get("rect") or not b.get("rect"):
                tries += 1
                continue
            if a["rect"].colliderect(b["rect"]):
                tries += 1
                continue
            pair = (a, b)
            break
        if not pair:
            return
        a, b = pair
        winner, loser = (a, b) if random.random() < 0.5 else (b, a)
        print(f"[Tournament] NPC duel: {winner.get('label','npc')} defeats {loser.get('label','npc')}")
        self._eliminate_npc_sim(loser, timeout=30.0)
        # small wander reset for winner so they keep moving
        winner.setdefault("wander", {})["timer"] = 0.1

    def _move_npc(self, rect, dx, dy, colliders, zone_rect=None, wander_state=None):
        # Legacy: keep as a no-op clamp for any callers still using it.
        if zone_rect:
            if rect.left < zone_rect.left:
                rect.left = zone_rect.left
            if rect.right > zone_rect.right:
                rect.right = zone_rect.right
            if rect.bottom > zone_rect.bottom:
                rect.bottom = zone_rect.bottom
            if rect.top < zone_rect.top:
                rect.top = zone_rect.top


class BarrierManager:
    def __init__(self, scene):
        self.scene = scene
        self.barriers = []
        self._font = None

    def get_blockers(self):
        return [b["rect"] for b in self.barriers if not b.get("open")]

    def draw(self, view, camx, camy):
        """Draw gates as colored lines (debug)."""
        if self._font is None:
            self._font = pygame.font.SysFont(None, 18)
        for b in self.barriers:
            if "rect" not in b or not b["rect"]:
                continue
            color = (255, 220, 80) if b.get("open") else (200, 60, 60)
            pygame.draw.rect(view, color, b["rect"].move(-camx, -camy), 3)
