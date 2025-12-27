import pygame, math, random


# --- Character class ---
class Character:
    def __init__(self, x, y, color=(255, 255, 255)):
        self.x, self.y = x, y
        self.vx, self.vy = 0, 0
        self.radius = 16
        self.color = color
        self.rotation = 0
        self.on_ground = False
        self.stunned = False
        self.stun_timer = 0

    def update(self, dt, segments, gravity):
        if self.stunned:
            self.stun_timer -= dt
            if self.stun_timer <= 0:
                self.stunned = False
            return True

        self.vy += gravity * dt
        old_y = self.y
        self.y += self.vy * dt
        self.on_ground = False
        player_rect = pygame.Rect(
            self.x - self.radius, self.y - self.radius, self.radius * 2, self.radius * 2
        )

        for seg in segments:
            # Static platforms
            for plat in seg.get("platforms", []):
                if player_rect.colliderect(plat):
                    if self.vy >= 0 and old_y + self.radius <= plat.top + 5:
                        self.y = plat.top - self.radius
                        self.vy = 0
                        self.on_ground = True
                    elif self.vy < 0 and player_rect.top < plat.bottom:
                        self.y = plat.bottom + self.radius
                        self.vy = 0

            # Moving platforms
            for mplat in seg.get("moving", []):
                plat = mplat.rect
                if player_rect.colliderect(plat):
                    if self.vy >= 0 and old_y + self.radius <= plat.top + 5:
                        self.y = plat.top - self.radius
                        self.vy = 0
                        self.on_ground = True
                        if mplat.axis == "x":
                            self.x += mplat.direction * mplat.speed * dt
                        else:
                            self.y += mplat.direction * mplat.speed * dt

            # Rotating platforms
            for rplat in seg.get("rotating", []):
                for plat in rplat.rects:
                    if player_rect.colliderect(plat):
                        if self.vy >= 0 and old_y + self.radius <= plat.top + 5:
                            self.y = plat.top - self.radius
                            self.vy = 0
                            self.on_ground = True
                        elif self.vy < 0 and player_rect.top < plat.bottom:
                            self.y = plat.bottom + self.radius
                            self.vy = 0

        # Horizontal movement
        self.x += self.vx * dt
        player_rect = pygame.Rect(
            self.x - self.radius, self.y - self.radius, self.radius * 2, self.radius * 2
        )

        for seg in segments:
            for plat in seg.get("platforms", []):
                if player_rect.colliderect(plat):
                    if self.vx > 0 and player_rect.right > plat.left:
                        self.x = plat.left - self.radius
                        self.vx = 0
                    elif self.vx < 0 and player_rect.left < plat.right:
                        self.x = plat.right + self.radius
                        self.vx = 0

        # Rotation + friction
        if abs(self.vx) > 1:
            self.rotation += (self.vx * dt) / (2 * math.pi * self.radius) * 360
        if self.on_ground:
            self.vx *= 0.9 if abs(self.vx) > 20 else 0

        if self.y > 480:
            return False
        return True

    def stun(self, duration=1.0):
        self.stunned = True
        self.stun_timer = duration

    def draw(self, surface, camx):
        sides = 10
        pts = []
        for i in range(sides):
            ang = math.radians(self.rotation + (360 / sides) * i)
            px = self.x - camx + math.cos(ang) * self.radius
            py = self.y + math.sin(ang) * self.radius
            pts.append((px, py))
        color = (255, 80, 80) if self.stunned else self.color
        pygame.draw.polygon(surface, color, pts)


# --- Enemy class ---
class Enemy:
    def __init__(self, x, y, w=30, h=30, speed=80, patrol_range=200):
        self.base_x = x
        self.y = y
        self.w, self.h = w, h
        self.speed = speed
        self.range = patrol_range
        self.direction = 1
        self.offset = 0

    @property
    def rect(self):
        return pygame.Rect(
            self.base_x + int(self.offset), self.y - self.h, self.w, self.h
        )

    def update(self, dt):
        move = self.speed * dt * self.direction
        self.offset += move
        if abs(self.offset) >= self.range:
            self.direction *= -1

    def draw(self, surface, camx):
        pygame.draw.rect(surface, (255, 0, 0), self.rect.move(-camx, 0))


