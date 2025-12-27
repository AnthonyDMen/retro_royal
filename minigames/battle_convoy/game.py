# minigames/battle_convoy/game.py
import os, random, pygame, uuid
from scene_manager import Scene
from game_context import GameContext

TITLE = "Battle Convoy"
MINIGAME_ID = "battle_convoy"

# ===================== CONFIG =====================
DESIGN_W, DESIGN_H = 1024, 576
DESIGN_CELL = 32
DESIGN_LEFT_ORIGIN = (64, 96)
GRID_SIZE = 12
GRID_W = DESIGN_CELL * GRID_SIZE
SIDE_MARGIN = 64

PLACEMENT_LIMIT_S = 45
TURN_LIMIT_PLAYER_S = 15
TURN_LIMIT_ENEMY_S = 2

BACKGROUND_FILE = "background.png"
SPRITESHEET_FILE = "spritesheet.png"

# SPECIALS: normal shots are allowed by default.
# Flip to True only when you want to FORCE choosing a special every turn for testing.
FORCE_SPECIALS_FOR_PLAYER = False

# BLITZ animation pacing
BLITZ_STEP_MS = 140  # per-cell delay (miss/empty)
BLITZ_HIT_BONUS_MS = 160  # extra pause when a hit happens

# Radar blink timing (visual ping)
RADAR_BLINK_ON_MS = 220
RADAR_BLINK_OFF_MS = 140

# Banner timings (seconds)
BANNER_START_S = 2.0  # "ENGAGE!"
BANNER_HIT_S = 1.1  # non-sinking hit
BANNER_MINE_S = 1.4  # mine detonation
BANNER_DESTROYED_S = 1.9  # "Destroyed!" / "Unit Lost"
BANNER_ENDGAME_S = 2.2  # final Victory/Defeat banner

# Convoy polyominoes (1 cell = 1 hit)
VEHICLE_DEFS = [
    ("Heavy Tank", [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)], False),
    ("Artillery", [(0, 0), (1, 0), (2, 0), (1, -1), (1, -2)], False),
    ("Cargo Truck", [(0, 0), (0, 1), (0, 2), (1, 2)], False),
    ("Light Tank", [(0, 0), (1, 0), (2, 0)], False),
    ("Jeep", [(0, 0), (1, 0)], False),
    # Mines (2 after convoy)
    ("Landmine", [(0, 0)], True),
]

# Visual correction: rotate these sprites +90° CW
SPRITE_ROT_OFFSETS = {"Light Tank": 1, "Jeep": 1}


# ===================== GEOMETRY =====================
def rot_cell(pt, r):
    x, y = pt
    for _ in range(r % 4):
        x, y = (-y, x)
    return (x, y)


def normalize_to_origin(cells):
    minx = min(x for x, _ in cells)
    miny = min(y for _, y in cells)
    return [(x - minx, y - miny) for x, y in cells]


def bbox_size(cells):
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def cell_from_mouse(origin_px, cw, ch, pos):
    x, y = pos
    relx, rely = x - origin_px[0], y - origin_px[1]
    if relx < 0 or rely < 0:
        return None
    cx, cy = relx // cw, rely // ch
    if cx < 0 or cy < 0 or cx >= GRID_SIZE or cy >= GRID_SIZE:
        return None
    return (int(cx), int(cy))


def in_bounds(cells):
    for x, y in cells:
        if x < 0 or y < 0 or x >= GRID_SIZE or y >= GRID_SIZE:
            return False
    return True


# ===================== STATE =====================
class VehicleInstance:
    def __init__(self, name, base_cells, is_mine=False, origin=(0, 0), rot=0):
        self.name = name
        self.base_cells = list(base_cells)
        self.is_mine = is_mine
        self.origin = origin
        self.rot = rot
        self.cells = self.compute_cells()
        self.hits = set()
        self.sunk = False

    def compute_cells(self):
        rc = normalize_to_origin([rot_cell(c, self.rot) for c in self.base_cells])
        return [(x + self.origin[0], y + self.origin[1]) for x, y in rc]

    def set_pose(self, origin, rot):
        self.origin = origin
        self.rot = rot
        self.cells = self.compute_cells()

    def receive_hit(self, cell):
        if cell in self.cells:
            self.hits.add(cell)
            self.sunk = len(self.hits) == len(self.cells)
            return True
        return False


class Board:
    def __init__(self):
        self.vehicles = []  # includes mines
        self.occ = {}  # (x,y) -> vehicle idx
        self.hits = set()
        self.misses = set()

    def convoy_vehicles(self):
        return [v for v in self.vehicles if not v.is_mine]

    def can_place(self, inst: "VehicleInstance"):
        if not in_bounds(inst.cells):
            return False
        for c in inst.cells:
            if c in self.occ:
                return False
        return True

    def place(self, inst: "VehicleInstance"):
        assert self.can_place(inst)
        idx = len(self.vehicles)
        self.vehicles.append(inst)
        for c in inst.cells:
            self.occ[c] = idx

    def undo_last(self):
        if not self.vehicles:
            return
        self.vehicles.pop()
        self.occ.clear()
        for idx, v in enumerate(self.vehicles):
            for c in v.cells:
                self.occ[c] = idx

    def receive_fire(self, cell):
        """Returns ('repeat'|'miss'|'hit'|'mine', vehicle_or_None, sunk_now_bool)."""
        if cell in self.hits or cell in self.misses:
            return ("repeat", None, False)
        if cell in self.occ:
            v = self.vehicles[self.occ[cell]]
            v.receive_hit(cell)
            self.hits.add(cell)
            if v.is_mine:
                return ("mine", v, True)
            return ("hit", v, v.sunk)
        else:
            self.misses.add(cell)
            return ("miss", None, False)

    def all_sunk(self):
        conv = self.convoy_vehicles()
        return len(conv) > 0 and all(v.sunk for v in conv)


