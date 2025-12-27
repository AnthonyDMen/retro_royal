import json, math, time, pygame, random
import importlib, importlib.util
from pathlib import Path
from scene_manager import Scene
from sound_engine import play_step, load_map_profile
from game_context import GameContext
from pause_menu import PauseMenuScene
from game_modes import ArcadeController, TournamentController



# === Constants / Paths ===
ROOT = Path(__file__).parent
MAP_DIR = ROOT / "maps" / "test_arena"
CHAR_DIR = ROOT / "characters" / "hero"

MAP_JSON = MAP_DIR / "map.json"
SPRITESHEET = MAP_DIR / "spritesheet.png"
PLAYER_IDLE_SHEET = CHAR_DIR / "idle.png"
PLAYER_WALK_SHEET = CHAR_DIR / "walk.png"

PLAYER_FRAME_W = 32
PLAYER_FRAME_H = 32
PLAYER_FOOT_Y = 28
PLAYER_SPEED = 140
INTERACT_RADIUS = 42
ZOOM_DEFAULT = 2.5


# === Sprite Animator ===
class SpriteAnimator:
    def __init__(self, idle_sheet, walk_sheet, frame_w, frame_h, foot_y):
        self.idle_frames = self._slice(idle_sheet, frame_w, frame_h)
        self.walk_frames = self._slice(walk_sheet, frame_w, frame_h)
        self.frame_w, self.frame_h = frame_w, frame_h
        self.foot_y = foot_y
        self.state = "idle"
        self.facing = "down"
        self.index = 0.0
        self.speed_idle = 2.0
        self.speed_walk = 10.0
        self.DIR_ROWS = {"down": 0, "up": 1, "left": 2, "right": 3}

    def _slice(self, sheet, w, h):
        rows = sheet.get_height() // h
        cols = sheet.get_width() // w
        frames = []
        for r in range(rows):
            row = []
            for c in range(cols):
                surf = pygame.Surface((w, h), pygame.SRCALPHA)
                surf.blit(sheet, (0, 0), (c * w, r * h, w, h))
                row.append(surf)
            frames.append(row)
        return frames

    def update(self, dt, vx, vy):
        moving = abs(vx) + abs(vy) > 0
        self.state = "walk" if moving else "idle"
        if moving:
            if abs(vx) >= abs(vy):
                self.facing = "right" if vx > 0 else "left"
            else:
                self.facing = "down" if vy > 0 else "up"
        self.index = (
            self.index + (self.speed_walk if moving else self.speed_idle) * dt
        ) % len(self.walk_frames[0])

    def draw(self, surf, cx, by, camx, camy):
        row = self.DIR_ROWS[self.facing]
        frames = (
            self.walk_frames[row] if self.state == "walk" else self.idle_frames[row]
        )
        frame = frames[int(self.index) % len(frames)]
        x = cx - self.frame_w // 2 - camx
        y = by - self.foot_y - camy
        surf.blit(frame, (x, y))