# --- Moving Platform ---
class MovingPlatform:
    def __init__(self, x, y, w, h, axis="x", distance=120, speed=60):
        self.base = pygame.Rect(x, y, w, h)
        self.axis, self.distance, self.speed = axis, distance, speed
        self.offset, self.direction = 0, 1

    def update(self, dt):
        self.offset += self.speed * dt * self.direction
        if abs(self.offset) >= self.distance:
            self.direction *= -1
            self.offset = max(min(self.offset, self.distance), -self.distance)

    @property
    def rect(self):
        return (
            self.base.move(int(self.offset), 0)
            if self.axis == "x"
            else self.base.move(0, int(self.offset))
        )

    def draw(self, surface, camx):
        pygame.draw.rect(surface, (200, 100, 255), self.rect.move(-camx, 0))


# --- Rotating Platform ---
class RotatingPlatform:
    def __init__(self, cx, cy, radius, count=6, speed=1.0, size=(60, 15)):
        self.cx, self.cy = cx, cy
        self.radius, self.count, self.speed = radius, count, speed
        self.size, self.angle = size, 0

    def update(self, dt):
        self.angle += self.speed * dt * 90

    @property
    def rects(self):
        rects, step = [], 360 / self.count
        for i in range(self.count):
            ang = math.radians(self.angle + i * step)
            px = self.cx + math.cos(ang) * self.radius
            py = self.cy + math.sin(ang) * self.radius
            rects.append(
                pygame.Rect(
                    px - self.size[0] // 2,
                    py - self.size[1] // 2,
                    self.size[0],
                    self.size[1],
                )
            )
        return rects

    def draw(self, surface, camx):
        for r in self.rects:
            pygame.draw.rect(surface, (255, 200, 50), r.move(-camx, 0))


# --- Segments ---
def make_start_segment(offset_x):
    floor_y = 420
    rect = pygame.Rect(offset_x, floor_y, 250, 20)
    return {
        "platforms": [rect],
        "enemies": [],
        "moving": [],
        "rotating": [],
        "end_x": rect.right,
        "death_y": 480,
        "start_platform": rect,
    }


def make_body_segment(offset_x):
    floor_y = 420
    platforms = [
        pygame.Rect(offset_x, floor_y, 400, 20),
        pygame.Rect(offset_x + 500, floor_y, 250, 20),
        pygame.Rect(offset_x + 870, floor_y - 40, 250, 20),
        pygame.Rect(offset_x + 1250, floor_y, 250, 20),
        pygame.Rect(offset_x + 1640, floor_y - 50, 250, 20),
        pygame.Rect(offset_x + 2000, floor_y, 400, 20),
    ]
    return {
        "platforms": platforms,
        "enemies": [],
        "moving": [],
        "rotating": [],
        "end_x": platforms[-1].right,
        "death_y": 480,
        "checkpoint": platforms[-1],
    }


def make_challenge_segment(offset_x):
    floor_y = 420
    platforms = [
        pygame.Rect(offset_x, floor_y, 300, 20),
        pygame.Rect(offset_x + 430, floor_y - 20, 150, 20),
        pygame.Rect(offset_x + 730, floor_y - 70, 150, 20),
        pygame.Rect(offset_x + 1040, floor_y - 30, 150, 20),
        pygame.Rect(offset_x + 1340, floor_y - 60, 150, 20),
        pygame.Rect(offset_x + 1660, floor_y - 40, 150, 20),
        pygame.Rect(offset_x + 2000, floor_y, 450, 20),
    ]
    return {
        "platforms": platforms,
        "enemies": [],
        "moving": [],
        "rotating": [],
        "end_x": platforms[-1].right,
        "death_y": 480,
        "checkpoint": platforms[-1],
    }