# ===================== AI =====================
class SimpleAI:
    def __init__(self):
        self.tried = set()
        self.special_ready = False

    def reset(self):
        self.tried.clear()
        self.special_ready = False

    def random_place_all(self, board: Board):
        # five convoy
        for name, base, is_mine in VEHICLE_DEFS[:5]:
            for _ in range(1000):
                rot = random.randint(0, 3)
                rc = normalize_to_origin([rot_cell(c, rot) for c in base])
                w, h = bbox_size(rc)
                ox = random.randint(0, GRID_SIZE - w)
                oy = random.randint(0, GRID_SIZE - h)
                inst = VehicleInstance(name, base, is_mine, origin=(ox, oy), rot=rot)
                if board.can_place(inst):
                    board.place(inst)
                    break
        # two mines
        for _ in range(2):
            base = VEHICLE_DEFS[5][1]
            for _ in range(1000):
                ox = random.randint(0, GRID_SIZE - 1)
                oy = random.randint(0, GRID_SIZE - 1)
                inst = VehicleInstance("Landmine", base, True, origin=(ox, oy), rot=0)
                if board.can_place(inst):
                    board.place(inst)
                    break
        return board

    def choose_fire(self):
        pool = [
            (x, y)
            for x in range(GRID_SIZE)
            for y in range(GRID_SIZE)
            if (x, y) not in self.tried
        ]
        c = random.choice(pool) if pool else (0, 0)
        self.tried.add(c)
        return c


# ===================== SPRITES =====================
def extract_components(sheet):
    mask = pygame.mask.from_surface(sheet)
    rects = [r for r in mask.get_bounding_rects() if r.w * r.h >= 200]
    rects.sort(key=lambda r: (r.x, r.y))
    comps = []
    for r in rects:
        s = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
        s.blit(sheet, (0, 0), r)
        comps.append((s, r))
    return comps


def merge_rects(a, b):
    x = min(a.x, b.x)
    y = min(a.y, b.y)
    w = max(a.right, b.right) - x
    h = max(a.bottom, b.bottom) - y
    return pygame.Rect(x, y, w, h)


def build_sprite_map(sheet):
    comps = extract_components(sheet)
    # smallest = mine (if present)
    mine_idx = (
        min(range(len(comps)), key=lambda i: comps[i][1].w * comps[i][1].h)
        if comps
        else None
    )
    mine_surf = comps[mine_idx][0] if mine_idx is not None else None
    if mine_idx is not None:
        comps.pop(mine_idx)

    if len(comps) >= 6:
        heavy, arty = comps[0], comps[1]
        merged_r = merge_rects(comps[2][1], comps[3][1])
        truck = pygame.Surface((merged_r.w, merged_r.h), pygame.SRCALPHA)
        truck.blit(
            sheet, (comps[2][1].x - merged_r.x, comps[2][1].y - merged_r.y), comps[2][1]
        )
        truck.blit(
            sheet, (comps[3][1].x - merged_r.x, comps[3][1].y - merged_r.y), comps[3][1]
        )
        light, jeep = comps[4], comps[5]
        ordered = [heavy[0], arty[0], truck, light[0], jeep[0]]
    else:
        ordered = [s for (s, _) in comps[:5]]
        while len(ordered) < 5:
            ordered.append(ordered[-1].copy())

    names = ["Heavy Tank", "Artillery", "Cargo Truck", "Light Tank", "Jeep"]
    mapping = {name: ordered[i] for i, name in enumerate(names)}
    if mine_surf is not None:
        mapping["Landmine"] = mine_surf
    else:
        m = pygame.Surface((20, 20), pygame.SRCALPHA)
        pygame.draw.circle(m, (200, 200, 60), (10, 10), 6)
        pygame.draw.circle(m, (40, 40, 40), (10, 10), 6, 2)
        mapping["Landmine"] = m
    return mapping


def blit_piece_cells(
    screen,
    sprite,
    name,
    left_origin,
    cw,
    ch,
    origin_cell,
    base_cells,
    rot_steps,
    alpha=None,
):
    rc = normalize_to_origin([rot_cell(c, rot_steps) for c in base_cells])
    w_cells, h_cells = bbox_size(rc)
    eff_rot = (rot_steps + SPRITE_ROT_OFFSETS.get(name, 0)) % 4
    spr = pygame.transform.rotate(sprite, -90 * eff_rot)
    spr = pygame.transform.smoothscale(
        spr, (max(1, w_cells * cw), max(1, h_cells * ch))
    )
    if alpha is not None:
        spr = spr.copy()
        spr.set_alpha(alpha)
    for cx, cy in rc:
        src = pygame.Rect(cx * cw, cy * ch, cw, ch)
        tile = spr.subsurface(src).copy()
        dx = left_origin[0] + (origin_cell[0] + cx) * cw
        dy = left_origin[1] + (origin_cell[1] + cy) * ch
        screen.blit(tile, (dx, dy))


def make_icon(sprite, height):
    sw, sh = sprite.get_size()
    s = height / max(1, sh)
    return pygame.transform.smoothscale(sprite, (max(1, int(sw * s)), height))


