import json
import asyncio
import pygame
from scene_manager import Scene
from arena_scene import ArenaScene
from pathlib import Path
from scoreboard import draw_highscores
from multiplayer import LobbyServer, LobbyClient, MultiplayerArenaScene
from resource_path import resource_path

FADE_SPEED = 200


class Button:
    def __init__(self, rect, text, font):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.font = font
        self.hover = False

    def draw(self, surf):
        bg = (50, 50, 70) if not self.hover else (80, 80, 120)
        pygame.draw.rect(surf, bg, self.rect, border_radius=10)
        pygame.draw.rect(surf, (180, 180, 220), self.rect, 2, border_radius=10)
        txt = self.font.render(self.text, True, (240, 240, 255))
        surf.blit(txt, txt.get_rect(center=self.rect.center))

    def hit(self, pos):
        return self.rect.collidepoint(pos)


# -----------------------------------------------------------------
# Main Menu Scene
# -----------------------------------------------------------------
class MainMenu(Scene):
    def __init__(self, manager):
        super().__init__(manager)
        self.screen = manager.screen
        w, h = manager.size
        self.w, self.h = w, h
        self.font_title = pygame.font.SysFont(None, 72)
        self.font_btn = pygame.font.SysFont(None, 36)
        self.show_scores = False
        self.scroll_y = 0

        bw, bh, gap = 240, 56, 16
        cx = w // 2 - bw // 2
        base_labels = [
            "Play",
            "High Scores",
            "Settings",
            "Exit",
        ]
        self.mp_block_h = 74
        stack_height = bh * len(base_labels) + gap * (len(base_labels) - 1) + self.mp_block_h
        top = h // 2 - stack_height // 2

        self.buttons = []
        y = top
        self.buttons.append(Button((cx, y, bw, bh), base_labels[0], self.font_btn))
        y += bh + gap
        self.mp_block_y = y
        y += self.mp_block_h + gap
        for label in base_labels[1:]:
            self.buttons.append(Button((cx, y, bw, bh), label, self.font_btn))
            y += bh + gap

        self.mp_label_text = "Multiplayer:"
        label_w, _ = self.font_btn.size(self.mp_label_text)
        mp_btn_w = 150
        mp_btn_h = 46
        label_gap = 12
        btn_gap = 12
        total_w = label_w + label_gap + mp_btn_w * 2 + btn_gap
        start_x = max(40, self.w // 2 - total_w // 2)
        center_y = self.mp_block_y + self.mp_block_h // 2
        self.mp_label_pos = (start_x, center_y)
        host_rect = pygame.Rect(
            start_x + label_w + label_gap,
            center_y - mp_btn_h // 2,
            mp_btn_w,
            mp_btn_h,
        )
        join_rect = pygame.Rect(
            host_rect.right + btn_gap,
            host_rect.y,
            mp_btn_w,
            mp_btn_h,
        )
        self.btn_host = Button(host_rect, "Host", self.font_btn)
        self.btn_join = Button(join_rect, "Join", self.font_btn)

        self.fade_alpha = 255
        self.fade_dir = -1

    def handle_event(self, event):
        if self.show_scores:
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                    self.show_scores = False
                    self.scroll_y = 0
                elif event.key in (pygame.K_UP, pygame.K_w):
                    self.scroll_y = min(self.scroll_y + 20, 0)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    self.scroll_y -= 20
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.show_scores = False
                self.scroll_y = 0
            return

        if event.type == pygame.MOUSEMOTION:
            for b in self.buttons:
                b.hover = b.hit(event.pos)
            self.btn_host.hover = self.btn_host.hit(event.pos)
            self.btn_join.hover = self.btn_join.hit(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons:
                if b.hit(event.pos):
                    if b.text == "Play":
                        self.manager.switch(MapCharacterSelect(self.manager))
                    elif b.text == "High Scores":
                        self.show_scores = True
                        self.scroll_y = 0
                    elif b.text == "Settings":
                        from settings_menu import SettingsMenu

                        self.manager.switch(SettingsMenu(self.manager))
                    elif b.text == "Exit":
                        pygame.event.post(pygame.event.Event(pygame.QUIT))
            if self.btn_host.hit(event.pos):
                self.manager.push(MultiplayerHostScene(self.manager))
            elif self.btn_join.hit(event.pos):
                self.manager.push(MultiplayerJoinScene(self.manager))
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self.manager.switch(MapCharacterSelect(self.manager))
            elif event.key == pygame.K_ESCAPE:
                pygame.event.post(pygame.event.Event(pygame.QUIT))

    def update(self, dt):
        self.fade_alpha += self.fade_dir * FADE_SPEED * dt
        self.fade_alpha = max(0, min(255, self.fade_alpha))

    def draw(self):
        if self.show_scores:
            draw_highscores(self.screen, self.font_btn, "tutor_forest", self.scroll_y)
            return

        self.screen.fill((18, 20, 29))
        title = self.font_title.render("Retro Royale", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.manager.size[0] // 2, 40)))

        for b in self.buttons:
            b.draw(self.screen)

        mp_label = self.font_btn.render(self.mp_label_text, True, (220, 220, 235))
        label_rect = mp_label.get_rect(midleft=self.mp_label_pos)
        self.screen.blit(mp_label, label_rect)
        self.btn_host.draw(self.screen)
        self.btn_join.draw(self.screen)

        hint = pygame.font.SysFont(None, 22).render(
            "Enter to Play • Esc to Quit", True, (200, 200, 210)
        )
        self.screen.blit(hint, hint.get_rect(center=(self.w // 2, self.h - 40)))

        if self.fade_alpha > 0:
            fade = pygame.Surface((self.w, self.h))
            fade.fill((0, 0, 0))
            fade.set_alpha(int(self.fade_alpha))
            self.screen.blit(fade, (0, 0))


# -----------------------------------------------------------------
# Map + Character Select Scene
# -----------------------------------------------------------------
class MapCharacterSelect(Scene):
    def __init__(self, manager):
        super().__init__(manager)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.font_title = pygame.font.SysFont(None, 60)
        self.font = pygame.font.SysFont(None, 28)
        self.small = pygame.font.SysFont(None, 22)

        root = Path(resource_path())
        maps_path = root / "maps"
        chars_path = root / "characters"

        self.maps = sorted([d.name for d in maps_path.iterdir() if d.is_dir()])
        self.characters = sorted([d.name for d in chars_path.iterdir() if d.is_dir()])
        self.map_meta = self._load_map_meta(maps_path)
        self.char_labels = {name: name.replace("_", " ").title() for name in self.characters}
        self.mode_idx = 0

        if not self.maps:
            self.maps = ["(no maps found)"]
        if not self.characters:
            self.characters = ["(no characters found)"]

        self.map_idx = 0
        self.char_idx = 0
        self.map_previews = {}
        self.char_anim = []
        self.char_frame = 0
        self.char_timer = 0.0
        self._load_map_preview()
        self._load_character_preview()

        self.btn_start = Button(
            (self.w // 2 - 120, self.h - 120, 240, 60), "Start Game", self.font
        )

    def handle_event(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.btn_start.hover = self.btn_start.hit(event.pos)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_start.hit(event.pos):
                selection = {
                    "map_name": self.maps[self.map_idx],
                    "char_name": self.characters[self.char_idx],
                    "mode": self._current_mode(),
                }
                from game_context import GameContext

                context = GameContext()
                context.flags["mode"] = selection["mode"]
                self.manager.switch(
                    ArenaScene(self.manager, selection, context)
                )
            elif self._mode_rect().collidepoint(event.pos) and len(self._current_modes()) > 1:
                self.mode_idx = (self.mode_idx + 1) % len(self._current_modes())
            elif self._map_card_rect().collidepoint(event.pos):
                self.map_idx = (self.map_idx + 1) % len(self.maps)
                self._load_map_preview()
                self.mode_idx = 0
            elif self._char_card_rect().collidepoint(event.pos):
                self.char_idx = (self.char_idx + 1) % len(self.characters)
                self._load_character_preview()

        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_LEFT, pygame.K_a):
                self.char_idx = (self.char_idx - 1) % len(self.characters)
                self._load_character_preview()
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self.char_idx = (self.char_idx + 1) % len(self.characters)
                self._load_character_preview()
            elif event.key in (pygame.K_UP, pygame.K_w):
                self.map_idx = (self.map_idx - 1) % len(self.maps)
                self._load_map_preview()
                self.mode_idx = 0
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.map_idx = (self.map_idx + 1) % len(self.maps)
                self._load_map_preview()
                self.mode_idx = 0
            elif event.key in (pygame.K_m, pygame.K_t):
                if len(self._current_modes()) > 1:
                    self.mode_idx = (self.mode_idx + 1) % len(self._current_modes())
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                selection = {
                    "map_name": self.maps[self.map_idx],
                    "char_name": self.characters[self.char_idx],
                    "mode": self._current_mode(),
                }
                from game_context import GameContext

                context = GameContext()
                context.flags["mode"] = selection["mode"]
                self.manager.switch(
                    ArenaScene(self.manager, selection, context)
                )
            elif event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self.manager.switch(MainMenu(self.manager))

    def update(self, dt):
        if self.char_anim:
            self.char_timer += dt
            if self.char_timer >= 0.15:
                self.char_timer = 0.0
                self.char_frame = (self.char_frame + 1) % len(self.char_anim)

    def _map_card_rect(self):
        card_w = self.w // 2 - 40
        return pygame.Rect(40, 120, card_w, self.h - 280)

    def _char_card_rect(self):
        card_w = self.w // 2 - 40
        return pygame.Rect(self.w // 2 + 20, 120, card_w, self.h - 280)

    def _mode_rect(self):
        card = self._map_card_rect()
        height = 40
        return pygame.Rect(card.x + 20, card.bottom - height - 30, card.width - 40, height)

    def _load_map_meta(self, maps_path):
        """Read friendly names and allowed modes from map profiles."""
        from sound_engine import load_map_profile

        meta = {}
        for d in maps_path.iterdir():
            if not d.is_dir():
                continue
            display = d.name.replace("_", " ").title()
            modes = ["arcade"]
            try:
                prof = load_map_profile(str(d / "map.json"))
            except Exception:
                prof = None
            if prof and hasattr(prof, "MAP_PROFILE"):
                display = prof.MAP_PROFILE.get("name", display)
            # default modes
            if prof and hasattr(prof, "get_available_modes"):
                try:
                    modes = prof.get_available_modes() or modes
                except Exception:
                    pass
            else:
                # special-case known maps
                if d.name.lower() == "tutor_forest":
                    modes = ["tournament"]
                elif d.name.lower() == "test_arena":
                    modes = ["sandbox", "tournament"]
                else:
                    modes = ["arcade"]
            meta[d.name] = {"display": display, "modes": modes}
        return meta

    def _current_modes(self):
        name = self.maps[self.map_idx]
        return self.map_meta.get(name, {}).get("modes", ["arcade"])

    def _current_mode(self):
        modes = self._current_modes()
        if not modes:
            return "arcade"
        return modes[self.mode_idx % len(modes)]

    def _build_map_preview(self, map_name):
        ROOT = Path(resource_path())
        MAP_DIR = ROOT / "maps" / map_name
        map_json = MAP_DIR / "map.json"
        sheet_path = MAP_DIR / "spritesheet.png"

        if not map_json.exists() or not sheet_path.exists():
            return None

        with open(map_json, "r") as f:
            data = json.load(f)

        tile = data.get("tileSize")
        if not tile:
            return None

        try:
            sheet = pygame.image.load(sheet_path).convert_alpha()
        except Exception:
            return None

        cols = sheet.get_width() // tile
        map_w_px = data.get("mapWidth", 0) * tile
        map_h_px = data.get("mapHeight", 0) * tile
        if map_w_px <= 0 or map_h_px <= 0:
            return None

        surf = pygame.Surface((map_w_px, map_h_px), pygame.SRCALPHA)

        for layer in data.get("layers", []):
            if "tiles" in layer:
                for t in layer["tiles"]:
                    tid = int(t.get("id", -1))
                    if tid < 0:
                        continue
                    gid = tid
                    tx = (gid % cols) * tile
                    ty = (gid // cols) * tile
                    x = int(t.get("x", 0)) * tile
                    y = int(t.get("y", 0)) * tile
                    tile_img = pygame.Surface((tile, tile), pygame.SRCALPHA)
                    tile_img.blit(sheet, (0, 0), (tx, ty, tile, tile))
                    surf.blit(tile_img, (x, y))
                continue

            if "data" not in layer:
                continue
            width = layer.get("width", data.get("mapWidth", 0)) or 0
            if width <= 0:
                continue
            for i, tid in enumerate(layer["data"]):
                if tid <= 0:
                    continue
                gid = tid - 1
                tx = (gid % cols) * tile
                ty = (gid // cols) * tile
                x = (i % width) * tile
                y = (i // width) * tile
                tile_img = pygame.Surface((tile, tile), pygame.SRCALPHA)
                tile_img.blit(sheet, (0, 0), (tx, ty, tile, tile))
                surf.blit(tile_img, (x, y))

        max_w, max_h = 260, 180
        scale = min(max_w / map_w_px, max_h / map_h_px)
        if scale <= 0:
            return None
        w, h = int(map_w_px * scale), int(map_h_px * scale)
        return pygame.transform.scale(surf, (max(1, w), max(1, h)))

    def _load_map_preview(self):
        if not self.maps:
            return
        map_name = self.maps[self.map_idx]
        if map_name.startswith("("):
            self.map_previews[map_name] = None
            return
        if map_name not in self.map_previews:
            self.map_previews[map_name] = self._build_map_preview(map_name)

    def _load_character_preview(self):
        self.char_anim = []
        self.char_frame = 0
        self.char_timer = 0.0
        if not self.characters:
            return
        char_name = self.characters[self.char_idx]
        if char_name.startswith("("):
            return
        ROOT = Path(resource_path())
        char_dir = ROOT / "characters" / char_name
        idle_path = char_dir / "idle.png"
        if not idle_path.exists():
            print(f"[Menu] Missing idle.png for {char_name}")
            return
        try:
            sheet = pygame.image.load(idle_path).convert_alpha()
        except Exception as exc:
            print(f"[Menu] Failed to load idle.png for {char_name}: {exc}")
            return

        cols = 4
        rows = 4
        if sheet.get_width() % cols != 0:
            cols = max(1, sheet.get_width() // 32)
        if sheet.get_height() % rows != 0:
            rows = max(1, sheet.get_height() // 32)
        frame_w = sheet.get_width() // max(1, cols)
        frame_h = sheet.get_height() // max(1, rows)
        row = 0
        frames = []
        for c in range(cols):
            rect = (c * frame_w, row * frame_h, frame_w, frame_h)
            frame = pygame.Surface((frame_w, frame_h), pygame.SRCALPHA)
            frame.blit(sheet, (0, 0), rect)
            frames.append(frame)
        self.char_anim = frames

    def draw(self):
        self.screen.fill((18, 20, 29))
        title = self.font_title.render("Select Map & Character", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.w // 2, 60)))

        map_rect = self._map_card_rect()
        pygame.draw.rect(self.screen, (35, 42, 58), map_rect, border_radius=14)
        pygame.draw.rect(self.screen, (180, 180, 220), map_rect, 2, border_radius=14)
        mname = self.font.render(
            f"Map: {self.map_meta.get(self.maps[self.map_idx], {}).get('display', self.maps[self.map_idx])}",
            True,
            (230, 230, 240),
        )
        self.screen.blit(mname, (map_rect.x + 20, map_rect.y + 20))

        map_key = self.maps[self.map_idx]
        preview = self.map_previews.get(map_key)
        if preview:
            pw, ph = preview.get_size()
            px = map_rect.centerx - pw // 2
            py = map_rect.centery - ph // 2 + 20
            self.screen.blit(preview, (px, py))

        char_rect = self._char_card_rect()
        pygame.draw.rect(self.screen, (35, 42, 58), char_rect, border_radius=14)
        pygame.draw.rect(self.screen, (180, 180, 220), char_rect, 2, border_radius=14)
        cname = self.font.render(
            f"Character: {self.char_labels.get(self.characters[self.char_idx], self.characters[self.char_idx])}",
            True,
            (230, 230, 240),
        )
        self.screen.blit(cname, (char_rect.x + 20, char_rect.y + 20))

        if self.char_anim:
            frame = self.char_anim[self.char_frame]
            scale = 3
            preview = pygame.transform.scale(
                frame, (frame.get_width() * scale, frame.get_height() * scale)
            )
            px = char_rect.centerx - preview.get_width() // 2
            py = char_rect.centery - preview.get_height() // 2 + 20
            self.screen.blit(preview, (px, py))

        self.btn_start.draw(self.screen)
        # Mode row (if multiple options)
        modes = self._current_modes()
        mode_label = self.font.render(
            f"Mode: {self._current_mode().title()}", True, (220, 220, 235)
        )
        mode_rect = self._mode_rect()
        pygame.draw.rect(self.screen, (30, 32, 45), mode_rect, border_radius=10)
        pygame.draw.rect(self.screen, (140, 140, 180), mode_rect, 1, border_radius=10)
        self.screen.blit(mode_label, mode_label.get_rect(center=mode_rect.center))
        if len(modes) > 1:
            hint = self.small.render("Click or press M to change mode", True, (180, 180, 200))
            hint_rect = hint.get_rect(midtop=(mode_rect.centerx, mode_rect.bottom + 6))
            self.screen.blit(hint, hint_rect)

        esc = self.small.render("Esc to return", True, (200, 200, 210))
        self.screen.blit(
            esc, esc.get_rect(center=(self.w // 2, self.btn_start.rect.bottom + 30))
        )


# -----------------------------------------------------------------
# Multiplayer Placeholder Scenes
# -----------------------------------------------------------------
class MultiplayerHostScene(Scene):
    """Host lobby scene that spins up a lightweight asyncio server."""

    def __init__(self, manager):
        super().__init__(manager)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.font_title = pygame.font.SysFont(None, 60)
        self.font = pygame.font.SysFont(None, 28)
        self.small = pygame.font.SysFont(None, 22)
        self.btn_back = Button((40, self.h - 90, 160, 48), "Back", self.font)
        gap = 12
        y = self.h - 90
        # Bottom row: Ready/Start only to reduce clutter
        self.btn_launch = Button((self.w - 160 - 20, y, 160, 48), "Start Match", self.font)
        self.btn_toggle_ready = Button((self.btn_launch.rect.x - gap - 140, y, 140, 48), "Ready", self.font)
        # Map/Mode/Skin sit inside the match setup panel; positions set in draw/_position_host_buttons
        self.btn_map = Button((0, 0, 200, 40), "Map", self.font)
        self.btn_mode = Button((0, 0, 200, 40), "Mode", self.font)
        self.btn_char = Button((0, 0, 200, 40), "Character", self.font)
        self.server = LobbyServer()
        self.server_running = self.server.start()
        self.server_logs = []
        self.client_logs = []
        self.client_state = {}
        self.local_name = "Host Player"
        self.local_client = None
        self.roster_scroll = 0
        self.preserve_connections = False
        self.maps = self._load_maps()
        self.characters = self._load_characters()
        self.char_idx = 0
        self.map_idx = 0
        if self.server_running:
            self.local_client = LobbyClient()
            if not self.local_client.connect(self.server.host, self.server.port, self.local_name):
                self.client_logs.append("Failed to register host player.")
        else:
            self.server_logs.append("Failed to start lobby server.")

    def handle_event(self, event):
        self._position_host_buttons()
        if event.type == pygame.MOUSEMOTION:
            self.btn_back.hover = self.btn_back.hit(event.pos)
            self.btn_launch.hover = self.btn_launch.hit(event.pos)
            self.btn_toggle_ready.hover = self.btn_toggle_ready.hit(event.pos)
            self.btn_mode.hover = self.btn_mode.hit(event.pos)
            self.btn_map.hover = self.btn_map.hit(event.pos)
            self.btn_char.hover = self.btn_char.hit(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_back.hit(event.pos):
                self._shutdown()
                self.manager.pop()
            elif self.btn_launch.hit(event.pos):
                if self._all_ready():
                    self._start_match()
                else:
                    self.client_logs.append("Cannot start: not all players ready.")
            elif self.btn_toggle_ready.hit(event.pos):
                self._toggle_ready()
            elif self.btn_mode.hit(event.pos):
                self._cycle_mode()
            elif self.btn_map.hit(event.pos):
                self._cycle_map()
            elif self.btn_char.hit(event.pos):
                self._cycle_char()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            players = self.client_state.get("players", []) if self.client_state else []
            total_slots = max(16, len(players))
            visible_slots = 6
            max_scroll = max(0, total_slots - visible_slots)
            if event.button == 4:
                self.roster_scroll = max(0, self.roster_scroll - 1)
            else:
                self.roster_scroll = min(max_scroll, self.roster_scroll + 1)
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self._shutdown()
                self.manager.pop()

    def update(self, dt):
        if self.server:
            self.server_logs.extend(self.server.pop_events())
            self.server_logs = self.server_logs[-4:]
        if self.local_client:
            self.client_logs.extend(self.local_client.pop_events())
            self.client_logs = self.client_logs[-4:]
            self.client_state = self.local_client.get_state()
            # sync local map index to current state map name
            map_name = (self.client_state or {}).get("map_name")
            if map_name and map_name in self.maps:
                self.map_idx = self.maps.index(map_name)
            # sync character to server state
            host_char = self._current_char_from_state()
            if host_char and host_char in self.characters:
                self.char_idx = self.characters.index(host_char)
        elif self.server:
            self.client_state = self.server.snapshot_state()
        players = self.client_state.get("players", []) if self.client_state else []
        total_slots = max(16, len(players))
        visible_slots = 6
        max_scroll = max(0, total_slots - visible_slots)
        self.roster_scroll = max(0, min(self.roster_scroll, max_scroll))
        if self.local_client and self.local_client.last_match:
            match = self.local_client.last_match
            self.local_client.last_match = None
            self._enter_match(match)

    def _position_host_buttons(self):
        # place map/mode buttons at top-right of match setup panel
        layout = pygame.Rect(60, 100, self.w - 120, self.h - 180)
        left = pygame.Rect(layout.x + 24, layout.y + 24, int(layout.width * 0.45), layout.height - 48)
        top_y = left.y + 40
        inset_x = left.x + 16
        width = left.width - 32
        height = 36
        self.btn_map.rect = pygame.Rect(inset_x, top_y, width, height)
        self.btn_mode.rect = pygame.Rect(inset_x, self.btn_map.rect.bottom + 10, width, height)
        self.btn_char.rect = pygame.Rect(inset_x, self.btn_mode.rect.bottom + 10, width, height)

    def _shutdown(self):
        if self.local_client:
            self.local_client.disconnect()
            self.local_client = None
        if self.server:
            self.server.stop()
            self.server = None
            self.server_running = False

    def _toggle_ready(self):
        if not self.local_client:
            return
        current = self._is_host_ready()
        self.local_client.send_ready(not current)

    def _is_host_ready(self):
        players = self.client_state.get("players", []) if self.client_state else []
        pid = self.local_client.player_id if self.local_client else None
        for p in players:
            if p.get("player_id") == pid:
                return bool(p.get("ready"))
        return False

    def _all_ready(self):
        players = self.client_state.get("players", []) if self.client_state else []
        if not players:
            return False
        return all(p.get("ready") for p in players)

    def _load_maps(self):
        root = Path(resource_path("maps"))
        if not root.exists():
            return ["test_arena"]
        names = [d.name for d in root.iterdir() if d.is_dir()]
        return sorted(names) if names else ["test_arena"]

    def _cycle_map(self):
        if not self.maps:
            return
        self.map_idx = (self.map_idx + 1) % len(self.maps)
        name = self.maps[self.map_idx]
        if self.local_client:
            self.local_client.send_set_map(name)
        self.client_logs.append(f"Map set to {name}.")

    def _start_match(self):
        if not self.local_client:
            return
        state = self.client_state or {}
        self.local_client.send_start_match(
            map_name=state.get("map_name"),
            mode=state.get("mode"),
            seed=None,
            allow_npc=False,
        )
        self.client_logs.append("Start match requested.")

    def _cycle_mode(self):
        # For now, just toggle tournament/sandbox to keep simple.
        state = self.client_state or {}
        current = (state.get("mode") or "tournament").lower()
        next_mode = "sandbox" if current == "tournament" else "tournament"
        if self.local_client:
            self.local_client.send_set_mode(next_mode)
        self.client_logs.append(f"Mode set to {next_mode}.")

    def _cycle_char(self):
        if not self.characters:
            return
        self.char_idx = (self.char_idx + 1) % len(self.characters)
        name = self.characters[self.char_idx]
        if self.local_client:
            self.local_client.send_set_char(name)
        self.client_logs.append(f"Character set to {name}.")

    def _current_char_from_state(self):
        players = self.client_state.get("players", []) if self.client_state else []
        pid = self.local_client.player_id if self.local_client else None
        for p in players:
            if p.get("player_id") == pid:
                return p.get("char_name")
        return None

    def _load_characters(self):
        root = Path(resource_path("characters"))
        if not root.exists():
            return ["classic"]
        names = [d.name for d in root.iterdir() if d.is_dir()]
        return sorted(names) if names else ["classic"]

    def _load_maps(self):
        root = Path(resource_path("maps"))
        if not root.exists():
            return ["test_arena"]
        names = [d.name for d in root.iterdir() if d.is_dir()]
        return sorted(names) if names else ["test_arena"]

    def _enter_match(self, match):
        if not match or not self.local_client:
            return
        # Keep lobby server/client alive so the match can continue to use them.
        self.preserve_connections = True
        self.manager.switch(MultiplayerArenaScene(self.manager, self.local_client, match, self.server))

    def __del__(self):
        if not getattr(self, "preserve_connections", False):
            self._shutdown()

    def draw(self):
        self.screen.fill((15, 18, 26))
        title = self.font_title.render("Host Multiplayer Lobby", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.w // 2, 50)))

        layout = pygame.Rect(60, 100, self.w - 120, self.h - 180)
        pygame.draw.rect(self.screen, (32, 38, 56), layout, border_radius=18)
        pygame.draw.rect(self.screen, (90, 100, 140), layout, 2, border_radius=18)

        left = pygame.Rect(layout.x + 24, layout.y + 24, int(layout.width * 0.45), layout.height - 48)
        right = pygame.Rect(
            left.right + 16, layout.y + 24, layout.right - left.right - 40, layout.height - 48
        )

        pygame.draw.rect(self.screen, (26, 30, 44), left, border_radius=12)
        pygame.draw.rect(self.screen, (45, 52, 74), left, 2, border_radius=12)
        pygame.draw.rect(self.screen, (26, 30, 44), right, border_radius=12)
        pygame.draw.rect(self.screen, (45, 52, 74), right, 2, border_radius=12)

        info_title = self.font.render("Match Setup", True, (230, 230, 240))
        self.screen.blit(info_title, (left.x + 16, left.y + 12))
        state = self.client_state or {}
        players = state.get("players", [])
        friendly_map = (state.get("map_name") or "(select)").replace("_", " ").title()
        # buttons placed relative to left panel
        self.btn_map.text = f"◀ {friendly_map} ▶"
        self.btn_mode.text = f"◀ {state.get('mode', 'tournament').title()} ▶"
        char_name = self._current_char_from_state() or (self.characters[self.char_idx] if self.characters else "classic")
        self.btn_char.text = f"◀ Skin: {char_name.replace('_', ' ').title()} ▶"
        self.btn_map.draw(self.screen)
        self.btn_mode.draw(self.screen)
        self.btn_char.draw(self.screen)
        details = [
            f"Endpoint: {self.server.host}:{self.server.port}" if self.server else "Endpoint: unavailable",
            f"Players: {len(players)} connected",
            f"Status: {'Listening' if self.server_running else 'Offline'}",
        ]
        base_y = self.btn_char.rect.bottom + 28
        for idx, line in enumerate(details):
            txt = self.small.render(line, True, (200, 205, 225))
            self.screen.blit(txt, (left.x + 18, base_y + idx * 24))
        status_line = ""
        combined_logs = (self.server_logs + self.client_logs)
        if combined_logs:
            status_line = combined_logs[-1]
        if status_line:
            st = self.small.render(status_line, True, (180, 185, 205))
            self.screen.blit(st, (left.x + 18, left.bottom - 32))

        roster_title = self.font.render("Lobby Slots", True, (230, 230, 240))
        self.screen.blit(roster_title, (right.x + 16, right.y + 12))
        slot_width = right.width - 32
        slot_height = 36
        visible_slots = 6
        roster = max(16, len(players))
        start_idx = self.roster_scroll
        end_idx = min(roster, start_idx + visible_slots)
        info_txt = self.small.render(
            f"Slots {start_idx + 1}-{end_idx} of {roster}", True, (180, 185, 205)
        )
        self.screen.blit(info_txt, (right.x + 16, right.y + 32))
        host_id = self.local_client.player_id if self.local_client else None
        for i in range(start_idx, end_idx):
            slot_i = i - start_idx
            slot_rect = pygame.Rect(
                right.x + 16, right.y + 52 + slot_i * (slot_height + 8), slot_width, slot_height
            )
            border_color = (70, 85, 120)
            fill_color = (33, 38, 55)
            label = f"Slot {i + 1}: waiting..."
            status = ""
            if i < len(players):
                p = players[i]
                ready = "Ready" if p.get("ready") else "Not ready"
                label = p.get("name", "Player")
                status = ready
                if p.get("player_id") == host_id:
                    border_color = (130, 200, 255)
                    fill_color = (40, 48, 70)
                    status = "You • " + ready
            pygame.draw.rect(self.screen, fill_color, slot_rect, border_radius=8)
            pygame.draw.rect(self.screen, border_color, slot_rect, 1, border_radius=8)
            txt = self.small.render(label, True, (185, 190, 210))
            self.screen.blit(txt, (slot_rect.x + 10, slot_rect.y + 6))
            if status:
                st = self.small.render(status, True, (150, 180, 200))
                self.screen.blit(st, (slot_rect.x + 10, slot_rect.y + 24))

        hint = self.small.render("Lobby server is running locally (preview only).", True, (180, 180, 200))
        self.screen.blit(hint, hint.get_rect(center=(self.w // 2, self.h - 40)))

        self.btn_back.draw(self.screen)
        self.btn_toggle_ready.text = "Unready" if self._is_host_ready() else "Ready"
        self.btn_toggle_ready.draw(self.screen)
        self.btn_launch.draw(self.screen)


class MultiplayerJoinScene(Scene):
    """Client lobby scene for connecting to a host."""

    def __init__(self, manager):
        super().__init__(manager)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.font_title = pygame.font.SysFont(None, 60)
        self.font = pygame.font.SysFont(None, 28)
        self.small = pygame.font.SysFont(None, 22)
        self.btn_back = Button((40, self.h - 90, 160, 48), "Back", self.font)
        self.btn_connect = Button((self.w - 200, self.h - 90, 160, 48), "Connect", self.font)
        self.sample_ip = "127.0.0.1"
        self.sample_port = 8765
        self.sample_name = "GuestPlayer"
        self.client = None
        self.status_text = "Not connected"
        self.messages = []
        self.lobby_state = {}
        self.roster_scroll = 0
        self.active_field = 0
        self.characters = []
        self.char_idx = 0
        self.fields = [
            {"label": "Display Name", "value": self.sample_name, "key": "name", "editable": True},
            {"label": "Host IP", "value": self.sample_ip, "key": "ip", "editable": True},
            {"label": "Port", "value": str(self.sample_port), "key": "port", "editable": True},
        ]

    def handle_event(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.btn_back.hover = self.btn_back.hit(event.pos)
            self.btn_connect.hover = self.btn_connect.hit(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_back.hit(event.pos):
                self._disconnect()
                self.manager.pop()
            elif self.btn_connect.hit(event.pos):
                if self.client and self.client.connected:
                    self._disconnect()
                else:
                    self._attempt_connect()
            else:
                layout = pygame.Rect(140, 90, self.w - 280, self.h - 170)
                field_height = 62
                gap = 12
                for idx in range(len(self.fields)):
                    top = layout.y + 24 + idx * (field_height + gap)
                    field_rect = pygame.Rect(layout.x + 20, top, layout.width - 40, field_height)
                    if field_rect.collidepoint(event.pos):
                        self.active_field = idx
                        break
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._disconnect()
                self.manager.pop()
            elif event.key == pygame.K_BACKSPACE:
                self._edit_field(backspace=True)
            elif event.key == pygame.K_RETURN:
                self._attempt_connect()
            else:
                self._edit_field(char=event.unicode)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            players = self.lobby_state.get("players", []) if self.lobby_state else []
            total_slots = max(12, len(players))
            visible_slots = 5
            max_scroll = max(0, total_slots - visible_slots)
            if event.button == 4:
                self.roster_scroll = max(0, self.roster_scroll - 1)
            else:
                self.roster_scroll = min(max_scroll, self.roster_scroll + 1)

    def _edit_field(self, char=None, backspace=False):
        if self.active_field is None or self.active_field >= len(self.fields):
            return
        field = self.fields[self.active_field]
        if not field.get("editable"):
            return
        text = field.get("value", "")
        if backspace:
            text = text[:-1]
        elif char:
            if not char or not char.isprintable():
                return
            if field["key"] == "port" and not char.isdigit():
                return
            if field["key"] == "ip" and not (char.isdigit() or char == "."):
                return
            text += char
        field["value"] = text[:32]

    def _attempt_connect(self):
        self._disconnect()
        self.client = LobbyClient()
        self.status_text = "Connecting..."
        name = self.fields[0]["value"] or "Player"
        ip = self.fields[1]["value"] or "127.0.0.1"
        try:
            port = int(self.fields[2]["value"]) if self.fields[2]["value"] else self.sample_port
        except ValueError:
            port = self.sample_port
        connected = self.client.connect(ip, port, name)
        if connected:
            self.status_text = "Connected"
            self.manager.switch(MultiplayerWaitingScene(self.manager, self.client, name, ip, port))
            self.client = None
        else:
            self.status_text = "Connection pending..."

    def _disconnect(self):
        if self.client:
            self.client.disconnect()
            self.client = None
        self.status_text = "Not connected"
        self.lobby_state = {}
        self.messages = []
        self.btn_connect.text = "Connect"
        self.char_idx = 0

    def _sync_character_from_state(self):
        """No-op: character selection handled in lobby, not on join form."""
        return

    def update(self, dt):
        if self.client:
            for msg in self.client.pop_events():
                self.messages.append(msg)
                if "Failed" in msg:
                    self.status_text = "Failed to connect"
                elif "Disconnected" in msg:
                    self.status_text = "Disconnected"
                elif "Joined" in msg:
                    self.status_text = "In lobby"
                elif "Connected" in msg:
                    self.status_text = "Connected"
            self.messages = self.messages[-4:]
            self.lobby_state = self.client.get_state()
            self.btn_connect.text = "Disconnect" if self.client.connected else "Connect"
            self._sync_character_from_state()
        else:
            self.btn_connect.text = "Connect"
        self.roster_scroll = 0
        self._sync_character_from_state()

    def draw(self):
        self.screen.fill((12, 15, 24))
        title = self.font_title.render("Join Multiplayer Lobby", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.w // 2, 50)))

        layout = pygame.Rect(140, 90, self.w - 280, self.h - 170)
        pygame.draw.rect(self.screen, (30, 34, 48), layout, border_radius=18)
        pygame.draw.rect(self.screen, (90, 100, 140), layout, 2, border_radius=18)

        labels = [(f["label"], f["value"]) for f in self.fields]
        field_height = 62
        gap = 12
        for idx, (label, value) in enumerate(labels):
            top = layout.y + 24 + idx * (field_height + gap)
            field_rect = pygame.Rect(layout.x + 20, top, layout.width - 40, field_height)
            is_active = idx == self.active_field
            border = (130, 200, 255) if is_active else (72, 84, 120)
            pygame.draw.rect(self.screen, (40, 46, 64), field_rect, border_radius=10)
            pygame.draw.rect(self.screen, border, field_rect, 1, border_radius=10)
            label_txt = self.small.render(label, True, (190, 195, 210))
            self.screen.blit(label_txt, (field_rect.x + 12, field_rect.y + 8))
            txt = self.font.render(value, True, (230, 230, 240))
            self.screen.blit(txt, txt.get_rect(midleft=(field_rect.x + 12, field_rect.y + field_rect.height // 2 + 2)))

        status_y = layout.y + 24 + len(labels) * (field_height + gap) + 6
        status_rect = pygame.Rect(layout.x + 20, status_y, layout.width - 40, 40)
        pygame.draw.rect(self.screen, (35, 40, 58), status_rect, border_radius=10)
        pygame.draw.rect(self.screen, (80, 90, 130), status_rect, 1, border_radius=10)
        status_txt = self.small.render(f"Status: {self.status_text}", True, (210, 215, 230))
        self.screen.blit(status_txt, status_txt.get_rect(midleft=(status_rect.x + 12, status_rect.centery)))

        log_y = status_rect.bottom + 16
        for idx, line in enumerate(self.messages[-3:]):
            txt = self.small.render(line, True, (165, 170, 190))
            self.screen.blit(txt, (layout.x + 24, log_y + idx * 18))

        self.btn_back.draw(self.screen)
        self.btn_connect.draw(self.screen)

    def __del__(self):
        if not getattr(self, "in_match", False):
            self._disconnect()


class MultiplayerWaitingScene(Scene):
    """Simple waiting room showing connected players for join clients."""

    def __init__(self, manager, client: LobbyClient, name: str, host: str, port: int):
        super().__init__(manager)
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.font_title = pygame.font.SysFont(None, 60)
        self.font = pygame.font.SysFont(None, 28)
        self.small = pygame.font.SysFont(None, 22)
        self.btn_back = Button((40, self.h - 90, 160, 48), "Leave", self.font)
        self.btn_ready = Button((self.w - 200, self.h - 90, 160, 48), "Ready", self.font)
        self.client = client
        self.name = name
        self.host = host
        self.port = port
        self.messages = []
        self.state = {}
        self.roster_scroll = 0
        self.in_match = False
        self.characters = self._load_characters()
        self.char_idx = 0

    def handle_event(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.btn_back.hover = self.btn_back.hit(event.pos)
            self.btn_ready.hover = self.btn_ready.hit(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_back.hit(event.pos):
                self._exit()
            elif self.btn_ready.hit(event.pos):
                self._toggle_ready()
            else:
                char_rect = pygame.Rect(self.btn_ready.rect.x - 200, self.btn_ready.rect.y, 180, self.btn_ready.rect.height)
                if char_rect.collidepoint(event.pos):
                    self._cycle_char()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            players = self.state.get("players", []) if self.state else []
            total_slots = max(12, len(players))
            visible = 7
            max_scroll = max(0, total_slots - visible)
            if event.button == 4:
                self.roster_scroll = max(0, self.roster_scroll - 1)
            else:
                self.roster_scroll = min(max_scroll, self.roster_scroll + 1)
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self._exit()

    def update(self, dt):
        if self.client:
            self.messages.extend(self.client.pop_events())
            self.messages = self.messages[-3:]
            self.state = self.client.get_state()
            self._sync_character_from_state()
        players = self.state.get("players", []) if self.state else []
        total_slots = max(12, len(players))
        visible = 7
        max_scroll = max(0, total_slots - visible)
        self.roster_scroll = max(0, min(self.roster_scroll, max_scroll))
        if self.client and self.client.last_match:
            match = self.client.last_match
            self.client.last_match = None
            self._enter_match(match)

    def _exit(self):
        if self.client:
            self.client.disconnect()
        self.manager.switch(MainMenu(self.manager))

    def _toggle_ready(self):
        if not self.client:
            return
        current = self._is_ready()
        self.client.send_ready(not current)
        self._send_character()

    def _send_character(self):
        if not self.client or not self.characters:
            return
        self.client.send_set_char(self.characters[self.char_idx])

    def _cycle_char(self):
        if not self.characters:
            return
        self.char_idx = (self.char_idx + 1) % len(self.characters)
        self._send_character()

    def _sync_character_from_state(self):
        char = self._current_char_from_state()
        if char and char in self.characters:
            self.char_idx = self.characters.index(char)

    def _current_char_from_state(self):
        pid = getattr(self.client, "player_id", None)
        players = self.state.get("players", []) if self.state else []
        for p in players:
            if p.get("player_id") == pid:
                return p.get("char_name")
        return None

    def _load_characters(self):
        root = Path(resource_path("characters"))
        if not root.exists():
            return ["classic"]
        names = [d.name for d in root.iterdir() if d.is_dir()]
        return sorted(names) if names else ["classic"]

    def _enter_match(self, match):
        if not match or not self.client:
            return
        self.in_match = True
        self.manager.switch(MultiplayerArenaScene(self.manager, self.client, match))

    def _is_ready(self):
        pid = getattr(self.client, "player_id", None)
        if not pid:
            return False
        players = self.state.get("players", []) if self.state else []
        for p in players:
            if p.get("player_id") == pid:
                return bool(p.get("ready"))
        return False

    def draw(self):
        self.screen.fill((12, 15, 24))
        title = self.font_title.render("Lobby Waiting Room", True, (255, 235, 140))
        self.screen.blit(title, title.get_rect(center=(self.w // 2, 50)))

        panel = pygame.Rect(120, 90, self.w - 240, self.h - 170)
        pygame.draw.rect(self.screen, (30, 34, 48), panel, border_radius=18)
        pygame.draw.rect(self.screen, (90, 100, 140), panel, 2, border_radius=18)

        players = self.state.get("players", []) if self.state else []
        info_lines = [
            f"Connected as: {self.name}",
            f"Host: {self.host}:{self.port}",
            f"Players: {len(players)}",
        ]
        server_meta = (self.state or {}).get("server_meta") or {}
        if server_meta:
            auto_start = bool(server_meta.get("auto_start", False))
            min_players = server_meta.get("min_players") or "?"
            ready_required = server_meta.get("ready_required", True)
            start_delay = int(round(server_meta.get("start_delay") or 0))
            auto_in = server_meta.get("auto_start_in")
            lobby_locked = bool(server_meta.get("lobby_locked", False))
            if auto_start:
                rule = f">={min_players} ready" if ready_required else f">={min_players} players"
                info_lines.append("Auto-start: On")
                info_lines.append(f"Rule: {rule}, {start_delay}s delay")
                countdown = f"{auto_in}s" if auto_in is not None else "Waiting"
                info_lines.append(f"Countdown: {countdown}")
            else:
                info_lines.append("Auto-start: Off")
            info_lines.append(f"Lobby: {'Locked' if lobby_locked else 'Open'}")
        for idx, line in enumerate(info_lines):
            txt = self.small.render(line, True, (200, 205, 225))
            self.screen.blit(txt, (panel.x + 24, panel.y + 24 + idx * 22))

        extra_lines = max(0, len(info_lines) - 3)
        roster_height = panel.height - 190 - extra_lines * 22
        roster_height = max(120, roster_height)
        roster_rect = pygame.Rect(
            panel.x + 20,
            panel.y + 90 + extra_lines * 22,
            panel.width - 40,
            roster_height,
        )
        pygame.draw.rect(self.screen, (26, 30, 44), roster_rect, border_radius=10)
        pygame.draw.rect(self.screen, (65, 78, 112), roster_rect, 1, border_radius=10)
        total_slots = max(12, len(players))
        visible = min(7, max(1, (roster_rect.height - 32) // 32))
        start_idx = self.roster_scroll
        end_idx = min(total_slots, start_idx + visible)
        title_txt = self.small.render(f"Slots {start_idx + 1}-{end_idx} / {total_slots}", True, (200, 205, 225))
        self.screen.blit(title_txt, (roster_rect.x + 12, roster_rect.y + 8))
        slot_height = 30
        for idx in range(start_idx, end_idx):
            row_idx = idx - start_idx
            row = pygame.Rect(
                roster_rect.x + 12,
                roster_rect.y + 26 + row_idx * (slot_height + 4),
                roster_rect.width - 24,
                slot_height,
            )
            pygame.draw.rect(self.screen, (33, 38, 55), row, border_radius=6)
            label = f"Slot {idx + 1}: waiting..."
            if idx < len(players):
                p = players[idx]
                ready = "Ready" if p.get("ready") else "Not ready"
                label = f"{p.get('name', 'Player')} • {ready}"
            txt = self.small.render(label, True, (185, 190, 210))
            self.screen.blit(txt, (row.x + 10, row.y + 8))

        # Activity footer below roster to avoid overlap
        footer_rect = pygame.Rect(panel.x + 20, roster_rect.bottom + 8, panel.width - 40, 40)
        pygame.draw.rect(self.screen, (33, 38, 55), footer_rect, border_radius=8)
        pygame.draw.rect(self.screen, (70, 78, 110), footer_rect, 1, border_radius=8)
        for idx, line in enumerate(self.messages[-2:]):
            txt = self.small.render(line, True, (165, 170, 190))
            self.screen.blit(txt, (footer_rect.x + 10, footer_rect.y + 8 + idx * 16))

        self.btn_back.draw(self.screen)
        self.btn_ready.text = "Unready" if self._is_ready() else "Ready"
        self.btn_ready.draw(self.screen)
        char_rect = pygame.Rect(self.btn_ready.rect.x - 200, self.btn_ready.rect.y, 180, self.btn_ready.rect.height)
        pygame.draw.rect(self.screen, (35, 40, 58), char_rect, border_radius=8)
        pygame.draw.rect(self.screen, (90, 100, 140), char_rect, 1, border_radius=8)
        current_char = self.characters[self.char_idx] if self.characters else "classic"
        txt = self.small.render(f"Skin: {current_char.replace('_', ' ').title()}", True, (210, 215, 230))
        self.screen.blit(txt, txt.get_rect(center=char_rect.center))