def make_enemy_platforms_segment(offset_x):
    floor_y = 420
    p2 = pygame.Rect(offset_x + 520, floor_y - 40, 300, 20)
    p3 = pygame.Rect(offset_x + 970, floor_y, 300, 20)
    p4 = pygame.Rect(offset_x + 1400, floor_y - 60, 300, 20)
    p5 = pygame.Rect(offset_x + 1900, floor_y, 400, 20)
    platforms = [pygame.Rect(offset_x, floor_y, 400, 20), p2, p3, p4, p5]
    enemies = [
        Enemy(p2.centerx, p2.top, patrol_range=120),
        Enemy(p3.centerx, p3.top, patrol_range=180),
        Enemy(p4.centerx, p4.top, patrol_range=100),
    ]
    return {
        "platforms": platforms,
        "enemies": enemies,
        "moving": [],
        "rotating": [],
        "end_x": platforms[-1].right,
        "death_y": 480,
        "checkpoint": platforms[-1],
    }


def make_moving_segment(offset_x):
    floor_y = 420
    start = pygame.Rect(offset_x, floor_y, 250, 20)
    cur_x, spacing = start.right + 180, 280
    moving_platforms = []
    for i in range(6):
        axis = "x" if i % 2 == 0 else "y"
        moving_platforms.append(
            MovingPlatform(
                cur_x,
                floor_y - (100 if axis == "y" else 80),
                150,
                20,
                axis=axis,
                distance=100 if axis == "x" else 80,
                speed=60 + i * 10,
            )
        )
        cur_x += spacing
    end = pygame.Rect(cur_x, floor_y, 300, 20)
    return {
        "platforms": [start, end],
        "enemies": [],
        "moving": moving_platforms,
        "rotating": [],
        "end_x": end.right,
        "death_y": 480,
        "checkpoint": end,
    }


def make_rotating_segment(offset_x, length=1600):
    """Segment with multiple rotating platform circles in sequence."""
    floor_y = 420
    rotating = []

    # Spacing between rotating rings
    spacing = 385

    # Create 3 rotating circles in sequence
    for i in range(3):
        center_x = offset_x + 275 + i * spacing
        center_y = floor_y - (60 if i % 2 == 0 else 120)  # alternate heights a bit

        rot = RotatingPlatform(
            center_x,
            center_y,
            radius=100,
            count=6,
            speed=0.8 + 0.2 * i,  # slightly faster with each circle
            size=(60, 15),
        )
        rotating.append(rot)

    # Safe landing platform after last rotating ring
    landing = pygame.Rect(center_x + 250, floor_y, 300, 20)

    return {
        "rotating": rotating,
        "platforms": [landing],  # safe exit
        "end_x": landing.right,
        "death_y": 480,
        "checkpoint": landing,  # checkpoint on the safe platform
    }


def make_mixed_combo_segment(offset_x, length=2600):
    """Fixed hybrid section with alternating moving and rotating platforms."""
    floor_y = 420
    static_platforms = []
    moving_platforms = []
    rotating = []

    # Start safe platform
    start = pygame.Rect(offset_x, floor_y, 250, 20)
    static_platforms.append(start)

    # --- First moving platform (horizontal) ---
    m1 = MovingPlatform(
        start.right + 175, floor_y - 80, 150, 20, axis="x", distance=100, speed=70
    )
    moving_platforms.append(m1)

    # --- Rotating group ---
    r1 = RotatingPlatform(
        m1.base.right + 250,
        floor_y - 100,
        radius=100,
        count=6,
        speed=0.8,
        size=(60, 15),
    )
    rotating.append(r1)

    # --- Second moving platform (vertical) ---
    m2 = MovingPlatform(
        r1.cx + 300, floor_y - 60, 150, 20, axis="y", distance=90, speed=80
    )
    moving_platforms.append(m2)

    # --- Second rotating group ---
    r2 = RotatingPlatform(
        m2.base.right + 300,
        floor_y - 120,
        radius=120,
        count=5,
        speed=-0.7,
        size=(70, 15),
    )
    rotating.append(r2)

    # --- Final safe landing ---
    landing = pygame.Rect(r2.cx + 300, floor_y, 350, 20)
    static_platforms.append(landing)

    return {
        "platforms": static_platforms,
        "moving": moving_platforms,
        "rotating": rotating,
        "end_x": landing.right,
        "death_y": 480,
        "checkpoint": landing,
    }