# ===================== HUD / UI =====================
def draw_status_center(screen, text, y, color=(210, 215, 220)):
    if not text:
        return
    font = pygame.font.SysFont(None, 22)
    img = font.render(text, True, color)
    rect = img.get_rect(center=(screen.get_width() // 2, y))
    screen.blit(img, rect)


def draw_timer_center(screen, seconds_left, y, label):
    font = pygame.font.SysFont(None, 24)
    s = max(0, int(seconds_left))
    txt = f"{label}: {s:02d}s"
    img = font.render(txt, True, (238, 220, 130))
    rect = img.get_rect(center=(screen.get_width() // 2, y))
    screen.blit(img, rect)


def draw_hits_misses(screen, origin, cw, ch, hits, misses):
    yoff = max(2, int(ch * 0.06))
    for cx, cy in misses:
        x = origin[0] + cx * cw
        y = origin[1] + cy * ch + yoff
        pygame.draw.line(
            screen, (160, 170, 180), (x + 6, y + 6), (x + cw - 6, y + ch - 6), 2
        )
        pygame.draw.line(
            screen, (160, 170, 180), (x + cw - 6, y + 6), (x + 6, y + ch - 6), 2
        )
    for cx, cy in hits:
        x = origin[0] + cx * cw
        y = origin[1] + cy * ch
        rect = pygame.Rect(x, y, cw, ch)
        center = (rect.centerx, rect.centery + yoff)
        pygame.draw.circle(screen, (210, 80, 80), center, min(cw, ch) // 3, 3)


def banner(surface_size, text, sub=None, color=(30, 30, 35), glow=(238, 220, 130)):
    W, H = surface_size
    panel = pygame.Surface((int(W * 0.7), 80), pygame.SRCALPHA)
    pygame.draw.rect(panel, (*color, 230), panel.get_rect(), border_radius=12)
    pygame.draw.rect(panel, (100, 110, 120), panel.get_rect(), 2, border_radius=12)
    font = pygame.font.SysFont(None, 36)
    img = font.render(text, True, glow)
    rect = img.get_rect(center=(panel.get_width() // 2, 30))
    panel.blit(img, rect)
    if sub:
        subf = pygame.font.SysFont(None, 24)
        srf = subf.render(sub, True, (210, 215, 220))
        srect = srf.get_rect(center=(panel.get_width() // 2, 58))
        panel.blit(srf, srect)
    return panel


def show_ephemeral_banner(screen, bg, text, sub, seconds):
    clock = pygame.time.Clock()
    elapsed = 0.0
    panel = banner(screen.get_size(), text, sub)
    while elapsed < seconds:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                return
        screen.blit(bg, (0, 0))
        rect = panel.get_rect(center=(screen.get_width() // 2, 76))
        screen.blit(panel, rect)
        pygame.display.flip()
        elapsed += clock.tick(60) / 1000.0


def draw_side_icon_columns(
    screen, sprite_map, names, enemy_board, player_board, sx, sy
):
    """Left = your losses, Right = enemy losses. Shows mini sprites; red X when destroyed."""
    SW, SH = screen.get_size()
    top = int(DESIGN_LEFT_ORIGIN[1] * sy)
    bottom = top + int(GRID_SIZE * DESIGN_CELL * sy)
    avail = bottom - top

    ICON_H = max(34, int(36 * sy))  # size of mini icons
    GAP = max(6, int(8 * sy))

    # Fallback placeholder if a name isn't in the spritesheet map
    placeholder = pygame.Surface((ICON_H, ICON_H), pygame.SRCALPHA)
    pygame.draw.rect(
        placeholder, (90, 90, 90, 140), placeholder.get_rect(), border_radius=4
    )
    pygame.draw.rect(
        placeholder, (130, 130, 130, 180), placeholder.get_rect(), 1, border_radius=4
    )

    def get_icon(name):
        surf = sprite_map.get(name)
        if surf is None:
            return placeholder
        return make_icon(surf, ICON_H)

    minis_enemy = [get_icon(n) for n in names]
    minis_player = [get_icon(n) for n in names]

    stack_h = len(names) * ICON_H + (len(names) - 1) * GAP
    start_y = top + (avail - stack_h) // 2

    # LEFT column: player's units (your losses)
    x_left = int(12 * sx)
    y = start_y
    player_dead = {v.name for v in player_board.convoy_vehicles() if v.sunk}
    for n, img in zip(names, minis_player):
        icon = img.copy()
        if n in player_dead:
            pygame.draw.line(
                icon, (210, 80, 80), (0, 0), (icon.get_width(), icon.get_height()), 3
            )
            pygame.draw.line(
                icon, (210, 80, 80), (icon.get_width(), 0), (0, icon.get_height()), 3
            )
        screen.blit(icon, (x_left, y))
        y += ICON_H + GAP

    # RIGHT column: enemy units (your eliminations)
    maxw = max(m.get_width() for m in minis_enemy) if minis_enemy else 0
    x_right = SW - int(12 * sx) - maxw
    y = start_y
    enemy_dead = {v.name for v in enemy_board.convoy_vehicles() if v.sunk}
    for n, img in zip(names, minis_enemy):
        icon = img.copy()
        if n in enemy_dead:
            pygame.draw.line(
                icon, (210, 80, 80), (0, 0), (icon.get_width(), icon.get_height()), 3
            )
            pygame.draw.line(
                icon, (210, 80, 80), (icon.get_width(), 0), (0, icon.get_height()), 3
            )
        screen.blit(icon, (x_right, y))
        y += ICON_H + GAP


# Special buttons (AIR / BLITZ / RADAR)
def draw_special_buttons(screen, center_y):
    font = pygame.font.SysFont(None, 22)
    labels = [("AIR", "air"), ("BLITZ", "blitz"), ("RADAR", "radar")]
    w = 110
    h = 30
    gap = 10
    total = len(labels) * w + (len(labels) - 1) * gap
    x = (screen.get_width() - total) // 2
    rects = []
    for text, key in labels:
        r = pygame.Rect(x, center_y - h // 2, w, h)
        pygame.draw.rect(screen, (35, 40, 48), r, border_radius=8)
        pygame.draw.rect(screen, (110, 120, 130), r, 1, border_radius=8)
        img = font.render(text, True, (238, 220, 130))
        screen.blit(img, img.get_rect(center=r.center))
        rects.append((r, key))
        x += w + gap
    return rects


def special_button_rects(screen, center_y):
    """Rect builder for hit-testing without drawing (prevents click-through)."""
    labels = [("AIR", "air"), ("BLITZ", "blitz"), ("RADAR", "radar")]
    w = 110
    h = 30
    gap = 10
    total = len(labels) * w + (len(labels) - 1) * gap
    x = (screen.get_width() - total) // 2
    rects = []
    for _, key in labels:
        rects.append((pygame.Rect(x, center_y - h // 2, w, h), key))
        x += w + gap
    return rects


# ===================== ENTRY =====================
class BattleConvoyScene(Scene):
    def __init__(self, manager, context=None, callback=None, difficulty=None, duel_id=None, participants=None, multiplayer_client=None, local_player_id=None, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.difficulty = difficulty or 1.0
        flags = getattr(context, "flags", {}) if context else {}
        self.duel_id = duel_id or flags.get("duel_id")
        self.participants = participants or flags.get("participants")
        self.local_id = local_player_id or flags.get("duel_local_id")
        self.opponent_id = None
        if self.participants and self.local_id:
            for pid in self.participants:
                if pid != self.local_id:
                    self.opponent_id = pid
                    break
        self.net_client = multiplayer_client or flags.get("multiplayer_client")
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.opponent_is_bot = bool(self.opponent_id and str(self.opponent_id).startswith("npc-"))
        # Deterministic starter: lowest participant ID takes first turn in PvP.
        ordered_parts = sorted(self.participants) if self.participants else []
        self.local_starts = (self.local_id == ordered_parts[0]) if ordered_parts else True
        self.battle_started = False

        here = os.path.dirname(__file__)
        bg_path = os.path.join(here, BACKGROUND_FILE)
        ss_path = os.path.join(here, SPRITESHEET_FILE)
        if not os.path.exists(bg_path):
            raise FileNotFoundError("Missing background.png")
        if not os.path.exists(ss_path):
            raise FileNotFoundError("Missing spritesheet.png")

        self.background = pygame.image.load(bg_path).convert_alpha()
        sheet = pygame.image.load(ss_path).convert_alpha()

        self.screen = manager.screen
        self.w, self.h = self.screen.get_size()
        self.bg_scaled = pygame.transform.smoothscale(self.background, (self.w, self.h))
        self.sx = self.w / DESIGN_W
        self.sy = self.h / DESIGN_H
        self.cw = max(1, int(DESIGN_CELL * self.sx))
        self.ch = max(1, int(DESIGN_CELL * self.sy))
        self.left_origin = (
            int(DESIGN_LEFT_ORIGIN[0] * self.sx),
            int(DESIGN_LEFT_ORIGIN[1] * self.sy),
        )
        self.right_origin = (
            int((DESIGN_W - SIDE_MARGIN - GRID_W) * self.sx),
            int(DESIGN_LEFT_ORIGIN[1] * self.sy),
        )
        self.buttons_y = int(140 * self.sy)
        self.sprite_map = build_sprite_map(sheet)

        self.player = Board()
        self.enemy = Board()
        self.ai = SimpleAI()
        self.ai.reset()
        self.enemy_ready = False
        self.player_ready = (not self.net_enabled) or self.opponent_is_bot
        self.waiting_for_enemy = False
        if not self.net_enabled or self.opponent_is_bot:
            self.ai.random_place_all(self.enemy)
            self.enemy_ready = True

        self.placement_list = VEHICLE_DEFS[:5] + [VEHICLE_DEFS[5]] * 2
        self.placing_index = 0
        self.current_rot = 0
        self.placement_left = PLACEMENT_LIMIT_S

        self.phase = "PLACEMENT"
        self.player_turn = True if (not self.net_enabled or self.opponent_is_bot or self.local_starts) else False
        self.shot_left = TURN_LIMIT_PLAYER_S if self.player_turn else TURN_LIMIT_ENEMY_S
        self.player_special_ready = False
        self.enemy_special_ready = False
        self.player_special_mode = None
        self.radar_flash = None
        self.banner_queue = []
        self.log = []
        self.pending_outcome = None
        self.pending_payload = {}
        self.end_banner = None
        self.minigame_id = MINIGAME_ID
        self._completed = False
        self.awaiting_result = False
        self.pending_action_id = None

        self.fog_tile = pygame.Surface((self.cw, self.ch), pygame.SRCALPHA)
        self.fog_tile.fill((0, 0, 0, 90))

        self.top_band_rect = pygame.Rect(
            self.right_origin[0], self.right_origin[1] - self.ch, GRID_SIZE * self.cw, self.ch
        )
        self.left_band_rect = pygame.Rect(
            self.right_origin[0] - self.cw, self.right_origin[1], self.cw, GRID_SIZE * self.ch
        )

    def _push_banner(self, text, sub=None, seconds=1.2):
        self.banner_queue.append({"text": text, "sub": sub, "timer": seconds})

    def _auto_place_remaining(self, board, start_idx):
        for i in range(start_idx, len(self.placement_list)):
            name, base, is_mine = self.placement_list[i]
            for _ in range(2000):
                rot = 0 if is_mine else random.randint(0, 3)
                rc = normalize_to_origin([rot_cell(c, rot) for c in base])
                w, h = bbox_size(rc)
                ox = random.randint(0, GRID_SIZE - w)
                oy = random.randint(0, GRID_SIZE - h)
                inst = VehicleInstance(name, base, is_mine, origin=(ox, oy), rot=rot)
                if board.can_place(inst):
                    board.place(inst)
                    break

    def _apply_normal_shot(self, target_board, cell, shooter_is_player):
        res, veh, sunk = target_board.receive_fire(cell)
        if res == "repeat":
            return None
        if res == "mine":
            self.log.append("Mine detonated!")
            self._push_banner("MINE!", "Boom!", BANNER_MINE_S)
            if shooter_is_player:
                self.enemy_special_ready = True
            else:
                self.player_special_ready = True
            return {"hit_any": False, "end_turn": True, "result": "mine", "cell": cell, "sunk": sunk}
        if res == "hit":
            self.log.append("Hit.")
            if sunk:
                title = "Destroyed!" if shooter_is_player else "Unit Lost"
                self._push_banner(title, veh.name, BANNER_DESTROYED_S)
            else:
                self._push_banner("HIT!", seconds=BANNER_HIT_S)
            return {"hit_any": True, "end_turn": False, "result": "hit", "cell": cell, "sunk": sunk}
        self.log.append("Miss.")
        return {"hit_any": False, "end_turn": True, "result": "miss", "cell": cell, "sunk": sunk}

    def _perform_air_strike(self, target_board, center_cell, shooter_is_player):
        hit_any = False
        mine_forced = False
        hits = []
        misses = []
        cx, cy = center_cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                x, y = cx + dx, cy + dy
                if 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE:
                    r = self._apply_normal_shot(target_board, (x, y), shooter_is_player)
                    if r is None:
                        continue
                    if r["hit_any"]:
                        hit_any = True
                        hits.append((x, y))
                    if r["end_turn"] and not r["hit_any"]:
                        mine_forced = True
                    if r.get("result") == "miss":
                        misses.append((x, y))
        if mine_forced:
            return {"hit_any": hit_any, "end_turn": True, "hits": hits, "misses": misses}
        return {"hit_any": hit_any, "end_turn": (not hit_any), "hits": hits, "misses": misses}

    def _instant_redraw(self):
        self._render_scene()
        pygame.display.flip()

    def _perform_blitz_line(self, target_board, is_row, index, shooter_is_player):
        hit_any = False
        mine_forced = False
        hits = []
        misses = []
        seq = (
            [(x, index) for x in range(GRID_SIZE)]
            if is_row
            else [(index, y) for y in range(GRID_SIZE)]
        )
        for x, y in seq:
            if (x, y) in target_board.hits or (x, y) in target_board.misses:
                continue
            was_occupied = (x, y) in target_board.occ
            was_mine = False
            if was_occupied:
                v = target_board.vehicles[target_board.occ[(x, y)]]
                was_mine = v.is_mine
            r = self._apply_normal_shot(target_board, (x, y), shooter_is_player)
            if r is None:
                continue
            self._instant_redraw()
            pygame.event.pump()
            pygame.time.wait(
                BLITZ_STEP_MS + (BLITZ_HIT_BONUS_MS if (was_occupied and not was_mine) else 0)
            )
            if was_mine:
                mine_forced = True
                break
            if was_occupied:
                hit_any = True
                hits.append((x, y))
                break
            else:
                misses.append((x, y))
        if mine_forced:
            return {"hit_any": hit_any, "end_turn": True, "hits": hits, "misses": misses}
        return {"hit_any": hit_any, "end_turn": (not hit_any), "hits": hits, "misses": misses}

    def _perform_radar_ping(self, target_board, center_cell, shooter_is_player):
        cx, cy = center_cell
        r = self._apply_normal_shot(target_board, (cx, cy), shooter_is_player)
        if r is None:
            r = {"hit_any": False, "end_turn": False}
        best = None
        best_d = 999
        for (x, y), vidx in list(target_board.occ.items()):
            v = target_board.vehicles[vidx]
            if v.is_mine:
                continue
            d = abs(x - cx) + abs(y - cy)
            if d <= 3 and d < best_d:
                best = (x, y)
                best_d = d
        if best is not None:
            cells = {best}
            blinks = 1
        else:
            cells = {(cx, cy)}
            blinks = 2
        self.radar_flash = {
            "cells": cells,
            "on_enemy": shooter_is_player,
            "state": "on",
            "state_time": 0.0,
            "blink_on": RADAR_BLINK_ON_MS / 1000.0,
            "blink_off": RADAR_BLINK_OFF_MS / 1000.0,
            "blinks_left": blinks,
        }
        r["radar_cells"] = list(cells)
        return r

    def _check_victory_after_player_action(self):
        if self.enemy.all_sunk():
            self._set_result("win", "Enemy convoy destroyed")

    def _check_victory_after_enemy_action(self):
        if self.player.all_sunk():
            self._set_result("lose", "Your convoy was lost")

    def _begin_battle(self):
        if self.phase == "BATTLE":
            return
        self.phase = "BATTLE"
        self.battle_started = True
        # Respect deterministic starter for PvP; otherwise local starts.
        self.player_turn = True if (not self.net_enabled or self.opponent_is_bot or self.local_starts) else False
        self.player_special_mode = None
        self.shot_left = TURN_LIMIT_PLAYER_S if self.player_turn else TURN_LIMIT_ENEMY_S
        self._push_banner("ENGAGE!", "Battle start", BANNER_START_S)

    def _set_result(self, outcome, subtitle):
        if self.pending_outcome:
            return
        self.pending_outcome = outcome
        self.phase = "END"
        title = "VICTORY" if outcome == "win" else "DEFEAT" if outcome == "lose" else "FORFEIT"
        self.end_banner = {"text": title, "sub": subtitle, "timer": BANNER_ENDGAME_S}
        if not self.pending_payload:
            self._capture_payload(outcome)

    def _capture_payload(self, outcome):
        self.pending_payload = {
            "outcome": outcome,
            "player_hits": len(self.enemy.hits),
            "player_misses": len(self.enemy.misses),
            "enemy_hits": len(self.player.hits),
            "enemy_misses": len(self.player.misses),
            "vehicles_destroyed": len([v for v in self.enemy.convoy_vehicles() if v.sunk]),
            "vehicles_lost": len([v for v in self.player.convoy_vehicles() if v.sunk]),
        }

    def _enemy_take_turn(self):
        if self.enemy_special_ready:
            choice = random.choice(["air", "blitz", "radar"])
            if choice == "air":
                center = (random.randrange(GRID_SIZE), random.randrange(GRID_SIZE))
                res = self._perform_air_strike(self.player, center, False)
            elif choice == "blitz":
                is_row = bool(random.getrandbits(1))
                idx = random.randrange(GRID_SIZE)
                res = self._perform_blitz_line(self.player, is_row, idx, False)
            else:
                center = (random.randrange(GRID_SIZE), random.randrange(GRID_SIZE))
                res = self._perform_radar_ping(self.player, center, False)
            self.enemy_special_ready = False
        else:
            tgt = self.ai.choose_fire()
            res = self._apply_normal_shot(self.player, tgt, False)
            if res is None:
                res = {"hit_any": False, "end_turn": True}
        self._check_victory_after_enemy_action()
        if self.pending_outcome:
            return
        if res["end_turn"]:
            self.player_turn = True
            self.shot_left = TURN_LIMIT_PLAYER_S
        else:
            self.player_turn = False
            self.shot_left = TURN_LIMIT_ENEMY_S

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[BattleConvoy] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    # ---------- Multiplayer helpers ----------
    def _net_send_action(self, action: dict):
        if not self.net_enabled or not self.net_client or not action:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": action})
        except Exception as exc:
            print(f"[BattleConvoy] Failed to send action: {exc}")

    def _net_poll_actions(self):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            action = msg.get("action") or {}
            sender = msg.get("from")
            kind = action.get("kind")
            if kind == "placement_ready":
                if sender == self.local_id:
                    continue
                self.enemy_ready = True
                self._maybe_start_battle_net(send=False)
            elif kind == "shot":
                if sender == self.local_id:
                    continue
                self._net_handle_incoming_shot(action, sender)
            elif kind == "shot_result":
                if sender == self.local_id:
                    continue
                self._net_handle_shot_result(action)
            elif kind == "battle_start":
                if sender == self.local_id:
                    continue
                self.enemy_ready = True
                self._begin_battle()

    def _net_mark_player_ready(self):
        if self.player_ready or not self.net_enabled:
            return
        self.player_ready = True
        self._net_send_action({"kind": "placement_ready"})
        self._maybe_start_battle_net(send=True)

    def _maybe_start_battle_net(self, send: bool):
        """When both players are ready, start battle and sync."""
        if not self.net_enabled or self.opponent_is_bot:
            return
        if self.phase != "PLACEMENT":
            return
        if self.player_ready and self.enemy_ready and not self.battle_started:
            self.battle_started = True
            if send:
                self._net_send_action({"kind": "battle_start"})
            self._begin_battle()

    def _net_handle_shot_result(self, action: dict):
        if not self.awaiting_result or action.get("action_id") != self.pending_action_id:
            return
        self.awaiting_result = False
        self.pending_action_id = None
        # Update enemy board knowledge for UI
        for c in action.get("hits", []):
            self.enemy.hits.add(tuple(c))
        for c in action.get("misses", []):
            self.enemy.misses.add(tuple(c))
        if action.get("radar_cells"):
            self.radar_flash = {
                "cells": set(tuple(c) for c in action.get("radar_cells")),
                "on_enemy": True,
                "state": "on",
                "state_time": 0.0,
                "blink_on": RADAR_BLINK_ON_MS / 1000.0,
                "blink_off": RADAR_BLINK_OFF_MS / 1000.0,
                "blinks_left": 2,
            }
        if action.get("opponent_defeated"):
            self._set_result("win", "Enemy convoy destroyed")
            return
        # Turn handling: if end_turn True, pass to opponent; else keep local turn.
        if action.get("end_turn", True):
            self.player_turn = False
            self.shot_left = TURN_LIMIT_ENEMY_S
        else:
            self.player_turn = True
            self.shot_left = TURN_LIMIT_PLAYER_S

    def _net_handle_incoming_shot(self, action: dict, sender_id: str):
        if self.phase != "BATTLE" or not self.battle_started:
            return
        mode = action.get("mode", "normal")
        res = None
        end_turn = True
        hits = []
        misses = []
        radar_cells = []
        if mode == "blitz":
            res = self._perform_blitz_line(
                self.player, bool(action.get("is_row")), int(action.get("index", 0)), False
            )
        elif mode == "air":
            tgt = action.get("target") or (0, 0)
            res = self._perform_air_strike(self.player, tuple(tgt), False)
        elif mode == "radar":
            tgt = action.get("target") or (0, 0)
            res = self._perform_radar_ping(self.player, tuple(tgt), False)
        else:
            tgt = action.get("target") or (0, 0)
            res = self._apply_normal_shot(self.player, tuple(tgt), False)
            if res is None:
                res = {"hit_any": False, "end_turn": True, "result": "repeat", "cell": tuple(tgt)}
        if res:
            end_turn = res.get("end_turn", True)
            hits = res.get("hits", [])
            misses = res.get("misses", [])
            if res.get("result") == "hit" and res.get("cell") is not None:
                hits.append(tuple(res["cell"]))
            if res.get("result") == "miss" and res.get("cell") is not None:
                misses.append(tuple(res["cell"]))
            radar_cells = res.get("radar_cells", [])

        self._check_victory_after_enemy_action()
        defeated = False
        if self.pending_outcome == "lose":
            defeated = True

        response = {
            "kind": "shot_result",
            "action_id": action.get("action_id"),
            "mode": mode,
            "hits": [(int(x), int(y)) for x, y in hits],
            "misses": [(int(x), int(y)) for x, y in misses],
            "end_turn": end_turn,
            "radar_cells": radar_cells,
            "opponent_defeated": False,
        }
        if defeated:
            response["opponent_defeated"] = True  # from shooter's perspective, they win
        self._net_send_action(response)
        if defeated and not self.pending_outcome:
            self._set_result("lose", "Your convoy was lost")
        if end_turn:
            # Shooter's turn ended; defender (local) now acts.
            self.player_turn = True
            self.shot_left = TURN_LIMIT_PLAYER_S
        else:
            # Shooter keeps turn; defender waits.
            self.player_turn = False
            self.shot_left = TURN_LIMIT_ENEMY_S

    def _random_untried_cell(self, board: Board):
        tried = set(board.hits) | set(board.misses)
        pool = [(x, y) for x in range(GRID_SIZE) for y in range(GRID_SIZE) if (x, y) not in tried]
        if not pool:
            return (0, 0)
        return random.choice(pool)

    def _auto_fire_random_net(self):
        """Auto-fire a random shot in PvP when turn timer expires."""
        if not (self.net_enabled and not self.opponent_is_bot):
            return
        if not self.enemy_ready or not self.player_turn or self.awaiting_result:
            return
        tgt = self._random_untried_cell(self.enemy)
        action_id = uuid.uuid4().hex
        self.pending_action_id = action_id
        self.awaiting_result = True
        payload = {"kind": "shot", "mode": "normal", "target": tgt, "action_id": action_id}
        self._net_send_action(payload)
        self.player_special_mode = None
        self.player_special_ready = False

    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._finalize(self.pending_outcome)
            return

        # Debug shortcuts: force win/lose to speed up testing (disabled in multiplayer).
        if event.type == pygame.KEYDOWN and not self.net_enabled:
            if event.key == pygame.K_F5:
                self.pending_payload = {"reason": "forced_win"}
                self._set_result("win", "Forced win (debug)")
                return
            if event.key == pygame.K_F6:
                self.pending_payload = {"reason": "forced_lose"}
                self._set_result("lose", "Forced lose (debug)")
                return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return

        if self.phase == "PLACEMENT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r:
                    self.current_rot = (self.current_rot + 1) % 4
                elif event.key == pygame.K_BACKSPACE:
                    self.player.undo_last()
                    self.placing_index = max(0, self.placing_index - 1)
            elif (
                event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1
                and self.placing_index < len(self.placement_list)
            ):
                m = cell_from_mouse(self.left_origin, self.cw, self.ch, event.pos)
                if m is not None:
                    name, base, is_mine = self.placement_list[self.placing_index]
                    rot = 0 if is_mine else self.current_rot
                    inst = VehicleInstance(name, base, is_mine, origin=m, rot=rot)
                    if self.player.can_place(inst):
                        self.player.place(inst)
                        self.placing_index += 1
                        if self.placing_index >= len(self.placement_list):
                            self._net_mark_player_ready()
            return

        if self.phase != "BATTLE":
            return

        if self.net_enabled and not self.opponent_is_bot:
            # Block input until both are ready and battle started.
            if not (self.player_ready and self.enemy_ready and self.battle_started):
                return

        click_consumed = False
        if self.player_turn and self.player_special_ready and self.player_special_mode is None:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_a:
                    self.player_special_mode = "air"
                elif event.key == pygame.K_b:
                    self.player_special_mode = "blitz"
                elif event.key == pygame.K_r:
                    self.player_special_mode = "radar"
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                for rect, key in special_button_rects(self.screen, self.buttons_y):
                    if rect.collidepoint((mx, my)):
                        self.player_special_mode = key
                        click_consumed = True
                        break

        if not self.player_turn:
            return
        if self.net_enabled and not self.opponent_is_bot and self.awaiting_result:
            return
        if self.net_enabled and not self.opponent_is_bot and not self.enemy_ready:
            return
        if self.net_enabled and not self.opponent_is_bot and not self.battle_started:
            return

        if (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and not click_consumed
        ):
            mx, my = event.pos
            if self.player_special_mode == "blitz":
                acted = False
                if self.top_band_rect.collidepoint((mx, my)):
                    col = (mx - self.right_origin[0]) // self.cw
                    if 0 <= col < GRID_SIZE:
                        if self.net_enabled and not self.opponent_is_bot:
                            action_id = uuid.uuid4().hex
                            self.pending_action_id = action_id
                            self.awaiting_result = True
                            self._net_send_action({"kind": "shot", "mode": "blitz", "is_row": False, "index": int(col), "action_id": action_id})
                            acted = True
                        else:
                            res = self._perform_blitz_line(self.enemy, False, int(col), True)
                            acted = True
                    acted = True
                elif self.left_band_rect.collidepoint((mx, my)):
                    row = (my - self.right_origin[1]) // self.ch
                    if 0 <= row < GRID_SIZE:
                        if self.net_enabled and not self.opponent_is_bot:
                            action_id = uuid.uuid4().hex
                            self.pending_action_id = action_id
                            self.awaiting_result = True
                            self._net_send_action({"kind": "shot", "mode": "blitz", "is_row": True, "index": int(row), "action_id": action_id})
                        else:
                            res = self._perform_blitz_line(self.enemy, True, int(row), True)
                        acted = True
                if acted:
                    if not self.net_enabled or self.opponent_is_bot:
                        self.player_special_ready = FORCE_SPECIALS_FOR_PLAYER
                        self.player_special_mode = None
                        self._check_victory_after_player_action()
                        if self.pending_outcome:
                            return
                        if res["end_turn"]:
                            self.player_turn = False
                            self.shot_left = TURN_LIMIT_ENEMY_S
                        else:
                            self.shot_left = TURN_LIMIT_PLAYER_S
                    else:
                        self.player_special_ready = False
                        self.player_special_mode = None
                return
            target = cell_from_mouse(self.right_origin, self.cw, self.ch, (mx, my))
            if target is None:
                return
            mode = "normal"
            if self.player_special_mode == "air":
                mode = "air"
            elif self.player_special_mode == "radar":
                mode = "radar"
            if self.net_enabled and not self.opponent_is_bot:
                action_id = uuid.uuid4().hex
                self.pending_action_id = action_id
                self.awaiting_result = True
                # Avoid sending duplicate shots to the same cell.
                if tuple(target) in self.enemy.hits or tuple(target) in self.enemy.misses:
                    self.awaiting_result = False
                    self.pending_action_id = None
                    return
                payload = {"kind": "shot", "mode": mode, "target": target, "action_id": action_id}
                self._net_send_action(payload)
                self.player_special_mode = None
                self.player_special_ready = False
                return
            if mode == "air":
                res = self._perform_air_strike(self.enemy, target, True)
                self.player_special_ready = FORCE_SPECIALS_FOR_PLAYER
                self.player_special_mode = None
            elif mode == "radar":
                res = self._perform_radar_ping(self.enemy, target, True)
                self.player_special_ready = FORCE_SPECIALS_FOR_PLAYER
                self.player_special_mode = None
            else:
                res = self._apply_normal_shot(self.enemy, target, True)
                if res is None:
                    return
            self._check_victory_after_player_action()
            if self.pending_outcome:
                return
            if res["end_turn"]:
                self.player_turn = False
                self.shot_left = TURN_LIMIT_ENEMY_S
            else:
                self.shot_left = TURN_LIMIT_PLAYER_S

    def update(self, dt):
        # Poll any incoming duel actions for multiplayer.
        self._net_poll_actions()
        if self.pending_outcome:
            if self.end_banner:
                self.end_banner["timer"] -= dt
                if self.end_banner["timer"] <= 0:
                    self._finalize(self.pending_outcome)
            else:
                self._finalize(self.pending_outcome)
            return

        if self.phase == "PLACEMENT":
            self.placement_left -= dt
            if self.placing_index >= len(self.placement_list) or self.placement_left <= 0:
                if self.placing_index < len(self.placement_list):
                    self._auto_place_remaining(self.player, self.placing_index)
                    self.placing_index = len(self.placement_list)
                self._net_mark_player_ready()
                if not self.net_enabled or self.opponent_is_bot:
                    if not self.battle_started:
                        self._begin_battle()
        elif self.phase == "BATTLE":
            if self.net_enabled and not self.opponent_is_bot and not (self.player_ready and self.enemy_ready and self.battle_started):
                return
            if self.net_enabled and not self.opponent_is_bot and self.awaiting_result:
                # Wait for opponent result; freeze timer.
                pass
            if FORCE_SPECIALS_FOR_PLAYER and self.player_turn:
                self.player_special_ready = True
            if not self.player_turn:
                self.player_special_mode = None
            if self.radar_flash:
                self.radar_flash["state_time"] += dt
                if self.radar_flash["state"] == "on":
                    if self.radar_flash["state_time"] >= self.radar_flash["blink_on"]:
                        self.radar_flash["state"] = "off"
                        self.radar_flash["state_time"] = 0.0
                        self.radar_flash["blinks_left"] -= 1
                        if self.radar_flash["blinks_left"] <= 0:
                            self.radar_flash = None
                elif self.radar_flash["state"] == "off":
                    if self.radar_flash["state_time"] >= self.radar_flash["blink_off"]:
                        self.radar_flash["state"] = "on"
                        self.radar_flash["state_time"] = 0.0
            if self.net_enabled and not self.opponent_is_bot:
                # PvP: only tick timer on our turn; auto-fire if time expires.
                if self.player_turn and not self.awaiting_result:
                    self.shot_left -= dt
                    if self.shot_left <= 0:
                        self._auto_fire_random_net()
                        self.shot_left = TURN_LIMIT_PLAYER_S
                else:
                    # Clamp timer while waiting for opponent.
                    self.shot_left = TURN_LIMIT_ENEMY_S if not self.player_turn else self.shot_left
            else:
                # PvE (NPC opponent) legacy behavior.
                if not (self.net_enabled and not self.opponent_is_bot and self.awaiting_result):
                    self.shot_left -= dt
                    if self.shot_left <= 0:
                        if self.player_turn:
                            self.player_turn = False
                            self.shot_left = TURN_LIMIT_ENEMY_S
                            self.player_special_mode = None
                        else:
                            self._enemy_take_turn()

        if self.banner_queue:
            self.banner_queue[0]["timer"] -= dt
            if self.banner_queue[0]["timer"] <= 0:
                self.banner_queue.pop(0)

    def draw(self):
        self._render_scene()

    def _render_scene(self):
        screen = self.screen
        screen.blit(self.bg_scaled, (0, 0))

        for v in self.player.vehicles:
            blit_piece_cells(
                screen,
                self.sprite_map[v.name],
                v.name,
                self.left_origin,
                self.cw,
                self.ch,
                v.origin,
                v.base_cells,
                v.rot,
            )
        draw_hits_misses(screen, self.left_origin, self.cw, self.ch, self.player.hits, self.player.misses)

        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                cell = (x, y)
                if cell not in self.enemy.hits and cell not in self.enemy.misses:
                    screen.blit(self.fog_tile, (self.right_origin[0] + x * self.cw, self.right_origin[1] + y * self.ch))
        draw_hits_misses(screen, self.right_origin, self.cw, self.ch, self.enemy.hits, self.enemy.misses)

        if self.radar_flash and self.radar_flash.get("state") == "on":
            origin = self.right_origin if self.radar_flash["on_enemy"] else self.left_origin
            hi = pygame.Surface((self.cw, self.ch), pygame.SRCALPHA)
            hi.fill((238, 220, 130, 110))
            for cx, cy in self.radar_flash["cells"]:
                screen.blit(hi, (origin[0] + cx * self.cw, origin[1] + cy * self.ch))

        if self.phase == "PLACEMENT" and self.placing_index < len(self.placement_list):
            m = cell_from_mouse(self.left_origin, self.cw, self.ch, pygame.mouse.get_pos())
            name, base, is_mine = self.placement_list[self.placing_index]
            if m is not None:
                ghost = VehicleInstance(name, base, is_mine, origin=m, rot=(0 if is_mine else self.current_rot))
                ok = self.player.can_place(ghost)
                rc = normalize_to_origin([rot_cell(c, ghost.rot) for c in base])
                w, h = bbox_size(rc)
                temp = pygame.Surface((w * self.cw, h * self.ch), pygame.SRCALPHA)
                blit_piece_cells(
                    temp,
                    self.sprite_map[name],
                    name,
                    (0, 0),
                    self.cw,
                    self.ch,
                    (0, 0),
                    base,
                    ghost.rot,
                    alpha=200,
                )
                tint = pygame.Surface((self.cw, self.ch), pygame.SRCALPHA)
                tint.fill((80, 200, 120, 80) if ok else (220, 80, 80, 90))
                for cx, cy in rc:
                    src = pygame.Rect(cx * self.cw, cy * self.ch, self.cw, self.ch)
                    tile = temp.subsurface(src).copy()
                    dx = self.left_origin[0] + (m[0] + cx) * self.cw
                    dy = self.left_origin[1] + (m[1] + cy) * self.ch
                    screen.blit(tile, (dx, dy))
                    screen.blit(tint, (dx, dy))

        if self.phase == "PLACEMENT":
            draw_timer_center(screen, self.placement_left, int(76 * self.sy), "Place convoy")
        else:
            label = "Your shot" if self.player_turn else "Enemy shot"
            draw_timer_center(screen, self.shot_left, int(76 * self.sy), label)
            if self.player_turn and self.player_special_ready:
                if self.player_special_mode is None:
                    draw_status_center(
                        screen, "Special ready — choose one:", int(96 * self.sy), (238, 220, 130)
                    )
                    draw_special_buttons(screen, self.buttons_y)
                elif self.player_special_mode == "blitz":
                    draw_status_center(
                        screen,
                        "BLITZ: click top letters (col) or left numbers (row).",
                        int(130 * self.sy),
                    )

        icon_names = [n for (n, _, is_mine) in VEHICLE_DEFS if not is_mine]
        draw_side_icon_columns(screen, self.sprite_map, icon_names, self.enemy, self.player, self.sx, self.sy)

        if self.banner_queue:
            panel = banner(screen.get_size(), self.banner_queue[0]["text"], self.banner_queue[0]["sub"])
            rect = panel.get_rect(center=(self.w // 2, int(120 * self.sy)))
            screen.blit(panel, rect)

        if self.end_banner:
            panel = banner(screen.get_size(), self.end_banner["text"], self.end_banner["sub"])
            rect = panel.get_rect(center=(self.w // 2, int(160 * self.sy)))
            screen.blit(panel, rect)

    def _finalize(self, outcome):
        if self._completed or outcome is None:
            return
        self._completed = True
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
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[BattleConvoy] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[BattleConvoy] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
            return
        self.pending_payload = {"reason": "forfeit"}
        self._set_result("forfeit", "Player forfeited")


def launch(manager, context=None, callback=None, **kwargs):
    return BattleConvoyScene(manager, context, callback, **kwargs)
