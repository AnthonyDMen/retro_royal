import pygame
import time
from pathlib import Path
from scene_manager import Scene
from content_registry import load_game_fonts
from game_context import GameContext
from . import graphics
from resource_path import resource_path

TITLE = "Platform Race"
MINIGAME_ID = "platform_race"
MULTIPLAYER_ENABLED = True

# Settings
GRAVITY = 900  # px/s^2
MOVE_SPEED = 200
JUMP_SPEED = 400
DEATH_LIMIT = 10
POST_RESULT_S = 1.5

ASSET_DIR = Path(resource_path("minigames", "platform_race"))
BACKGROUND = ASSET_DIR / "background.png"


class PlatformRaceScene(Scene):
    def __init__(
        self,
        manager,
        context=None,
        callback=None,
        difficulty=1.0,
        duel_id=None,
        participants=None,
        multiplayer_client=None,
        local_player_id=None,
        **kwargs,
    ):
        super().__init__(manager)
        self.manager = manager
        self.context = context
        self.callback = callback
        self.minigame_id = MINIGAME_ID
        self.big, self.font, self.small = load_game_fonts()
        self.w, self.h = manager.size
        self.background = pygame.image.load(BACKGROUND).convert()
        self._pending_outcome = None
        self.pending_payload = {}
        self._completed = False
        self.forfeited = False
        self.post_timer = 0.0

        # Multiplayer plumbing
        flags = getattr(self.context, "flags", {}) or {}
        self.duel_id = duel_id or flags.get("duel_id")
        self.participants = participants or flags.get("participants") or []
        self.net_client = multiplayer_client or flags.get("multiplayer_client")
        self.local_id = local_player_id or flags.get("local_player_id")
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
        self.net_timer = 0.0
        self.net_interval = 1.0 / 15.0

        # --- Build map flow ---
        segments = []

        # Start
        seg_start = graphics.make_start_segment(0)
        segments.append(seg_start)
        offset = seg_start["end_x"]

        # Body section
        seg_body = graphics.make_body_segment(offset)
        segments.append(seg_body)
        offset = seg_body["end_x"]

        # Challenge section
        seg_challenge = graphics.make_challenge_segment(offset)
        segments.append(seg_challenge)
        offset = seg_challenge["end_x"]

        # Enemy platforms
        seg_enemy_plats = graphics.make_enemy_platforms_segment(offset)
        segments.append(seg_enemy_plats)
        offset = seg_enemy_plats["end_x"]

        # Moving platforms
        seg_moving = graphics.make_moving_segment(offset)
        segments.append(seg_moving)
        offset = seg_moving["end_x"]

        # Rotating platforms
        seg_rotating = graphics.make_rotating_segment(offset)
        segments.append(seg_rotating)
        offset = seg_rotating["end_x"]

        # Mixed combo segment
        seg_mixed = graphics.make_mixed_combo_segment(offset)
        segments.append(seg_mixed)
        offset = seg_mixed["end_x"]

        # Mixed enemy + moving platform gauntlet
        seg_combo = graphics.make_combo_segment(offset)
        segments.append(seg_combo)
        offset = seg_combo["end_x"]


        # Finish
        seg_finish = graphics.make_finish_segment(offset)
        segments.append(seg_finish)


        self.segments = segments
        self.level_w = self.segments[-1]["end_x"]

        # --- Player setup ---
        start_plat = seg_start["start_platform"]
        local_color = (200, 240, 255) if self.local_idx == 0 else (255, 210, 120)
        ghost_color = (255, 210, 120) if self.local_idx == 0 else (200, 240, 255)
        self.player = graphics.Character(x=start_plat.left + 20, y=start_plat.top - 16, color=local_color)
        self.checkpoint = start_plat
        # Remote ghost (visual only)
        self.ghost = graphics.Character(x=start_plat.left + 20, y=start_plat.top - 16, color=ghost_color)
        self.ghost_visible = False
        self.ghost_last_seen = 0.0
        self.remote_deaths = 0
        self.remote_finished = None

        self.camx = 0
        self.deaths = 0
        self.finished = None
        self.winner_id = None
        self.loser_id = None

        if self.net_enabled:
            self._net_send_state(kind="init", force=True)

    def player_collides(self, enemy):
        return pygame.Rect(
            self.player.x - self.player.radius,
            self.player.y - self.player.radius,
            self.player.radius * 2,
            self.player.radius * 2,
        ).colliderect(enemy.rect)

    def handle_event(self, event):
        if self._pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._finalize(self._pending_outcome)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()

    def update(self, dt):
        dt = max(1e-4, float(dt))
        self._net_poll_actions(dt)
        now = time.perf_counter()

        if self._pending_outcome:
            if self.post_timer > 0:
                self.post_timer = max(0.0, self.post_timer - dt)
                if self.post_timer <= 0:
                    self._finalize(self._pending_outcome)
            else:
                self._finalize(self._pending_outcome)
            return

        if self.net_enabled and self.ghost_visible and now - self.ghost_last_seen > 2.0:
            self.ghost_visible = False

        keys = pygame.key.get_pressed()
        if not self.player.stunned:
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                self.player.vx = -MOVE_SPEED
            elif keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                self.player.vx = MOVE_SPEED
            if (keys[pygame.K_UP] or keys[pygame.K_SPACE]) and self.player.on_ground:
                self.player.vy = -JUMP_SPEED

        # --- Update player physics ---
        alive = self.player.update(dt, self.segments, GRAVITY)
        if not alive:
            self.deaths += 1
            self.respawn_player()
            if self.net_enabled:
                self._net_send_state(kind="respawn", force=True)

        # --- Update all segment entities ---
        for seg in self.segments:
            for mplat in seg.get("moving", []):
                mplat.update(dt)
            for rplat in seg.get("rotating", []):
                rplat.update(dt)
            for enemy in seg.get("enemies", []):
                enemy.update(dt)
                if self.player_collides(enemy) and not self.player.stunned:
                    self.deaths += 1
                    self.player.stun(1.0)
                    self.respawn_player()

            # Checkpoints (skip None)
            if "checkpoint" in seg and seg["checkpoint"] is not None:
                plat = seg["checkpoint"]
                if self.player.x > plat.left and self.player.x < plat.right:
                    if plat != self.checkpoint:
                        self.checkpoint = plat

        # --- Win / Lose conditions ---
        if self.deaths >= DEATH_LIMIT:
            self.finished = "lose"
        if self.player.x > self.level_w:
            self.finished = "win"

        if self.finished and not self._pending_outcome:
            self._queue_finish(self.finished)
            return

        # Camera follows player
        self.camx = max(0, self.player.x - self.w // 3)

        if self.net_enabled and not self._pending_outcome:
            self.net_timer += dt
            self._net_send_state()

    def respawn_player(self):
        self.player.x = self.checkpoint.left + 40
        self.player.y = self.checkpoint.top - self.player.radius
        self.player.vx = self.player.vy = 0

    def draw(self):
        screen = self.manager.screen
        screen.blit(pygame.transform.scale(self.background, (self.w, self.h)), (0, 0))

        # Draw segments
        for seg in self.segments:
            graphics.draw_segment(screen, seg, self.camx)

        # Ghost (opponent) draw under the local player for clarity
        if self.net_enabled and self.ghost_visible:
            ghost_surface = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            self.ghost.draw(ghost_surface, self.camx)
            ghost_surface.set_alpha(150)
            screen.blit(ghost_surface, (0, 0))

        # Draw player
        self.player.draw(screen, self.camx)

        # HUD
        hud = self.font.render(f"Deaths: {self.deaths}/{DEATH_LIMIT}", True, (240, 240, 240))
        screen.blit(hud, (10, 10))
        y = 34
        if self.net_enabled:
            opp = self.font.render(
                f"Opponent deaths: {self.remote_deaths}/{DEATH_LIMIT}", True, (220, 210, 255)
            )
            screen.blit(opp, (10, y))
            y += 22
            status = "Racing"
            if self.remote_finished:
                status = f"Opponent {self.remote_finished}"
            elif not self.ghost_visible:
                status = "Opponent connecting..."
            status_surf = self.small.render(status, True, (210, 214, 223))
            screen.blit(status_surf, (10, y))

        if self._pending_outcome:
            panel = pygame.Surface((self.w, 90), pygame.SRCALPHA)
            pygame.draw.rect(panel, (0, 0, 0, 170), panel.get_rect())
            msg = "You win!" if self._pending_outcome == "win" else "You lost."
            if self._pending_outcome == "forfeit":
                msg = "Forfeit"
            txt = self.big.render(msg, True, (240, 240, 180))
            sub = self.small.render("Returning to arena...", True, (230, 230, 230))
            panel.blit(txt, ((panel.get_width() - txt.get_width()) // 2, 18))
            panel.blit(sub, ((panel.get_width() - sub.get_width()) // 2, 54))
            screen.blit(panel, (0, 48))

    def _queue_finish(self, outcome, winner=None, loser=None):
        if self._pending_outcome:
            return
        self.finished = outcome
        self._pending_outcome = outcome
        self.post_timer = POST_RESULT_S
        base_payload = {
            "deaths": self.deaths,
            "limit": DEATH_LIMIT,
            "final_x": self.player.x,
            "forfeit": self.forfeited,
        }
        if self.net_enabled:
            if winner is None and loser is None:
                if outcome == "win":
                    winner, loser = self.local_id, self.remote_id
                else:
                    winner, loser = self.remote_id, self.local_id
            self.winner_id, self.loser_id = winner, loser
            base_payload["winner"] = winner
            base_payload["loser"] = loser
            self._net_send_state(
                kind="finish",
                force=True,
                outcome=outcome,
                winner=winner,
                loser=loser,
                deaths=self.deaths,
                pos=[self.player.x, self.player.y],
            )
        self.pending_payload = base_payload

    # ---------- Networking ----------
    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[PlatformRace] Failed to send action: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        if not force and self.net_timer < self.net_interval:
            return
        self.net_timer = 0.0
        payload = {
            "kind": kind,
            "pos": [self.player.x, self.player.y],
            "vel": [self.player.vx, self.player.vy],
            "deaths": self.deaths,
            "finished": bool(self.finished),
            "outcome": self._pending_outcome,
        }
        payload.update(extra or {})
        self._net_send_action(payload)

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
            self._apply_remote_action(action)

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind in ("state", "init", "respawn"):
            pos = action.get("pos")
            if pos and len(pos) == 2:
                try:
                    self.ghost.x = float(pos[0])
                    self.ghost.y = float(pos[1])
                    self.ghost_visible = True
                    self.ghost_last_seen = time.perf_counter()
                except Exception:
                    pass
            deaths = action.get("deaths")
            if deaths is not None:
                try:
                    self.remote_deaths = int(deaths)
                except Exception:
                    pass
            finished = action.get("finished")
            if finished:
                self.remote_finished = "finished" if finished is True else finished
            return
        if kind == "finish":
            win_id = action.get("winner")
            lose_id = action.get("loser")
            outcome = action.get("outcome")
            pos = action.get("pos")
            if pos and len(pos) == 2:
                try:
                    self.ghost.x = float(pos[0])
                    self.ghost.y = float(pos[1])
                    self.ghost_visible = True
                    self.ghost_last_seen = time.perf_counter()
                except Exception:
                    pass
            mapped = outcome
            if win_id or lose_id:
                if win_id == self.local_id:
                    mapped = "win"
                elif lose_id == self.local_id:
                    mapped = "lose"
            mapped = mapped or "lose"
            if not self.pending_payload:
                self.pending_payload = {
                    "deaths": self.deaths,
                    "limit": DEATH_LIMIT,
                    "final_x": self.player.x,
                    "forfeit": self.forfeited,
                }
            self.pending_payload["winner"] = win_id
            self.pending_payload["loser"] = lose_id
            self.remote_finished = outcome or "finished"
            self.finished = mapped
            self._pending_outcome = mapped
            self.post_timer = POST_RESULT_S
            return

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[PlatformRace] Pause menu unavailable: {exc}")
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
                "deaths": self.deaths,
                "limit": DEATH_LIMIT,
                "final_x": self.player.x,
                "forfeit": self.forfeited,
            }
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        try:
            self.manager.pop()
        except Exception as exc:
            print(f"[PlatformRace] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[PlatformRace] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self._pending_outcome:
            self._finalize(self._pending_outcome)
            return
        self.forfeited = True
        self._queue_finish("forfeit")
        self._finalize("forfeit")


def launch(manager, context=None, callback=None, **kwargs):
    return PlatformRaceScene(manager, context, callback, **kwargs)