def make_combo_segment(offset_x, length=2200):
    """Combo gauntlet with static enemy platforms, moving platforms, and rotating sections."""
    floor_y = 420
    static_platforms = []
    moving_platforms = []
    rotating_platforms = []
    enemies = []

    # Start safe entry
    start = pygame.Rect(offset_x, floor_y, 200, 20)
    static_platforms.append(start)
    cur_x = start.right + 180  # was 200

    # 1. Static platform with enemy
    plat1 = pygame.Rect(cur_x, floor_y - 1, 260, 20)
    static_platforms.append(plat1)
    enemies.append(Enemy(plat1.centerx, plat1.top, patrol_range=120))
    cur_x = plat1.right + 160  # was 200

    # 2. Moving horizontal platform
    m1 = MovingPlatform(cur_x, floor_y - 80, 150, 20, axis="x", distance=80, speed=70)
    moving_platforms.append(m1)
    cur_x += 220  # was 300

    # 3. Rotating platform cluster
    rot1 = RotatingPlatform(cur_x + 160, floor_y - 100, radius=90, count=6, speed=0.7)
    rotating_platforms.append(rot1)
    cur_x += 400  # was 500

    # 4. Static with enemy
    plat2 = pygame.Rect(cur_x, floor_y, 240, 20)
    static_platforms.append(plat2)
    enemies.append(Enemy(plat2.centerx, plat2.top, patrol_range=100))
    cur_x = plat2.right + 160  # was 220

    # 5. Moving vertical platform
    m2 = MovingPlatform(cur_x, floor_y - 90, 150, 20, axis="y", distance=70, speed=80)
    moving_platforms.append(m2)
    cur_x += 220  # was 300

    # 6. Another rotating cluster
    rot2 = RotatingPlatform(cur_x + 160, floor_y - 100, radius=90, count=5, speed=0.8)
    rotating_platforms.append(rot2)
    cur_x += 400  # was 500

    # Final safe checkpoint
    landing = pygame.Rect(cur_x, floor_y, 350, 20)
    static_platforms.append(landing)

    return {
        "platforms": static_platforms,
        "moving": moving_platforms,
        "rotating": rotating_platforms,
        "enemies": enemies,
        "end_x": landing.right,
        "death_y": 480,
    }


def make_finish_segment(offset_x):
    floor_y = 420
    finish_rect = pygame.Rect(offset_x, floor_y, 200, 20)
    return {
        "platforms": [finish_rect],
        "enemies": [],
        "moving": [],
        "rotating": [],
        "end_x": finish_rect.right,
        "death_y": 480,
        "finish_platform": finish_rect,
        "checkpoint": None,  # ✅ prevents a blue checkpoint flag here
    }


# --- Drawing ---
def draw_segment(surface, seg, camx):
    for plat in seg.get("platforms", []):
        pygame.draw.rect(surface, (80, 200, 80), plat.move(-camx, 0))
    for mplat in seg.get("moving", []):
        mplat.draw(surface, camx)
    for rplat in seg.get("rotating", []):
        rplat.draw(surface, camx)
    for enemy in seg.get("enemies", []):
        enemy.draw(surface, camx)

    if "finish_platform" in seg:
        goal = seg["finish_platform"]
        pygame.draw.rect(
            surface,
            (255, 255, 255),
            pygame.Rect(goal.right - 10 - camx, goal.top - 60, 5, 60),
        )
        pygame.draw.rect(
            surface,
            (255, 50, 50),
            pygame.Rect(goal.right - 5 - camx, goal.top - 50, 25, 15),
        )

    if seg.get("checkpoint") and "finish_platform" not in seg:
        plat = seg["checkpoint"]
        pole = pygame.Rect(plat.right - 15 - camx, plat.top - 50, 4, 50)
        pygame.draw.rect(surface, (200, 200, 255), pole)
        flag = pygame.Rect(plat.right - 11 - camx, plat.top - 40, 20, 12)
        pygame.draw.rect(surface, (50, 150, 255), flag)