# === Arena Scene ===
class ArenaScene(Scene):
    def __init__(self, manager, selection=None, context=None):
        super().__init__(manager)
        self.screen = manager.screen
        self.clock = pygame.time.Clock()
        self.selection = selection or {}
        self.context = context or GameContext()
        self.is_multiplayer = bool(self.context.flags.get("multiplayer"))
        self.mode = None
        self.controller = None
        self.available_minigames = self._discover_minigames()
        self.character_folders = self._discover_characters()
        self.npcs = []
        self.other_players = []

        # --- Determine selected map & character ---
        maps_root = ROOT / "maps"
        chars_root = ROOT / "characters"

        map_name = (self.selection.get("map_name") or "test_arena").strip()
        if self.is_multiplayer:
            map_name = "test_arena"
        map_dir_path = maps_root / map_name
        if not map_dir_path.exists():
            available_maps = sorted(d.name for d in maps_root.iterdir() if d.is_dir())
            if available_maps:
                map_name = available_maps[0]
                map_dir_path = maps_root / map_name
        self.map_name = map_name
        self.is_tutor_forest = map_name.lower() == "tutor_forest"

        char_name = self.selection.get("char_name")
        if not char_name:
            char_name = "classic"
        char_dir_path = chars_root / char_name
        if not char_dir_path.exists():
            available_chars = sorted(d.name for d in chars_root.iterdir() if d.is_dir())
            if available_chars:
                char_name = available_chars[0]
                char_dir_path = chars_root / char_name
            else:
                raise FileNotFoundError("No character sprite folders found in /characters")

        MAP_DIR = map_dir_path
        CHAR_DIR = char_dir_path

        map_json = MAP_DIR / "map.json"
        sheet_path = MAP_DIR / "spritesheet.png"
        PLAYER_IDLE_SHEET = CHAR_DIR / "idle.png"
        PLAYER_WALK_SHEET = CHAR_DIR / "walk.png"

        # --- Load map JSON ---
        with open(map_json, "r") as f:
            self.map_data = json.load(f)
        for _ly in self.map_data.get("layers", []):
            if "name" in _ly and isinstance(_ly["name"], str):
                _ly["name"] = _ly["name"].strip().lower()

        self.tile = self.map_data["tileSize"]
        self.map_w_px = self.map_data["mapWidth"] * self.tile
        self.map_h_px = self.map_data["mapHeight"] * self.tile
        self.sheet = pygame.image.load(sheet_path).convert_alpha()
        self.sheet_cols = self.sheet.get_width() // self.tile

        # ✅ Load map profile BEFORE building the map
        self.map_profile = load_map_profile(map_json)
        if self.map_profile and hasattr(self.map_profile, "on_load"):
            if type(self).__name__.lower() != "tournamentarena":
                try:
                    self.map_profile.on_load(self.context, self.manager)
                except TypeError:
                    self.map_profile.on_load(self.context)
            else:
                print("[ArenaScene] Skipping map_profile.on_load for TournamentArena (prevent recursion).")

        profile_mode = None
        if self.map_profile and hasattr(self.map_profile, "get_mode"):
            try:
                profile_mode = self.map_profile.get_mode(self.context)
            except TypeError:
                profile_mode = self.map_profile.get_mode()
        self.mode = (
            self.context.flags.get("mode")
            or profile_mode
            or (self.selection.get("mode") if self.selection else None)
            or "arcade"
        )
        if self.is_multiplayer:
            self.mode = "tournament"
        if self.is_tutor_forest and self.mode != "tournament":
            print("[TutorForest] Forcing tournament mode for Tutor Forest map.")
            self.mode = "tournament"

        # --- Build map (after profile is available) ---
        self.map_surface, self.overlay_surface, self.colliders = self._build_map()

        # --- Player setup ---
        idle_img = pygame.image.load(PLAYER_IDLE_SHEET).convert_alpha()
        walk_img = pygame.image.load(PLAYER_WALK_SHEET).convert_alpha()
        self.player_rect = pygame.Rect(0, 0, 10, 6)
        self.player_rect.midbottom = (300, 180)
        self.anim = SpriteAnimator(
            idle_img, walk_img, PLAYER_FRAME_W, PLAYER_FRAME_H, PLAYER_FOOT_Y
        )
        if not self.is_multiplayer:
            if self.mode == "tournament":
                self.controller = TournamentController(self)
            else:
                self.controller = ArcadeController(self)
            if self.controller:
                self.controller.initialize_spawns()
        else:
            # Multiplayer uses tournament layout/overlays; arcade is disabled.
            self.controller = TournamentController(self)
            if self.controller:
                self.controller.initialize_spawns()

        self.npcs = []
        self.near_npc = False

        spawn_at = self.selection.get("spawn_at")
        if spawn_at and len(spawn_at) == 2:
            try:
                sx, sy = int(spawn_at[0]), int(spawn_at[1])
                self.player_rect.midbottom = (sx, sy)
            except (ValueError, TypeError):
                pass

        # --- Camera & zoom ---
        self.zoom = ZOOM_DEFAULT
        self._make_view()

        # --- Misc ---
        self.start_time = time.time()

        # === TEMPORARY FIX: Tutor Forest proper spawn ===
        if self.is_tutor_forest:
            print("[TutorForest] Forcing spawn to bottom of 'large level'")
            tile = self.tile
            spawn_set = False
            for layer in self.map_data.get("layers", []):
                lname = (layer.get("name") or "").strip().lower()
                if lname == "large level":
                    xs, ys = [], []
                    if "tiles" in layer:
                        for t in layer["tiles"]:
                            xs.append(int(t.get("x", 0)))
                            ys.append(int(t.get("y", 0)))
                    elif "data" in layer and "width" in layer:
                        data = layer["data"]
                        width = int(layer["width"])
                        filled = [i for i, tid in enumerate(data) if tid and tid > 0]
                        if filled:
                            min_i, max_i = min(filled), max(filled)
                            minx, miny = (min_i % width), (min_i // width)
                            maxx, maxy = (max_i % width), (max_i // width)
                            xs = [minx, maxx]
                            ys = [miny, maxy]
                    if xs and ys:
                        minx, maxx = min(xs), max(xs)
                        miny, maxy = min(ys), max(ys)
                        center_col = (minx + maxx) / 2.0
                        px = int(round((center_col + 0.5) * tile))
                        bottom_row = maxy
                        safe_row = max(miny, bottom_row - 1)  # stay one tile above the tree wall
                        py = int(((safe_row + 1) * tile) - 8)
                        rect = self.player_rect.copy()
                        rect.midbottom = (px, py)

                        def collides(r):
                            return any(r.colliderect(c) for c in self.colliders)

                        attempts = 0
                        while collides(rect) and attempts < 40:
                            rect.y -= max(2, tile // 4)
                            attempts += 1
                        if attempts:
                            print(f"[TutorForest] Adjusted spawn upward by {attempts} steps to avoid trees.")
                        self.player_rect.midbottom = rect.midbottom
                        print(f"[TutorForest] Player spawn set to {self.player_rect.midbottom} within large level zone.")
                        spawn_set = True
                    break
            if not spawn_set:
                print("[TutorForest] Large level layer not found; using default spawn.")

    # --- Map Helpers ---
    def _build_map(self):
        """Render map layers and generate collider rects for both custom and Sprite Fusion JSON."""
        base = pygame.Surface((self.map_w_px, self.map_h_px), pygame.SRCALPHA)
        overlay = pygame.Surface((self.map_w_px, self.map_h_px), pygame.SRCALPHA)
        colliders = []

        cols = self.sheet.get_width() // self.tile
        layers = self.map_data.get("layers", [])
        order_lookup = {}
        overlay_names = {"overlay"}

        if self.map_profile:
            if hasattr(self.map_profile, "get_draw_order"):
                order_lookup = {
                    name.lower(): idx
                    for idx, name in enumerate(self.map_profile.get_draw_order())
                }
            overlay_list = []
            if hasattr(self.map_profile, "get_overlay_layers"):
                overlay_list = self.map_profile.get_overlay_layers()
            else:
                overlay_list = getattr(self.map_profile, "OVERLAY_LAYERS", [])
            overlay_names |= {name.lower() for name in overlay_list}

        if order_lookup:
            layers_iter = sorted(
                layers,
                key=lambda layer: order_lookup.get(
                    layer.get("name", "").lower(), len(order_lookup)
                ),
            )
        else:
            layers_iter = layers

        margin = getattr(self.map_profile, "COLLIDER_MARGIN", 0) if self.map_profile else 0
        if isinstance(margin, (list, tuple)):
            if len(margin) == 2:
                ml, mt = margin
                mr, mb = ml, mt
            elif len(margin) == 4:
                ml, mt, mr, mb = margin
            else:
                ml = mt = mr = mb = 0
        else:
            ml = mt = mr = mb = margin

        for layer in layers_iter:
            name = layer.get("name", "").lower()
            is_overlay = name in overlay_names
            is_collider = layer.get("collider", False)

            # --- CASE 1: Custom format (with explicit x,y per tile) ---
            if "tiles" in layer:
                for t in layer["tiles"]:
                    tid = int(t.get("id", -1))
                    if tid < 0:
                        continue

                    # --- apply per-map tile offset if defined ---
                    offset = getattr(self.map_profile, "TILE_OFFSET", 0)
                    gid = tid + offset

                    tx = (gid % cols) * self.tile
                    ty = (gid // cols) * self.tile
                    x = int(t["x"]) * self.tile
                    y = int(t["y"]) * self.tile
                    tile_img = pygame.Surface((self.tile, self.tile), pygame.SRCALPHA)
                    tile_img.blit(self.sheet, (0, 0), (tx, ty, self.tile, self.tile))
                    (overlay if is_overlay else base).blit(tile_img, (x, y))
                    if is_collider:
                        rect = pygame.Rect(
                            x + ml,
                            y + mt,
                            self.tile - ml - mr,
                            self.tile - mt - mb,
                        )
                        if rect.width > 0 and rect.height > 0:
                            colliders.append(rect)

            # --- CASE 2: Sprite Fusion / Tiled export (flat data array) ---
            elif "data" in layer:
                data = layer["data"]
                width = layer.get("width", self.map_data.get("mapWidth", 0))
                for i, tid in enumerate(data):
                    if tid <= 0:
                        continue
                    # Fusion exports 1-based IDs like Tiled
                    gid = tid - 1
                    tx = (gid % cols) * self.tile
                    ty = (gid // cols) * self.tile
                    x = (i % width) * self.tile
                    y = (i // width) * self.tile
                    tile_img = pygame.Surface((self.tile, self.tile), pygame.SRCALPHA)
                    tile_img.blit(self.sheet, (0, 0), (tx, ty, self.tile, self.tile))
                    (overlay if is_overlay else base).blit(tile_img, (x, y))
                    if is_collider:
                        rect = pygame.Rect(
                            x + ml,
                            y + mt,
                            self.tile - ml - mr,
                            self.tile - mt - mb,
                        )
                        if rect.width > 0 and rect.height > 0:
                            colliders.append(rect)

            else:
                print(f"[Map] Skipping unknown layer format: {layer.get('name')}")

        print(f"[Map] Loaded {len(layers)} layers, {len(colliders)} colliders.")
        return base, overlay, colliders


    def _discover_minigames(self):
        base = Path(__file__).parent / "minigames"
        valid = []
        if not base.exists():
            return valid
        for d in base.iterdir():
            if not d.is_dir():
                continue
            if d.name.lower().startswith(("template", "test")):
                continue
            game_file = d / "game.py"
            if not game_file.exists():
                continue
            mod = None
            module_name = f"minigames.{d.name}.game"
            try:
                mod = importlib.import_module(module_name)
            except Exception:
                try:
                    spec = importlib.util.spec_from_file_location(f"_minigame_{d.name}", game_file)
                    if not spec or not spec.loader:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                except Exception:
                    mod = None
            if mod and hasattr(mod, "launch"):
                valid.append(d.name)
        return valid

    def _discover_characters(self):
        chars_root = Path(__file__).parent / "characters"
        if not chars_root.exists():
            return []
        # Sort for deterministic ordering across processes.
        return sorted([d for d in chars_root.iterdir() if d.is_dir()], key=lambda p: p.name.lower())

    def _layer_rect_px(self, name_ci: str):
        """Return (x,y,w,h) in pixels for a layer whose name matches case-insensitively."""
        name_ci = (name_ci or "").strip().lower()
        tile = self.tile
        for layer in self.map_data.get("layers", []):
            lname = (layer.get("name") or "").strip().lower()
            if lname != name_ci:
                continue

            if "tiles" in layer:
                xs, ys = [], []
                for t in layer["tiles"]:
                    try:
                        xs.append(int(t.get("x", 0)))
                        ys.append(int(t.get("y", 0)))
                    except Exception:
                        continue
                if not xs or not ys:
                    break
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
                x = minx * tile
                y = miny * tile
                w = (maxx - minx + 1) * tile
                h = (maxy - miny + 1) * tile
                return (x, y, w, h)

            if "data" in layer:
                data = layer.get("data") or []
                width = int(layer.get("width", self.map_data.get("mapWidth", 0)) or 0)
                if width <= 0 or not data:
                    break
                filled = [i for i, tid in enumerate(data) if tid and tid > 0]
                if not filled:
                    break
                min_i, max_i = min(filled), max(filled)
                minx, miny = (min_i % width), (min_i // width)
                maxx, maxy = (max_i % width), (max_i // width)
                x = minx * tile
                y = miny * tile
                w = (maxx - minx + 1) * tile
                h = (maxy - miny + 1) * tile
                return (x, y, w, h)

        if name_ci and name_ci != "perimeter":
            print(f"[Spawn] Layer '{name_ci}' not found, using full map.")
        return (0, 0, self.map_w_px, self.map_h_px)

    def _make_view(self):
        vw = max(1, self.screen.get_width() // self.zoom)
        vh = max(1, self.screen.get_height() // self.zoom)
        self.view = pygame.Surface((vw, vh), pygame.SRCALPHA)

    def _cam(self):
        vw, vh = self.view.get_size()
        cx = max(0, min(self.player_rect.centerx - vw // 2, self.map_w_px - vw))
        cy = max(0, min(self.player_rect.centery - vh // 2, self.map_h_px - vh))
        return cx, cy

    # --- Input / Interaction ---
    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.manager.push(PauseMenuScene(self.manager, self.context, self))
                return
            if event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                self.zoom = min(4.0, self.zoom + 0.2)
                self._make_view()
                return
            elif event.key == pygame.K_MINUS:
                self.zoom = max(1.0, self.zoom - 0.2)
                self._make_view()
                return
            else:
                if self.controller:
                    self.controller.handle_event(event)
                return

    # --- Logic ---
    def update(self, dt):
        self.context.add_playtime(dt)
        keys = pygame.key.get_pressed()
        dx = dy = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            dx -= PLAYER_SPEED * dt
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            dx += PLAYER_SPEED * dt
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            dy -= PLAYER_SPEED * dt
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            dy += PLAYER_SPEED * dt

        self._move_with_collision(dx, dy)
        self.anim.update(dt, dx, dy)

        if dx or dy:
            play_step()

        if self.controller:
            self.controller.update(dt)

    def _move_with_collision(self, dx, dy):
        rect = self.player_rect
        move_x = int(round(dx))
        move_y = int(round(dy))
        colliders = list(self.colliders)
        barrier_mgr = getattr(self, "barrier_mgr", None)
        if barrier_mgr:
            colliders.extend(barrier_mgr.get_blockers())

        if move_x:
            rect.x += move_x
            for c in colliders:
                if rect.colliderect(c):
                    if move_x > 0:
                        rect.right = c.left
                    else:
                        rect.left = c.right

        if move_y:
            rect.y += move_y
            for c in colliders:
                if rect.colliderect(c):
                    if move_y > 0:
                        rect.bottom = c.top
                    else:
                        rect.top = c.bottom

        # simple clamp to keep player inside map bounds
        rect.left = max(0, min(rect.left, self.map_w_px - rect.width))
        rect.top = max(0, min(rect.top, self.map_h_px - rect.height))

    # --- Draw ---
    def draw(self):
        camx, camy = self._cam()
        self.view.fill((0, 0, 0))
        self.view.blit(self.map_surface, (-camx, -camy))
        # draw overlay tiles before actors so characters remain on top
        self.view.blit(self.overlay_surface, (-camx, -camy))

        for other in getattr(self, "other_players", []):
            rect = other.get("rect")
            if not rect:
                continue
            color = other.get("color", (120, 180, 255) if other.get("is_bot") else (255, 200, 120))
            sprite = other.get("sprite")
            if sprite is not None:
                foot = other.get("foot_y") or sprite.get_height()
                px = rect.centerx - sprite.get_width() // 2 - camx
                py = rect.bottom - foot - camy
                self.view.blit(sprite, (px, py))
            else:
                pygame.draw.rect(self.view, color, rect.move(-camx, -camy))
            label = other.get("label")
            if label:
                font = pygame.font.SysFont(None, 16)
                txt = font.render(label, True, (240, 240, 255))
                self.view.blit(
                    txt, txt.get_rect(midbottom=(rect.centerx - camx, rect.top - 4 - camy))
                )

        if self.controller and getattr(self.controller, "npcs", None):
            for npc in self.controller.npcs:
                frame = npc.get("frame")
                if frame is not None:
                    fw, fh = frame.get_width(), frame.get_height()
                    px, py = npc["rect"].centerx, npc["rect"].bottom
                    foot_y = npc.get("foot_y", fh)
                    self.view.blit(frame, (px - fw // 2 - camx, py - foot_y - camy))
                else:
                    pygame.draw.rect(
                        self.view, (200, 60, 60), npc["rect"].move(-camx, -camy)
                    )

        # Skip drawing the local player when spectating in multiplayer.
        if not getattr(self, "is_spectator", False):
            self.anim.draw(
                self.view, self.player_rect.centerx, self.player_rect.bottom, camx, camy
            )

        if self.controller:
            self.controller.draw_overlay(self.view)

        scaled = pygame.transform.scale(self.view, self.screen.get_size())
        self.screen.blit(scaled, (0, 0))

        elapsed = time.time() - self.start_time
        f = pygame.font.Font(None, 22)
        t = f.render(
            f"Time: {elapsed:.1f}s | Zoom: {self.zoom:.1f}x", True, (255, 255, 255)
        )
        self.screen.blit(t, (8, 8))
        px, py = self.player_rect.midbottom
        coord_txt = f"Player @ ({px},{py})"
        c = f.render(coord_txt, True, (180, 255, 200))
        self.screen.blit(c, (8, 30))
        if self.mode == "tournament" and hasattr(self, "round"):
            layer_name = self._round_layer_name(self.round).title()
            rtxt = pygame.font.Font(None, 22).render(
                f"Round {self.round}: {layer_name}", True, (255, 255, 200)
            )
            self.screen.blit(rtxt, (8, 52))


class TournamentArena(ArenaScene):
    """Placeholder so map profiles that import TournamentArena can resolve the symbol."""
    pass
