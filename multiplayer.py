import asyncio
import copy
import json
import threading
import uuid
import random
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
import queue
import pygame
from pathlib import Path

from game_context import GameContext
from arena_scene import ArenaScene
from scene_manager import Scene
from sound_engine import load_map_profile
from minigames.shared import discover_multiplayer_minigames, pick_minigame_wheel, load_minigame_multiplayer
from minigame_loader import load_minigame_module
from resource_path import resource_path

# Track the currently running lobby so reopening host menus does not create duplicates.
ACTIVE_LOBBY_SERVER = None
ACTIVE_LOBBY_CLIENTS = []


@dataclass
class LobbyPlayer:
    player_id: str
    name: str
    ready: bool = False
    char_name: str = "classic"

    def to_dict(self):
        return asdict(self)


@dataclass
class LobbyState:
    map_name: str = "test_arena"
    mode: str = "tournament"
    allow_npc: bool = False
    host_id: Optional[str] = None
    players: List[LobbyPlayer] = field(default_factory=list)

    def to_dict(self):
        return {
            "map_name": self.map_name,
            "mode": self.mode,
            "allow_npc": self.allow_npc,
            "host_id": self.host_id,
            "players": [p.to_dict() for p in self.players],
        }


class LobbyServer:
    """Asyncio-based lightweight lobby server running in its own thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.loop = asyncio.new_event_loop()
        self.thread: Optional[threading.Thread] = None
        self.server: Optional[asyncio.base_events.Server] = None
        self.state = LobbyState()
        self._lock = threading.Lock()
        self._players: Dict[str, LobbyPlayer] = {}
        self._clients: Dict[str, asyncio.StreamWriter] = {}
        self._start_event = threading.Event()
        self._stop_event = threading.Event()
        self.events = queue.Queue()
        self.running = False
        # Match runtime state
        self.match_active = False
        self.match_task: Optional[asyncio.Task] = None
        self.match_players: Dict[str, Dict] = {}
        self.match_inputs: Dict[str, Dict[str, float]] = {}
        self.match_tick = 0
        self.eliminated_humans: set[str] = set()
        self.map_bounds = (960, 540)
        self.player_radius = 12.0
        self.map_colliders: List[pygame.Rect] = []
        self.match_seed = uuid.uuid4().hex
        self.duel_active = False
        self.duel_participants: List[str] = []
        self.duel_cooldown = 0.0
        self.duel_timeout = 0.0
        self.auto_duel_timer = 0.0
        self.auto_duel_interval = (3.0, 5.0)
        # Discover multiplayer-enabled minigames (falls back to a validated list).
        self.available_minigames = discover_multiplayer_minigames(Path(resource_path("minigames")))
        self.pending_duels: Dict[str, Dict] = {}
        self._duel_resolve_timeout = 60.0
        self.pending_duel_requests: Dict[str, Dict] = {}
        self.eliminated: set[str] = set()
        self.allow_join_during_match = True
        self.allow_join_in_lobby = True
        self.server_meta: Optional[Dict] = None

    def start(self) -> bool:
        if self.running:
            return True
        # Shut down any lingering global lobby before starting a new one.
        stop_active_lobby()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        ready = self._start_event.wait(timeout=3)
        self.running = ready
        if ready:
            global ACTIVE_LOBBY_SERVER
            ACTIVE_LOBBY_SERVER = self
        return ready

    def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        if self.match_task:
            self.match_active = False
            try:
                self.match_task.cancel()
            except Exception:
                pass
        if self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=3)
        self.thread = None
        self.running = False
        global ACTIVE_LOBBY_SERVER
        if ACTIVE_LOBBY_SERVER is self:
            ACTIVE_LOBBY_SERVER = None

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._start_server())
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            try:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self.loop.close()

    async def _start_server(self):
        try:
            self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
            self._log(f"Lobby server listening on {self.host}:{self.port}")
        except Exception as exc:
            self._log(f"Failed to start lobby server: {exc}")
            self._start_event.set()
            return
        self._start_event.set()
        async with self.server:
            await self.server.serve_forever()

    async def _shutdown(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for writer in list(self._clients.values()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        self._players.clear()
        with self._lock:
            self.state.players.clear()
        self._log("Lobby server stopped.")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self.match_active:
            if not self.allow_join_during_match:
                try:
                    await self._send(writer, {"type": "reject", "reason": "match_active"})
                except Exception:
                    pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return
        elif not self.allow_join_in_lobby:
            try:
                await self._send(writer, {"type": "reject", "reason": "lobby_locked"})
            except Exception:
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return
        player_id = str(uuid.uuid4())
        peer = writer.get_extra_info("peername")
        with self._lock:
            player = LobbyPlayer(player_id=player_id, name=f"Player {len(self.state.players) + 1}")
            self.state.players.append(player)
            self._players[player_id] = player
            self._clients[player_id] = writer
            if not self.state.host_id:
                self.state.host_id = player_id
        self._log(f"Client connected: {peer} -> {player_id}")
        await self._send(writer, {"type": "welcome", "player_id": player_id, "state": self.snapshot_state()})
        await self._broadcast_state()
        try:
            while not reader.at_eof():
                try:
                    data = await reader.readline()
                except ConnectionResetError:
                    break
                if not data:
                    break
                try:
                    payload = json.loads(data.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                await self._handle_message(player_id, payload)
        except ConnectionResetError:
            pass
        except Exception as exc:
            self._log(f"Client handler error for {player_id}: {exc}")
        finally:
            await self._remove_player(player_id)
            self._log(f"Client disconnected: {player_id}")

    async def _handle_message(self, player_id: str, payload: Dict):
        msg_type = payload.get("type")
        if msg_type == "hello":
            name = (payload.get("name") or "").strip() or "Player"
            with self._lock:
                player = self._players.get(player_id)
                if player:
                    player.name = name[:24]
            await self._broadcast_state()
        elif msg_type == "set_ready":
            ready = bool(payload.get("ready"))
            with self._lock:
                player = self._players.get(player_id)
                if player:
                    player.ready = ready
            await self._broadcast_state()
        elif msg_type == "set_map":
            # Multiplayer is locked to test_arena for now.
            return
        elif msg_type == "set_mode":
            # Multiplayer is locked to tournament for now.
            return
        elif msg_type == "set_allow_npc":
            # NPC fill disabled for now; keep state false and ignore.
            with self._lock:
                self.state.allow_npc = False
            await self._broadcast_state()
        elif msg_type == "set_char":
            char_name = (payload.get("char_name") or "").strip()
            if not char_name:
                return
            with self._lock:
                player = self._players.get(player_id)
                if player:
                    player.char_name = char_name[:32]
            await self._broadcast_state()
        elif msg_type in ("match_input", "input"):
            if not self.match_active:
                return
            vec = payload.get("vec") or payload
            try:
                vx = float(vec.get("x", 0.0))
                vy = float(vec.get("y", 0.0))
            except Exception:
                vx = vy = 0.0
            self.match_inputs[player_id] = {"x": max(-1.0, min(1.0, vx)), "y": max(-1.0, min(1.0, vy))}
            if vx or vy:
                self._log(f"Input from {player_id}: {vx:.2f},{vy:.2f}")
        elif msg_type == "start_minigame":
            # Host validates and rebroadcasts to all.
            if not self.match_active:
                return
            mg = (payload.get("minigame") or "").strip()
            participants = payload.get("participants") or []
            if not mg or not participants:
                return
            duel_id = payload.get("duel_id") or uuid.uuid4().hex
            await self._broadcast(
                {
                    "type": "start_minigame",
                    "minigame": mg,
                    "participants": participants,
                    "duel_id": duel_id,
                }
            )
        elif msg_type == "debug_start_duel":
            # Host-only manual trigger for testing.
            if not self.match_active or self.state.host_id != player_id:
                return
            target = payload.get("target")
            # pick target if not provided
            pid_list = [pid for pid in self.match_players.keys() if pid != player_id]
            if target not in pid_list:
                if not pid_list:
                    return
                target = pid_list[0]
            self._start_duel(player_id, target)
        elif msg_type == "request_duel":
            # Ignore requests targeting NPCs that are busy in a pseudo-duel.
            target = payload.get("target")
            if str(target).startswith("npc-") and target in getattr(self, "npc_busy", {}):
                return
            await self._handle_duel_request(player_id, payload)
        elif msg_type == "duel_result":
            await self._handle_duel_result(player_id, payload)
        elif msg_type == "duel_choice":
            await self._handle_duel_choice(player_id, payload)
        elif msg_type == "duel_action":
            # Generic per-duel action relay for multiplayer minigames.
            duel_id = payload.get("duel_id")
            if not duel_id or duel_id not in self.pending_duels:
                return
            duel = self.pending_duels[duel_id]
            if player_id not in duel.get("participants", []):
                return
            await self._broadcast({"type": "duel_action", "from": player_id, **payload})
        elif msg_type == "minigame_result":
            # For now just broadcast the result; later we can update scores.
            await self._broadcast({"type": "minigame_result", **payload})
        elif msg_type == "start_match":
            # Only host can initiate start.
            with self._lock:
                if self.state.host_id != player_id:
                    return
                seed = payload.get("seed") or uuid.uuid4().hex
                player_copy = [p.to_dict() for p in self.state.players]
                # Lock multiplayer to test_arena/tournament.
                map_name = "test_arena"
                map_json = Path(resource_path("maps", map_name, "map.json"))
                spawns = self._build_spawns_from_map(map_json, seed, len(player_copy))
                # assign player_ids to spawn slots deterministically
                for idx, p in enumerate(player_copy):
                    if idx < len(spawns):
                        spawns[idx]["player_id"] = p["player_id"]
                    else:
                        spawns.append({"player_id": p["player_id"], "pos": spawns[0]["pos"]})
                allow_npc = False
                match = {
                    "map": map_name,
                    "mode": "tournament",
                    "seed": seed,
                    "allow_npc": allow_npc,
                    "players": player_copy,
                    "spawns": spawns,
                }
            await self._broadcast({"type": "start_match", "match": match})
            await self._begin_match(match, map_json)

    async def _remove_player(self, player_id: str):
        with self._lock:
            player = self._players.pop(player_id, None)
            if player and player in self.state.players:
                self.state.players.remove(player)
        if player_id in self.match_players:
            self.match_players.pop(player_id, None)
        if player_id in self.match_inputs:
            self.match_inputs.pop(player_id, None)
        writer = self._clients.pop(player_id, None)
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        await self._broadcast_state()

    async def _send(self, writer: asyncio.StreamWriter, payload: Dict):
        try:
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
        except ConnectionError:
            pass

    async def _broadcast_state(self):
        snapshot = self.snapshot_state()
        message = {"type": "lobby_state", "state": snapshot}
        await self._broadcast(message)

    async def _broadcast(self, payload: Dict):
        for writer in list(self._clients.values()):
            await self._send(writer, payload)

    def snapshot_state(self) -> Dict:
        with self._lock:
            snapshot = self.state.to_dict()
            if self.server_meta is not None:
                snapshot["server_meta"] = dict(self.server_meta)
            return snapshot

    def pop_events(self) -> List[str]:
        msgs = []
        while True:
            try:
                msgs.append(self.events.get_nowait())
            except queue.Empty:
                break
        return msgs

    def _log(self, message: str):
        self.events.put(message)

    def _build_spawns_from_map(self, map_json: Path, seed: str, count: int, as_dict=True):
        """Replicate perimeter spawn logic used in single-player tournament."""
        try:
            with open(map_json, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
        tile = int(data.get("tileSize", 16))
        w = int(data.get("mapWidth", 0)) * tile
        h = int(data.get("mapHeight", 0)) * tile
        if w <= 0 or h <= 0:
            w = h = 640
        margin = 96
        minx = margin
        maxx = max(margin + tile, w - margin)
        miny = margin
        maxy = max(margin + tile, h - margin)
        edges = [
            ((minx, miny), (maxx, miny)),  # top
            ((maxx, miny), (maxx, maxy)),  # right
            ((maxx, maxy), (minx, maxy)),  # bottom
            ((minx, maxy), (minx, miny)),  # left
        ]
        per_edge = [count // 4] * 4
        for i in range(count % 4):
            per_edge[i] += 1

        pts = []
        for edge_idx, ((x1, y1), (x2, y2)) in enumerate(edges):
            slots = per_edge[edge_idx]
            if slots <= 0:
                continue
            for s in range(slots):
                t = (s + 0.5) / slots
                px = int(round(x1 + (x2 - x1) * t))
                py = int(round(y1 + (y2 - y1) * t))
                pts.append((px, py))

        rng = random.Random(seed)
        rng.shuffle(pts)
        pts = pts[:count] if len(pts) >= count else (pts * ((count // len(pts)) + 1))[:count]
        if not as_dict:
            return pts
        return [{"player_id": None, "pos": pt} for pt in pts]

    async def _begin_match(self, match: Dict, map_json: Path):
        """Initialize match state and spin a lightweight host tick loop."""
        self.match_active = False
        if self.match_task:
            try:
                self.match_task.cancel()
            except Exception:
                pass
        self.match_players.clear()
        self.match_inputs.clear()
        self.eliminated.clear()
        self.eliminated_humans.clear()
        self.match_tick = 0
        self.map_bounds = self._load_map_bounds(map_json)
        self.map_colliders = self._load_map_colliders(map_json)
        self.match_seed = match.get("seed") or self.match_seed
        self.duel_active = False
        self.duel_participants = []
        self.duel_cooldown = 2.0
        self.duel_timeout = 0.0
        self.auto_duel_timer = 2.5
        # Safe zone (match-wide circle) similar to TournamentController.
        self.safe_center = (self.map_bounds[0] * 0.5, self.map_bounds[1] * 0.5)
        self.safe_radius = int(max(self.map_bounds[0], self.map_bounds[1]) * 0.75)
        self.safe_radius_min = max(220, min(self.map_bounds[0], self.map_bounds[1]) // 3)
        self.shrink_rate = 8.0
        self.shrink_delay = 8.0
        self.shrink_elapsed = 0.0
        # NPC vs NPC pseudo minigame state
        self.npc_busy: Dict[str, Dict] = {}
        self.npc_duel_cooldown = 0.0
        self.npc_idle_timers: Dict[str, float] = {}
        spawns = match.get("spawns", [])
        self._log(f"Match initialized with {len(spawns)} spawns.")
        players = {p["player_id"]: p for p in match.get("players", [])}
        for spawn in spawns:
            pid = spawn.get("player_id") or f"npc-{len(self.match_players)}"
            pos = spawn.get("pos") or (0, 0)
            info = players.get(pid, {})
            self.match_players[pid] = {
                "pos": [float(pos[0]), float(pos[1])],
                "vel": [0.0, 0.0],
                "char": info.get("char_name"),
                "npc": bool(spawn.get("npc", False)),
                "name": info.get("name") or ("NPC" if spawn.get("npc") else "Player"),
            }
            self.match_inputs[pid] = {"x": 0.0, "y": 0.0}
        self.match_active = True
        self.match_task = asyncio.create_task(self._match_loop())

    def _load_map_bounds(self, map_json: Path):
        try:
            with open(map_json, "r") as f:
                data = json.load(f)
            tile = int(data.get("tileSize", 16))
            w = int(data.get("mapWidth", 0)) * tile
            h = int(data.get("mapHeight", 0)) * tile
            if w > 0 and h > 0:
                return (w, h)
        except Exception:
            pass
        return self.map_bounds

    def _load_map_colliders(self, map_json: Path):
        rects = []
        try:
            with open(map_json, "r") as f:
                data = json.load(f)
            tile = int(data.get("tileSize", 16))
            for layer in data.get("layers", []):
                if not layer.get("collider"):
                    continue
                for t in layer.get("tiles", []):
                    x = int(t.get("x", 0)) * tile
                    y = int(t.get("y", 0)) * tile
                    w = int(t.get("w", tile))
                    h = int(t.get("h", tile))
                    rects.append(pygame.Rect(x, y, w, h))
        except Exception:
            rects = []
        return rects

    async def _match_loop(self):
        target_dt = 1.0 / 15.0
        last = time.time()
        while self.match_active and not self._stop_event.is_set():
            now = time.time()
            dt = max(0.0, min(0.2, now - last))
            last = now
            self._step_match(dt)
            snapshot = self._build_match_snapshot(now)
            await self._broadcast({"type": "match_state", "state": snapshot})
            self.match_tick += 1
            await asyncio.sleep(target_dt)

    def _step_match(self, dt: float):
        # Base run speed for humans/NPCs; lowered for smoother pacing.
        speed = 110.0
        maxx, maxy = self.map_bounds
        # Clear any stale duels so actors don't stay frozen.
        self._cleanup_stale_duels()
        # Force-resolve any duels that already have a winner/complete results.
        self._resolve_ready_duels()
        # Safety: if no duel is pending or participants vanished, clear duel state so actors aren't frozen.
        if self.duel_participants:
            self.duel_participants = [p for p in self.duel_participants if p in self.match_players]
        if self.duel_active and (not self.pending_duels or not self.duel_participants):
            self.duel_active = False
            self.duel_participants = []
        self.duel_cooldown = max(0.0, self.duel_cooldown - dt)
        if self.duel_timeout > 0:
            self.duel_timeout -= dt
            if self.duel_timeout <= 0:
                self.duel_active = False
                self.duel_participants = []
        # expire stale duel requests
        self._expire_duel_requests()
        # Update safe zone shrink to stay aligned with client view.
        self._update_safe_zone(dt)
        to_eliminate = []
        for pid, state in self.match_players.items():
            frozen = False
            # Freeze duel participants only while their duel is actually active/unresolved.
            if self.duel_active and pid in self.duel_participants and self._is_pid_in_active_duel(pid):
                state["vel"] = [0.0, 0.0]
                frozen = True
            # Freeze NPCs that are in a pseudo-duel.
            busy = self.npc_busy.get(pid)
            if busy:
                state["vel"] = [0.0, 0.0]
                frozen = True
            if state.get("npc") and not frozen:
                # Bots orbit toward a personal anchor near the center to avoid clumping at the exact middle.
                cx, cy = maxx * 0.5, maxy * 0.5
                safe_r = self.safe_radius if self.safe_radius else 0.75 * min(maxx, maxy)
                wander = state.setdefault("wander", {"timer": 0.0})
                if "angle" not in wander:
                    wander["angle"] = random.uniform(0, math.tau)
                if "radius" not in wander:
                    wander["radius"] = 0.35 * min(maxx, maxy) + random.uniform(-30, 60)
                wander["timer"] -= dt
                if wander["timer"] <= 0:
                    wander["angle"] += random.uniform(-0.22, 0.22)
                    desired_r = min(0.65 * safe_r, 0.45 * min(maxx, maxy))
                    wander["radius"] = max(80.0, min(desired_r, wander["radius"] + random.uniform(-18, 18)))
                    wander["timer"] = random.uniform(1.8, 3.2)
                tx = cx + math.cos(wander["angle"]) * wander["radius"]
                ty = cy + math.sin(wander["angle"]) * wander["radius"]
                px, py = state["pos"]
                dx = tx - px
                dy = ty - py
                dist = math.hypot(dx, dy) or 1.0
                nx, ny = dx / dist, dy / dist
                # If near the boundary, bias inward a bit harder.
                dist_center = math.hypot(px - cx, py - cy) or 1.0
                if dist_center > safe_r * 0.88:
                    nx, ny = (cx - px) / dist_center, (cy - py) / dist_center
                jitter = random.uniform(-0.06, 0.06)
                sin_j = math.sin(jitter)
                cos_j = math.cos(jitter)
                vx = nx * cos_j - ny * sin_j
                vy = nx * sin_j + ny * cos_j
                mag = speed * 0.7 + random.uniform(-6, 10)
                target_v = [vx * mag, vy * mag]
                # Smooth acceleration to avoid jittery micro-movements.
                prev_vx, prev_vy = state.get("vel", (0.0, 0.0))
                blend = 0.12
                smoothed_vx = prev_vx * (1 - blend) + target_v[0] * blend
                smoothed_vy = prev_vy * (1 - blend) + target_v[1] * blend
                state["vel"] = [smoothed_vx, smoothed_vy]
                # If velocity is nearly zero, force a small re-aim to avoid stalls.
                if abs(state["vel"][0]) + abs(state["vel"][1]) < 6.0:
                    wander["timer"] = 0.0
                    state["vel"][0] += random.uniform(-1.0, 1.0) * 30
                    state["vel"][1] += random.uniform(-1.0, 1.0) * 30
                # Track idle time for cleanup; reset when moving.
                if abs(state["vel"][0]) + abs(state["vel"][1]) < 6.0:
                    self.npc_idle_timers[pid] = self.npc_idle_timers.get(pid, 0.0) + dt
                else:
                    self.npc_idle_timers[pid] = 0.0
            elif not state.get("npc"):
                inp = self.match_inputs.get(pid, {"x": 0.0, "y": 0.0})
                vx = float(inp.get("x", 0.0))
                vy = float(inp.get("y", 0.0))
                state["vel"] = [vx * speed, vy * speed]
            # axis-separated collision with map colliders
            rect = self._state_rect(state)
            move_x = state["vel"][0] * dt
            move_y = state["vel"][1] * dt
            if move_x:
                rect.x += int(round(move_x))
                for c in self.map_colliders:
                    if rect.colliderect(c):
                        if move_x > 0:
                            rect.right = c.left
                        else:
                            rect.left = c.right
            if move_y:
                rect.y += int(round(move_y))
                for c in self.map_colliders:
                    if rect.colliderect(c):
                        if move_y > 0:
                            rect.bottom = c.top
                        else:
                            rect.top = c.bottom
            rect.left = max(0, min(rect.left, maxx - rect.width))
            rect.top = max(0, min(rect.top, maxy - rect.height))
            state["pos"][0], state["pos"][1] = rect.midbottom
            # Boundary with grace period: eliminate any actor outside the safe circle after 5s.
            # Duel participants are immune while their duel is active.
            if not (self.duel_active and pid in self.duel_participants):
                if self.safe_center and self.safe_radius:
                    cx, cy = self.safe_center
                    radius = self.safe_radius
                else:
                    cx, cy = maxx * 0.5, maxy * 0.5
                    radius = 0.48 * min(maxx, maxy)
                dx = state["pos"][0] - cx
                dy = state["pos"][1] - cy
                tol = radius * 1.02
                outside = (dx * dx + dy * dy) > (tol * tol)
                timer = state.get("outside_timer", 0.0)
                timer = timer + dt if outside else 0.0
                state["outside_timer"] = timer
                if outside and timer >= 5.0:
                    to_eliminate.append(pid)
            # Idle fail-safe: remove NPCs that stay nearly still for too long.
            if state.get("npc"):
                idle_t = self.npc_idle_timers.get(pid, 0.0)
                if idle_t > 8.0:
                    to_eliminate.append(pid)
        self._update_duel_resolve()
        for pid in to_eliminate:
            self._eliminate_actor(pid)
        # Match-end conditions: last human standing or only NPCs remain (<=4).
        if self.match_active:
            alive_humans = [
                pid
                for pid, st in self.match_players.items()
                if not st.get("npc") and pid not in self.eliminated_humans
            ]
            alive_npcs = [
                pid for pid, st in self.match_players.items() if st.get("npc") and pid not in self.eliminated
            ]
            if len(alive_humans) == 1 and not alive_npcs:
                winner = alive_humans[0]
                snap = self._build_match_snapshot(time.time())
                snap["winner"] = winner
                snap["npc_winner"] = False
                asyncio.run_coroutine_threadsafe(
                    self._broadcast({"type": "match_state", "state": snap}), self.loop
                )
                self.match_active = False
                self.duel_active = False
                self.duel_participants = []
                return
            if len(alive_humans) == 0 and 0 < len(alive_npcs) <= 4:
                snap = self._build_match_snapshot(time.time())
                snap["winner"] = None
                snap["npc_winner"] = True
                asyncio.run_coroutine_threadsafe(
                    self._broadcast({"type": "match_state", "state": snap}), self.loop
                )
                self.match_active = False
                self.duel_active = False
                self.duel_participants = []
                return

    def _cleanup_stale_duels(self, max_age: float = 25.0):
        if not self.pending_duels:
            return
        now = time.time()
        stale_ids = []
        for did, duel in list(self.pending_duels.items()):
            parts = duel.get("participants") or []
            age = now - duel.get("start", now)
            # If any participant vanished from the match, drop it.
            missing = [p for p in parts if p not in self.match_players]
            if missing:
                stale_ids.append(did)
                continue
            # NPC duels: if they linger too long, force-resolve to avoid freezing actors.
            npc_involved = any(str(p).startswith("npc-") for p in parts)
            if npc_involved and age > 8.0:
                results = duel.get("results") or {}
                reporter = next(iter(results.keys()), None)
                outcome = (results.get(reporter) or {}).get("outcome") if reporter else None
                opp = [p for p in parts if p != reporter]
                opp_id = opp[0] if opp else None
                if outcome in ("win", "lose", "forfeit"):
                    if outcome == "win":
                        duel["forced_winner"] = reporter
                        duel["forced_loser"] = opp_id
                    else:
                        duel["forced_winner"] = opp_id
                        duel["forced_loser"] = reporter
                else:
                    # No report? pick a random winner to unblock.
                    rng = random.Random(f"stale-npc-{did}-{now}")
                    w = rng.choice(parts) if parts else None
                    l = [p for p in parts if p != w][0] if parts and len(parts) > 1 else None
                    duel["forced_winner"] = w
                    duel["forced_loser"] = l
                try:
                    asyncio.run_coroutine_threadsafe(self._resolve_duel(did, duel), self.loop)
                except Exception:
                    pass
        for did in stale_ids:
            self.pending_duels.pop(did, None)
        if stale_ids:
            self.duel_active = False
            self.duel_participants = []

    def _resolve_ready_duels(self):
        """Auto-resolve duels that already have a forced winner or complete results."""
        if not self.pending_duels:
            return
        for did, duel in list(self.pending_duels.items()):
            parts = duel.get("participants") or []
            # If already forced, finish it.
            if duel.get("forced_winner"):
                asyncio.run_coroutine_threadsafe(self._resolve_duel(did, duel), self.loop)
                continue
            # If all participants have reported outcomes and there is a single winner, finish it.
            outcomes = {pid: (duel["results"].get(pid) or {}).get("outcome") for pid in parts}
            winners = [pid for pid, out in outcomes.items() if out == "win"]
            losers = [pid for pid, out in outcomes.items() if out == "lose"]
            if len(winners) == 1:
                duel["forced_winner"] = winners[0]
                duel["forced_loser"] = losers[0] if losers else [p for p in parts if p != winners[0]][0]
                asyncio.run_coroutine_threadsafe(self._resolve_duel(did, duel), self.loop)
                continue
            # NPC duels: if we have any report at all, treat it as decisive to avoid freezing.
            npc_involved = any(str(p).startswith("npc-") for p in parts)
            if npc_involved and duel.get("results"):
                reporter = next(iter(duel["results"].keys()))
                outcome = (duel["results"][reporter] or {}).get("outcome")
                opp = [p for p in parts if p != reporter]
                opp_id = opp[0] if opp else None
                if outcome in ("win", "lose", "forfeit"):
                    if outcome == "win":
                        duel["forced_winner"] = reporter
                        duel["forced_loser"] = opp_id
                    else:
                        duel["forced_winner"] = opp_id
                        duel["forced_loser"] = reporter
                    asyncio.run_coroutine_threadsafe(self._resolve_duel(did, duel), self.loop)

    def _is_pid_in_active_duel(self, pid: str) -> bool:
        """Return True only if pid is in a duel that is still unresolved."""
        for duel in self.pending_duels.values():
            parts = duel.get("participants") or []
            if pid not in parts:
                continue
            if duel.get("forced_winner"):
                continue
            # If all participants reported results, treat as resolved.
            if all(p in duel.get("results", {}) for p in parts):
                continue
            return True
        return False

    def _update_safe_zone(self, dt: float):
        if not self.safe_center or not self.safe_radius:
            return
        self.shrink_elapsed += dt
        if self.shrink_elapsed < self.shrink_delay:
            return
        if self.safe_radius > self.safe_radius_min:
            self.safe_radius = max(self.safe_radius_min, self.safe_radius - self.shrink_rate * dt)

    def _update_npc_pseudo_duels(self, dt: float):
        """Host-only lightweight NPC vs NPC pseudo minigames."""
        if not self.match_active:
            return
        now = time.time()
        # Advance busy timers and resolve when done.
        finished = []
        for pid, info in list(self.npc_busy.items()):
            end_at = info.get("end_at", 0)
            opp = info.get("opponent")
            # If opponent vanished, clear this busy entry.
            if opp not in self.match_players:
                finished.append((pid, info))
                continue
            if now >= end_at:
                finished.append((pid, info))
        for pid, info in finished:
            opponent = info.get("opponent")
            rng = random.Random(f"{self.match_seed}-{self.match_tick}-{pid}-{opponent}-{info.get('start_at')}")
            winner = rng.choice([pid, opponent]) if opponent else pid
            loser = opponent if winner == pid else pid
            if loser:
                self._eliminate_actor(loser)
            if pid in self.npc_busy:
                self.npc_busy.pop(pid, None)
            if opponent and opponent in self.npc_busy:
                self.npc_busy.pop(opponent, None)
        # Cooldown before starting new pseudo duels.
        self.npc_duel_cooldown = max(0.0, self.npc_duel_cooldown - dt)
        if self.npc_duel_cooldown > 0.0:
            return
        # Attempt to start a pseudo duel between nearby NPCs.
        npc_ids = [pid for pid, st in self.match_players.items() if st.get("npc") and pid not in self.npc_busy]
        for i in range(len(npc_ids)):
            for j in range(i + 1, len(npc_ids)):
                a, b = npc_ids[i], npc_ids[j]
                pa = self.match_players.get(a)
                pb = self.match_players.get(b)
                if not pa or not pb:
                    continue
                dx = pa["pos"][0] - pb["pos"][0]
                dy = pa["pos"][1] - pb["pos"][1]
                if (dx * dx + dy * dy) <= (42.0 * 42.0):
                    # Random chance to start when they pass.
                    rng = random.Random(f"{self.match_seed}-{self.match_tick}-{a}-{b}")
                    if rng.random() < 0.20:
                        dur = rng.uniform(20.0, 35.0)
                        end_at = now + dur
                        self.npc_busy[a] = {"opponent": b, "start_at": now, "end_at": end_at}
                        self.npc_busy[b] = {"opponent": a, "start_at": now, "end_at": end_at}
                        self.npc_duel_cooldown = 5.0
                        return

    def _state_rect(self, state: Dict):
        rect = pygame.Rect(0, 0, 10, 6)
        rect.midbottom = (int(state["pos"][0]), int(state["pos"][1]))
        return rect

    def _try_start_duel(self):
        if (
            not self.match_active
            or self.duel_active
            or self.pending_duels
            or self.duel_cooldown > 0
        ):
            return False
        duel_radius = 44.0
        npc_busy = getattr(self, "npc_busy", {})
        eligible = []
        for pid, st in self.match_players.items():
            if pid in self.eliminated or (not st.get("npc") and pid in self.eliminated_humans):
                continue
            if pid in npc_busy:
                continue
            eligible.append(pid)
        if len(eligible) < 2:
            return False
        best_pair = None
        best_d2 = float("inf")
        best_has_human = False
        for i in range(len(eligible)):
            for j in range(i + 1, len(eligible)):
                a = eligible[i]
                b = eligible[j]
                pa = self.match_players.get(a)
                pb = self.match_players.get(b)
                if not pa or not pb:
                    continue
                dx = pa["pos"][0] - pb["pos"][0]
                dy = pa["pos"][1] - pb["pos"][1]
                d2 = dx * dx + dy * dy
                has_human = (not pa.get("npc")) or (not pb.get("npc"))
                if has_human and not best_has_human:
                    best_pair = (a, b)
                    best_d2 = d2
                    best_has_human = True
                elif has_human == best_has_human and d2 < best_d2:
                    best_d2 = d2
                    best_pair = (a, b)
        if not best_pair or best_d2 > duel_radius * duel_radius:
            return False
        self._log(f"Auto-starting duel {best_pair[0]} vs {best_pair[1]} (proximity)")
        self._start_duel(best_pair[0], best_pair[1])
        return True

    async def _handle_duel_request(self, player_id: str, payload: Dict):
        """First player requests a duel; second confirms to start."""
        if not self.match_active or not self.match_players:
            return
        if self.duel_active or self.pending_duels or self.duel_cooldown > 0:
            return
        target = payload.get("target")
        if not target or target == player_id:
            return
        if target not in self.match_players:
            return
        # NPC opponents auto-accept.
        if str(target).startswith("npc-"):
            self._start_duel(player_id, target)
            return
        pair_key = tuple(sorted([player_id, target]))
        now = time.time()
        record = self.pending_duel_requests.get(pair_key)
        # If the target already requested this pair recently, start duel.
        if record and record.get("initiator") == target and now - record.get("ts", 0) < 10.0:
            self.pending_duel_requests.pop(pair_key, None)
            self._start_duel(player_id, target)
            return
        # Otherwise store request and notify the target.
        self.pending_duel_requests[pair_key] = {"initiator": player_id, "target": target, "ts": now}
        asyncio.run_coroutine_threadsafe(
            self._broadcast({"type": "duel_request", "from": player_id, "to": target}), self.loop
        )

    def _expire_duel_requests(self, ttl: float = 10.0):
        now = time.time()
        stale = [k for k, v in self.pending_duel_requests.items() if now - v.get("ts", now) > ttl]
        for k in stale:
            self.pending_duel_requests.pop(k, None)

    def _start_duel(self, pid_a: str, pid_b: str):
        rng = random.Random(f"{self.match_seed}-{self.match_tick}-{pid_a}-{pid_b}")
        wheel = pick_minigame_wheel(
            rng,
            self.available_minigames,
            base_dir=Path(resource_path("minigames")),
        )
        if not wheel:
            wheel = list(self.available_minigames)
        selected_entry = rng.choice(wheel) if wheel else "rps_duel"
        mp_module = load_minigame_multiplayer(selected_entry, Path(resource_path("minigames")))
        payload = {
            "type": "start_duel",
            "participants": [pid_a, pid_b],
            "wheel_entries": wheel,
            "wheel_spin_seed": rng.random(),
            "selected_entry": selected_entry,
        }
        self.duel_active = True
        self.duel_participants = [pid_a, pid_b]
        duel_id = uuid.uuid4().hex
        duel_record = {
            "participants": [pid_a, pid_b],
            "wheel": wheel,
            "selected": selected_entry,
            "results": {},
            "start": time.time(),
            "scores": {pid_a: 0, pid_b: 0},
            "round": 1,
            "round_entries": {},
            "round_first_choice_at": None,
        }
        # Auto-mark NPC participants so we can resolve without their input.
        for pid in (pid_a, pid_b):
            if pid.startswith("npc-"):
                auto_entry = selected_entry
                if mp_module and hasattr(mp_module, "ai_choice"):
                    try:
                        auto_entry = mp_module.ai_choice(self.match_seed, duel_record.get("round", 1), duel_record["participants"])
                    except Exception:
                        auto_entry = selected_entry
                elif selected_entry == "rps_duel":
                    auto_entry = random.choice(["rock", "paper", "scissors"])
                duel_record["results"][pid] = {"entry": auto_entry, "outcome": "npc"}
        self.pending_duels[duel_id] = duel_record
        self.duel_cooldown = 2.5
        self.duel_timeout = 10.0
        for key in list(self.pending_duel_requests.keys()):
            if pid_a in key or pid_b in key:
                self.pending_duel_requests.pop(key, None)
        self._log(f"Starting duel {pid_a} vs {pid_b} with wheel {wheel}")
        # Reset out-of-circle timers for duel participants so they don't get culled right after the minigame.
        for pid in (pid_a, pid_b):
            if pid in self.match_players:
                self.match_players[pid]["outside_timer"] = 0.0
        asyncio.run_coroutine_threadsafe(self._broadcast({**payload, "duel_id": duel_id}), self.loop)

    async def _handle_duel_result(self, player_id: str, payload: Dict):
        duel_id = payload.get("duel_id")
        if not duel_id or duel_id not in self.pending_duels:
            # Failsafe: if we get a decisive result for an unknown duel, honor it to keep the match flowing.
            winner = payload.get("winner")
            loser = payload.get("loser")
            if winner or loser:
                out = {
                    "type": "duel_result",
                    "duel_id": duel_id or uuid.uuid4().hex,
                    "winner": winner,
                    "loser": loser,
                    "entries": [payload.get("entry")],
                }
                self._log(f"[Duel] Failsafe resolving unknown duel {duel_id}: winner={winner} loser={loser}")
                await self._broadcast(out)
                if loser:
                    self._eliminate_actor(loser)
                self.duel_active = False
                self.duel_participants = []
            return
        duel = self.pending_duels[duel_id]
        participants = duel.get("participants") or []
        if player_id not in participants:
            # Ignore stray results not from duel participants.
            return
        entry = payload.get("entry")
        result = payload.get("outcome") or "ack"
        winner_field = payload.get("winner")
        loser_field = payload.get("loser")
        duel["results"][player_id] = {"entry": entry, "outcome": result}
        npc_involved = any(str(p).startswith("npc-") for p in participants)
        # If this duel involves an NPC, resolve as soon as a decisive outcome is reported.
        if npc_involved and result in ("win", "lose", "forfeit"):
            opp = [p for p in participants if p != player_id]
            opp_id = opp[0] if opp else None
            if result == "win":
                duel["forced_winner"] = player_id
                duel["forced_loser"] = opp_id
            else:
                duel["forced_winner"] = opp_id
                duel["forced_loser"] = player_id
            await self._resolve_duel(duel_id, duel)
            return
        # If a winner/loser was explicitly provided by the minigame, honor it immediately.
        if not duel.get("forced_winner") and winner_field:
            duel["forced_winner"] = winner_field
            duel["forced_loser"] = loser_field
        # For non-RPS duels, allow a single decisive result to force resolution (helps NPC opponents).
        if not duel.get("forced_winner") and result in ("win", "lose", "forfeit"):
            if len(participants) >= 2:
                opponent = [p for p in participants if p != player_id]
                opp = opponent[0] if opponent else None
                if opp:
                    if result == "win":
                        duel["forced_winner"] = player_id
                        duel["forced_loser"] = opp
                    elif result in ("lose", "forfeit"):
                        duel["forced_winner"] = opp
                        duel["forced_loser"] = player_id
        # For rps, wait for round-based resolution; don't auto-resolve on raw results.
        if duel.get("selected") == "rps_duel":
            # If a minigame reports a clear win/lose, use it to force resolution once.
            if not duel.get("forced_winner") and result in ("win", "lose"):
                participants = duel.get("participants") or []
                if len(participants) >= 2:
                    opponent = [p for p in participants if p != player_id]
                    opp = opponent[0] if opponent else None
                    if opp:
                        if result == "win":
                            duel["forced_winner"] = player_id
                            duel["forced_loser"] = opp
                        elif result == "lose":
                            duel["forced_winner"] = opp
                            duel["forced_loser"] = player_id
            # If still no forced winner, wait for round resolution.
            if not duel.get("forced_winner"):
                return
        # When all participants reported, pick a winner using outcomes when possible.
        if all(p in duel["results"] for p in duel["participants"]):
            outcomes = {pid: (duel["results"].get(pid) or {}).get("outcome") for pid in duel["participants"]}
            winners = [pid for pid, out in outcomes.items() if out == "win"]
            losers = [pid for pid, out in outcomes.items() if out == "lose"]
            if len(winners) == 1 and len(losers) >= 1:
                duel["forced_winner"] = winners[0]
                # pick first loser that is not winner
                duel["forced_loser"] = losers[0]
        # If we have a forced winner at this point, resolve immediately.
        if duel.get("forced_winner"):
            await self._resolve_duel(duel_id, duel)
            return

    async def _handle_duel_choice(self, player_id: str, payload: Dict):
        """Per-round choice handling for RPS best-of-3 duels."""
        duel_id = payload.get("duel_id")
        choice = (payload.get("entry") or "").strip().lower()
        if not duel_id or duel_id not in self.pending_duels:
            return
        duel = self.pending_duels[duel_id]
        if duel.get("selected") != "rps_duel":
            return
        participants = duel.get("participants") or []
        if player_id not in participants:
            return
        duel.setdefault("round_entries", {})[player_id] = choice
        if not duel.get("round_first_choice_at"):
            duel["round_first_choice_at"] = time.time()
        self._log(f"[Duel] choice {duel_id} {player_id} -> {choice}")
        # Auto-pick for NPC opponents so rounds resolve.
        for pid in participants:
            if pid.startswith("npc-") and pid not in duel["round_entries"]:
                duel["round_entries"][pid] = random.choice(["rock", "paper", "scissors"])
        # Wait until both entries are present.
        if any(p not in duel["round_entries"] for p in participants):
            return
        # Resolve the round.
        beats = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
        a, b = participants[0], participants[1]
        ca = duel["round_entries"].get(a)
        cb = duel["round_entries"].get(b)
        winner = None
        if ca in beats and cb in beats:
            if ca == cb:
                winner = None  # tie: no score change
            elif beats[ca] == cb:
                winner = a
            else:
                winner = b
        duel.setdefault("scores", {a: 0, b: 0})
        if winner:
            duel["scores"][winner] = duel["scores"].get(winner, 0) + 1
        round_no = duel.get("round", 1)
        round_payload = {
            "type": "duel_round_result",
            "duel_id": duel_id,
            "round": round_no,
            "choices": {a: ca, b: cb},
            "winner": winner,
            "scores": duel["scores"],
        }
        self._log(f"[Duel] round_result {duel_id} r{round_no} choices={round_payload['choices']} winner={winner} scores={duel['scores']}")
        await self._broadcast(round_payload)
        duel["round"] = round_no + 1
        duel["round_entries"] = {}
        duel["round_first_choice_at"] = None
        # Check for match end (best of 3).
        match_winner = None
        for pid, score in duel["scores"].items():
            if score >= 2:
                match_winner = pid
                break
        if match_winner:
            match_loser = [p for p in participants if p != match_winner][0]
            duel["results"][match_winner] = {"entry": "win", "outcome": "win"}
            duel["results"][match_loser] = {"entry": "lose", "outcome": "lose"}
            duel["forced_winner"] = match_winner
            duel["forced_loser"] = match_loser
            await self._resolve_duel(duel_id, duel)
        # If this duel involved an NPC and we still haven't resolved, pick the reported winner to end it.
        if any(str(p).startswith("npc-") for p in participants) and not duel.get("forced_winner"):
            outcomes = {pid: (duel["results"].get(pid) or {}).get("outcome") for pid in participants}
            winners = [pid for pid, out in outcomes.items() if out == "win"]
            losers = [pid for pid, out in outcomes.items() if out == "lose"]
            # If only one participant has reported anything, assume that report decides the duel.
            if len(winners) == 1:
                duel["forced_winner"] = winners[0]
                duel["forced_loser"] = losers[0] if losers else [p for p in participants if p != winners[0]][0]
                await self._resolve_duel(duel_id, duel)
            elif len(winners) == 0 and len(losers) == 0 and len(duel.get("results", {})) == 1:
                reporter = next(iter(duel["results"].keys()))
                outcome = duel["results"][reporter].get("outcome")
                opp = [p for p in participants if p != reporter]
                opp_id = opp[0] if opp else None
                if outcome in ("win", "lose", "forfeit"):
                    if outcome == "win":
                        duel["forced_winner"] = reporter
                        duel["forced_loser"] = opp_id
                    else:
                        duel["forced_winner"] = opp_id
                        duel["forced_loser"] = reporter
                    await self._resolve_duel(duel_id, duel)
                    return
        # If duel was resolved and removed, clear duel flags so actors unfreeze.
        if duel_id not in self.pending_duels:
            self.duel_active = False
            self.duel_participants = []

    async def _resolve_duel(self, duel_id: str, duel: Dict):
        participants = duel["participants"]
        entries = [duel["results"].get(p, {}).get("entry") for p in participants]
        selected = duel.get("selected")
        winner = None
        loser = None
        if duel.get("forced_winner"):
            winner = duel.get("forced_winner")
            loser = duel.get("forced_loser")
            # If loser is missing, infer the other participant.
            if not loser and len(participants) >= 2:
                others = [p for p in participants if p != winner]
                loser = others[0] if others else None
        # Deterministic RPS resolution when applicable.
        if selected == "rps_duel" and len(participants) >= 2 and not winner:
            # Avoid random resolve if we don't have complete round data.
            if duel.get("scores"):
                winner, loser = self._resolve_rps(entries, participants)
            if not winner:
                self._log(f"[Duel] Waiting for complete results for {duel_id}")
                return
        if not winner:
            # Do not random-resolve; wait for proper outcomes.
            self._log(f"[Duel] Incomplete results for {duel_id}, skipping resolve")
            return
        outcome_payload = {
            "type": "duel_result",
            "duel_id": duel_id,
            "winner": winner,
            "loser": loser,
            "entries": entries,
        }
        self._log(f"Duel resolved winner={winner} loser={loser}")
        await self._broadcast(outcome_payload)
        self._eliminate_actor(loser)
        self.duel_active = False
        self.duel_participants = []
        # Clear any lingering out-of-circle timers so duel participants get a fresh grace period.
        for pid in participants:
            if pid in self.match_players:
                self.match_players[pid]["outside_timer"] = 0.0
        self.pending_duels.pop(duel_id, None)

    def _resolve_rps(self, entries, participants):
        BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
        if len(participants) < 2:
            return None, None
        choice_map = {}
        for idx, pid in enumerate(participants):
            raw = entries[idx] if idx < len(entries) else None
            choice_map[pid] = (str(raw).strip().lower() if raw else None)
        a, b = participants[0], participants[1]
        ca, cb = choice_map.get(a), choice_map.get(b)
        if ca not in BEATS or cb not in BEATS:
            return None, None
        if ca == cb:
            rng = random.Random(f"rps-{ca}-{cb}-{a}-{b}")
            winner = rng.choice([a, b])
            loser = b if winner == a else a
            return winner, loser
        if BEATS[ca] == cb:
            return a, b
        return b, a

    def _update_duel_resolve(self):
        """Resolve any pending duel if timeout reached."""
        now = time.time()
        to_resolve = []
        for duel_id, duel in list(self.pending_duels.items()):
            age = now - duel.get("start", now)
            parts = duel.get("participants", [])
            if all(p in duel.get("results", {}) for p in parts):
                to_resolve.append((duel_id, duel))
                continue
            if age >= self._duel_resolve_timeout:
                to_resolve.append((duel_id, duel))
        for duel_id, duel in to_resolve:
            asyncio.run_coroutine_threadsafe(self._resolve_duel(duel_id, duel), self.loop)

    def _build_match_snapshot(self, now: float):
        ents = []
        alive_humans = 0
        for pid, st in self.match_players.items():
            if pid in self.eliminated or (not st.get("npc", False) and pid in self.eliminated_humans):
                continue
            if not st.get("npc", False):
                alive_humans += 1
            ents.append(
                {
                    "id": pid,
                    "pos": (int(st["pos"][0]), int(st["pos"][1])),
                    "vel": (int(st["vel"][0]), int(st["vel"][1])),
                    "char": st.get("char"),
                    "npc": st.get("npc", False),
                    "name": st.get("name"),
                }
            )
        return {
            "tick": self.match_tick,
            "ts": now,
            "entities": ents,
            "remaining": len(ents),
            "remaining_humans": alive_humans,
            "remaining_total": len(ents),
            "npc_winner": False,
        }

    def _eliminate_actor(self, player_id: Optional[str]):
        if not player_id:
            return
        # Humans: mark eliminated but keep record for match counts.
        if player_id in self.match_players and not self.match_players[player_id].get("npc"):
            self.eliminated_humans.add(player_id)
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "eliminate", "player_id": player_id}), self.loop
            )
            return
        # NPCs: remove fully.
        self.eliminated.add(player_id)
        if hasattr(self, "npc_busy") and player_id in self.npc_busy:
            opp = self.npc_busy[player_id].get("opponent")
            self.npc_busy.pop(player_id, None)
            if opp and opp in self.npc_busy:
                self.npc_busy.pop(opp, None)
        if player_id in self.match_players:
            self.match_players.pop(player_id, None)
        if player_id in self.match_inputs:
            self.match_inputs.pop(player_id, None)
        asyncio.run_coroutine_threadsafe(
            self._broadcast({"type": "eliminate", "player_id": player_id}), self.loop
        )


def stop_active_lobby():
    """Stop any globally tracked lobby server/clients to avoid duplicate hosts."""
    global ACTIVE_LOBBY_SERVER
    if ACTIVE_LOBBY_SERVER:
        try:
            ACTIVE_LOBBY_SERVER.stop()
        except Exception:
            pass
        ACTIVE_LOBBY_SERVER = None
    for client in list(ACTIVE_LOBBY_CLIENTS):
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            ACTIVE_LOBBY_CLIENTS.remove(client)
        except ValueError:
            pass


class LobbyClient:
    """Asyncio-based lobby client that connects to a host."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread: Optional[threading.Thread] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.player_id: Optional[str] = None
        self.events = queue.Queue()
        self.state: Dict = {}
        self.last_match: Optional[Dict] = None
        self.last_match_state: Dict = {}
        self.last_duel: Optional[Dict] = None
        self.last_minigame: Optional[Dict] = None
        self.last_duel_request: Optional[Dict] = None
        self.last_duel_round: Optional[Dict] = None
        self.last_elimination: Optional[str] = None
        self.last_duel_action: Optional[Dict] = None
        self._duel_action_queue: Dict[str, queue.Queue] = {}
        self.connected = False
        self._stop_event = threading.Event()
        self._name = "Player"
        self._state_lock = threading.Lock()
        self._match_state_queue = queue.Queue()
        self.local_id = None

    def connect(self, host: str, port: int, name: str) -> bool:
        if self.connected:
            return True
        self._stop_event.clear()
        self._name = name or "Player"
        self.thread = threading.Thread(target=self._run_loop, args=(host, port), daemon=True)
        self.thread.start()
        # Wait for up to 3 seconds for connection result.
        waited = 0.0
        while waited < 3.0:
            if self.connected:
                if self not in ACTIVE_LOBBY_CLIENTS:
                    ACTIVE_LOBBY_CLIENTS.append(self)
                return True
            if self._stop_event.is_set():
                break
            self._stop_event.wait(0.1)
            waited += 0.1
        return self.connected

    def disconnect(self):
        if not self.thread:
            return
        self._stop_event.set()
        if self.loop and not self.loop.is_closed() and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except RuntimeError:
                pass
        self.thread.join(timeout=3)
        self.thread = None
        self.connected = False
        if self in ACTIVE_LOBBY_CLIENTS:
            ACTIVE_LOBBY_CLIENTS.remove(self)

    def _run_loop(self, host: str, port: int):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._connect(host, port))
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            try:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self.loop.close()

    async def _connect(self, host: str, port: int):
        try:
            self.reader, self.writer = await asyncio.open_connection(host, port)
            self.connected = True
            self.events.put(f"Connected to {host}:{port}")
            await self._send({"type": "hello", "name": self._name})
            asyncio.create_task(self._listen())
        except Exception as exc:
            self.events.put(f"Failed to connect: {exc}")
            self._stop_event.set()
            self.loop.stop()

    async def _listen(self):
        try:
            while not self.reader.at_eof():
                data = await self.reader.readline()
                if not data:
                    break
                try:
                    payload = json.loads(data.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                self._handle_message(payload)
        finally:
            self.events.put("Disconnected from lobby.")
            self.connected = False
            self._stop_event.set()
            self.loop.stop()

    async def _shutdown(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if self in ACTIVE_LOBBY_CLIENTS:
            ACTIVE_LOBBY_CLIENTS.remove(self)

    def _handle_message(self, payload: Dict):
        msg_type = payload.get("type")
        if msg_type == "welcome":
            self.player_id = payload.get("player_id")
            self.local_id = self.player_id
            with self._state_lock:
                self.state = payload.get("state") or {}
            self.events.put("Joined lobby.")
        elif msg_type == "lobby_state":
            with self._state_lock:
                self.state = payload.get("state") or {}
        elif msg_type == "start_match":
            self.last_match = payload.get("match") or {}
            self.events.put("Start match received.")
        elif msg_type == "match_state":
            state = payload.get("state") or {}
            self.last_match_state = state
            try:
                self._match_state_queue.put_nowait(state)
            except queue.Full:
                pass
        elif msg_type == "start_duel":
            self.events.put("Duel starting.")
            self.last_duel = payload
        elif msg_type == "start_minigame":
            self.events.put("Minigame starting.")
            self.last_minigame = payload
        elif msg_type == "duel_result":
            self.events.put(f"Duel result: {payload.get('outcome')}")
            self.last_duel = payload
            # Show outcome banner
            winner = payload.get("winner")
            if winner:
                if winner == (self.local_id or self.player_id):
                    self.duel_banner = "You won the duel!"
                elif payload.get("loser") == (self.local_id or self.player_id):
                    self.duel_banner = "You lost the duel."
                else:
                    self.duel_banner = f"Duel winner: {winner}"
                self.duel_banner_time = 5.0
        elif msg_type == "duel_request":
            self.last_duel_request = payload
            self.events.put(f"Duel request from {payload.get('from')}")
        elif msg_type == "duel_round_result":
            self.last_duel_round = payload
        elif msg_type == "eliminate":
            self.last_elimination = payload.get("player_id")
        elif msg_type == "duel_action":
            self.last_duel_action = payload
            did = payload.get("duel_id")
            if did:
                q = self._duel_action_queue.setdefault(did, queue.Queue())
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass

    async def _send(self, payload: Dict):
        if not self.writer:
            return
        try:
            self.writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self.writer.drain()
        except ConnectionError:
            pass

    def send_ready(self, ready: bool):
        if not self.connected:
            return
        asyncio.run_coroutine_threadsafe(self._send({"type": "set_ready", "ready": ready}), self.loop)

    def send_set_map(self, map_name: str):
        if not self.connected or not map_name:
            return
        asyncio.run_coroutine_threadsafe(self._send({"type": "set_map", "map_name": map_name}), self.loop)

    def send_set_mode(self, mode: str):
        if not self.connected or not mode:
            return
        asyncio.run_coroutine_threadsafe(self._send({"type": "set_mode", "mode": mode}), self.loop)

    def send_set_allow_npc(self, allow: bool):
        # NPC fill disabled; ignore.
        return

    def send_set_char(self, char_name: str):
        if not self.connected or not char_name:
            return
        asyncio.run_coroutine_threadsafe(self._send({"type": "set_char", "char_name": char_name}), self.loop)

    def send_duel_request(self, target_id: str):
        if not self.connected or not target_id:
            return
        payload = {"type": "request_duel", "target": target_id}
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_match_input(self, x: float, y: float):
        if not self.connected:
            return
        payload = {"type": "match_input", "vec": {"x": float(x), "y": float(y)}}
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_duel_result(self, result: Dict):
        if not self.connected:
            return
        payload = {"type": "duel_result", **result}
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_debug_duel(self, target_id: Optional[str] = None):
        if not self.connected:
            return
        payload = {"type": "debug_start_duel"}
        if target_id:
            payload["target"] = target_id
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_start_minigame(self, minigame: str, participants: List[str]):
        if not self.connected or not minigame or not participants:
            return
        payload = {
            "type": "start_minigame",
            "minigame": minigame,
            "participants": participants,
            "duel_id": uuid.uuid4().hex,
        }
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_minigame_result(self, duel_id: str, minigame: str, outcome: str):
        if not self.connected or not duel_id or not minigame:
            return
        payload = {
            "type": "minigame_result",
            "duel_id": duel_id,
            "minigame": minigame,
            "outcome": outcome,
            "player_id": self.player_id,
        }
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_duel_choice(self, duel_id: str, entry: str):
        if not self.connected or not duel_id or not entry:
            return
        payload = {
            "type": "duel_choice",
            "duel_id": duel_id,
            "entry": entry,
        }
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def send_start_match(self, map_name=None, mode=None, seed=None, allow_npc=False):
        if not self.connected:
            return
        payload = {
            "type": "start_match",
            "map": map_name,
            "mode": mode,
            "seed": seed,
            "allow_npc": False,
        }
        asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)

    def pop_events(self) -> List[str]:
        msgs = []
        while True:
            try:
                msgs.append(self.events.get_nowait())
            except queue.Empty:
                break
        return msgs

    def get_state(self) -> Dict:
        with self._state_lock:
            return copy.deepcopy(self.state)

    def pop_match_state(self) -> Optional[Dict]:
        try:
            return self._match_state_queue.get_nowait()
        except queue.Empty:
            return None

    def pop_duel_action(self, duel_id: str) -> Optional[Dict]:
        if not duel_id:
            return None
        q = self._duel_action_queue.get(duel_id)
        if not q:
            return None
        try:
            return q.get_nowait()
        except queue.Empty:
            return None

    def send_duel_action(self, payload: Dict):
        if not self.connected or not payload:
            return
        msg = {"type": "duel_action", **payload}
        asyncio.run_coroutine_threadsafe(self._send(msg), self.loop)


class MultiplayerArenaScene(ArenaScene):
    """Lightweight multiplayer arena that spawns players/bots from a lobby match payload."""

    def __init__(self, manager, client: LobbyClient, match: Dict, server=None):
        self.client = client
        self.server = server
        self.match = match or {}
        self.local_id = getattr(client, "player_id", None)
        self.is_spectator = False
        self.spectator_pos = None
        self.eliminated_ids = set()
        self.allow_npc = bool(self.match.get("allow_npc"))
        self.seed = self.match.get("seed") or uuid.uuid4().hex
        self.spawns = self.match.get("spawns") or []
        self.players = self.match.get("players") or []
        if not self.spawns:
            total = max(1, len(self.players))
            self.spawns = self._generate_seeded_spawns(total)
        self._rng = random.Random(self.seed)
        mode = "tournament"
        selection = {
            "map_name": "test_arena",
            "mode": mode,
            "spawn_at": self._spawn_for(self.local_id),
            "char_name": self._char_for(self.local_id) or "classic",
        }
        context = GameContext()
        context.flags.update(
            {
                "mode": mode,
                "multiplayer": True,
                "match_seed": self.seed,
                "allow_npc": self.allow_npc,
            }
        )
        super().__init__(manager, selection, context)
        self.match_info = {
            "map": selection["map_name"],
            "mode": mode,
            "allow_npc": self.allow_npc,
        }
        self.starting_humans = max(1, sum(1 for p in self.players if not p.get("npc")))
        self.other_players = self._build_other_players()
        self._actor_map = {p["player_id"]: p for p in self.other_players if p.get("player_id")}
        self._player_target = self.player_rect.midbottom
        self._net_targets = {}
        self._last_input_vec = (0.0, 0.0)
        self._last_input_time = 0.0
        self._last_snapshot_time = 0.0
        self._last_draw_pos = self.player_rect.midbottom
        self.duel_banner = ""
        self.duel_banner_time = 0.0
        self.elim_banner = ""
        self.elim_banner_time = 0.0
        self.remaining = None
        self.eliminated_ids = set()
        self._predict_timer = 0.0
        self.pending_duel_request: Optional[Dict] = None
        self.match_over = False
        self.match_outcome_msg: Optional[str] = None
        self.match_end_screen_shown = False
        self.duel_ui = {
            "active": False,
            "entries": [],
            "seed": None,
            "start": 0.0,
            "duration": 3.0,  # spin time
            "hold": 3.0,  # time to keep result visible after stopping
            "participants": [],
            "result_sent": False,
            "selected": None,
            "launched": False,
            "duel_id": None,
        }
        self.challenge_btn_rect = None

    def _spawn_for(self, player_id: Optional[str]):
        for spawn in self.spawns:
            if spawn.get("player_id") == player_id and spawn.get("pos"):
                return spawn.get("pos")
        if self.spawns:
            return self.spawns[0].get("pos")
        return None

    def _build_other_players(self):
        actors = []
        for spawn in self.spawns:
            pid = spawn.get("player_id")
            pos = spawn.get("pos")
            if not pos or pid == self.local_id:
                continue
            rect = pygame.Rect(0, 0, 12, 14)
            rect.midbottom = (int(pos[0]), int(pos[1]))
            label = self._label_for(pid)
            is_npc = spawn.get("npc", False) or label is None
            skin_name = self._char_for(pid)
            anim = self._load_anim_for_skin(pid or "npc", skin_name, is_npc)
            sprite, foot_y = self._pick_idle_sprite(pid or "npc", skin_name=skin_name, allow_random=is_npc)
            if anim and not sprite:
                sprite = anim.get("idle", [[None]])[0][0] if anim.get("idle") else sprite
            actors.append(
                {
                    "player_id": pid,
                    "rect": rect,
                    "label": label or "NPC",
                    "color": (120, 180, 255) if is_npc else (255, 200, 120),
                    "is_bot": is_npc,
                    "sprite": sprite,
                    "foot_y": foot_y,
                    "anim": anim,
                    "anim_state": {"index": 0.0, "facing": "down", "state": "idle"},
                }
            )

        return actors

    def _generate_bot_spawns(self, count: int):
        rng = random.Random(self.seed)
        total = len(self.spawns) + count
        radius = 260
        result = []
        for idx in range(len(self.spawns), len(self.spawns) + count):
            angle = (idx / max(1, total)) * 2 * math.pi
            angle += rng.uniform(-0.1, 0.1)
            x = int(320 + radius * math.cos(angle))
            y = int(240 + radius * math.sin(angle))
            result.append((x, y))
        return result

    def _generate_seeded_spawns(self, total: int):
        rng = random.Random(self.seed)
        radius = 240
        spawns = []
        total = max(1, total)
        for idx in range(total):
            ang = (idx / total) * 2 * math.pi
            ang += rng.uniform(-0.15, 0.15)
            x = int(320 + radius * math.cos(ang))
            y = int(240 + radius * math.sin(ang))
            pid = None
            if idx < len(self.players):
                pid = self.players[idx].get("player_id")
            spawns.append({"player_id": pid, "pos": (x, y)})
        return spawns

    def _label_for(self, player_id: Optional[str]):
        for p in self.players:
            if p.get("player_id") == player_id:
                return p.get("name") or "Player"
        return None

    def _char_for(self, player_id: Optional[str]):
        for p in self.players:
            if p.get("player_id") == player_id:
                return p.get("char_name") or None
        return None

    def _pick_idle_sprite(self, identifier: str, skin_name: Optional[str] = None, allow_random: bool = True):
        chars = self.character_folders or []
        if not chars:
            return None, 0
        skin_dir = None
        if skin_name:
            for c in chars:
                if c.name == skin_name:
                    skin_dir = c
                    break
        if skin_dir is None and allow_random:
            seed_val = f"{self.seed}-{identifier}"
            rng = random.Random(seed_val)
            skin_dir = rng.choice(chars)
        if skin_dir is None:
            skin_dir = chars[0]
        idle_path = skin_dir / "idle.png"
        try:
            img = pygame.image.load(idle_path).convert_alpha()
        except Exception:
            return None, 0
        rows = 4
        fh = img.get_height() // rows if rows else img.get_height()
        fw = fh
        if fw <= 0 or fh <= 0:
            return None, 0
        frame = pygame.Surface((fw, fh), pygame.SRCALPHA)
        frame.blit(img, (0, 0), (0, 0, fw, fh))
        foot_y = fh - 4
        return frame, foot_y

    def update(self, dt):
        # Multiplayer: send inputs, then apply latest host snapshots.
        self.context.add_playtime(dt)
        if self.pending_duel_request and (time.time() - self.pending_duel_request.get("ts", 0) > 8.0):
            self.pending_duel_request = None
        self._poll_snapshots()
        self._send_inputs()
        self._predict_if_stale(dt)
        self._lerp_entities(dt)
        self._update_duel(dt)
        if self.is_spectator:
            self._spectator_move(dt)
        freeze_inputs = False
        if self.duel_ui.get("active") or self.duel_ui.get("duel_id"):
            freeze_inputs = True
        if self.controller and not freeze_inputs:
            self.controller.update(dt)

    def _poll_snapshots(self):
        if not self.client:
            return
        while True:
            snap = self.client.pop_match_state()
            if not snap:
                break
            self._last_snapshot_time = time.time()
            self.remaining = snap.get(
                "remaining_total",
                snap.get("remaining_humans", snap.get("remaining", self.remaining)),
            )
            entities = snap.get("entities") or []
            seen_ids = set()
            humans_in_entities = 0
            local_in_entities = False
            for ent in entities:
                pid = ent.get("id")
                if not pid:
                    continue
                seen_ids.add(pid)
                if not ent.get("npc"):
                    humans_in_entities += 1
                if pid == self.local_id:
                    local_in_entities = True
                if pid in self.eliminated_ids:
                    continue
                pos = ent.get("pos") or (0, 0)
                target = (int(pos[0]), int(pos[1]))
                if pid == self.local_id:
                    self._player_target = target
                actor = self._get_or_create_actor(pid, ent)
                if actor:
                    actor["target"] = target
                    actor["npc"] = ent.get("npc", False)
                    actor["label"] = ent.get("name") or actor.get("label")
                    new_char = ent.get("char") or actor.get("char")
                    if new_char and new_char != actor.get("char"):
                        actor["anim"] = self._load_anim_for_skin(pid, new_char, actor.get("is_bot", False))
                        actor["char"] = new_char
                    if not actor.get("anim"):
                        actor["anim"] = self._load_anim_for_skin(pid, actor.get("char"), actor.get("is_bot", False))
            # Remove actors that vanished from snapshot (e.g., eliminated NPCs).
            for pid in list(self._actor_map.keys()):
                if pid == self.local_id:
                    continue
                if pid not in seen_ids:
                    actor = self._actor_map.pop(pid, None)
                    if actor and actor in self.other_players:
                        try:
                            self.other_players.remove(actor)
                        except ValueError:
                            pass
            # End-of-match detection for local player.
            if not self.match_over:
                snap_winner = snap.get("winner")
                npc_winner = snap.get("npc_winner")
                if snap_winner:
                    if snap_winner == self.local_id:
                        self.duel_banner = "YOU WIN! Press Esc to exit."
                        self.duel_banner_time = 999.0
                        self.match_over = True
                        self.match_outcome_msg = "You win the match!"
                    else:
                        self.duel_banner = "YOU LOSE. Press Esc to exit."
                        self.duel_banner_time = 999.0
                        self.match_over = True
                        self.match_outcome_msg = "You lost the match"
                elif npc_winner:
                    self.duel_banner = "YOU LOSE. Press Esc to exit."
                    self.duel_banner_time = 999.0
                    self.match_over = True
                    self.match_outcome_msg = "You lost the match"
                # Prefer live entity counts so we don't end the match early when the host under-reports.
                reported_humans = snap.get("remaining_humans", 0)
                humans = max(humans_in_entities, reported_humans if isinstance(reported_humans, int) else 0)
                reported_total = snap.get("remaining_total", self.remaining or 0)
                total = max(len(entities), reported_total if isinstance(reported_total, int) else 0)
                # Keep remaining in sync with the more conservative total we just computed.
                self.remaining = total
                # Treat local as alive only if we still appear in the snapshot and not explicitly eliminated.
                if local_in_entities:
                    self.eliminated_ids.discard(self.local_id)
                local_alive = local_in_entities and (self.local_id not in self.eliminated_ids)
                # Debug: print snapshot counts to trace premature endings (throttled).
                # You win only when you're the sole entity left.
                if total == 1 and local_alive:
                    self.duel_banner = "YOU WIN! Press Esc to exit."
                    self.duel_banner_time = 999.0
                    self.match_over = True
                    self.match_outcome_msg = "You win the match!"
                # If you're eliminated and only one remains, you lose.
                elif total == 1 and not local_alive:
                    self.duel_banner = "YOU LOSE. Press Esc to exit."
                    self.duel_banner_time = 999.0
                    self.match_over = True
                    self.match_outcome_msg = "You lost the match"
                # Spectators: when match ends (1 remaining), show loss screen.
                elif self.is_spectator and total == 1:
                    self.duel_banner = "YOU LOSE. Press Esc to exit."
                    self.duel_banner_time = 999.0
                    self.match_over = True
                    self.match_outcome_msg = "You lost the match"
        # purge any eliminated actors lingering locally
        for eid in list(self.eliminated_ids):
            actor = self._actor_map.pop(eid, None)
            if actor and actor in self.other_players:
                try:
                    self.other_players.remove(actor)
                except ValueError:
                    pass
        # Spectator fallback: if only one entity remains and we're already spectating, end the match with a loss.
        if self.is_spectator and not self.match_over and self.remaining is not None and self.remaining <= 1:
            self.duel_banner = "YOU LOSE. Press Esc to exit."
            self.duel_banner_time = 999.0
            self.match_over = True
            self.match_outcome_msg = "You lost the match"
        # Ignore duel flow in sandbox multiplayer; use minigame NPC path instead.
        if self.mode != "sandbox" and self.client and self.client.last_duel:
            duel = self.client.last_duel
            self.client.last_duel = None
            # Spectators ignore duel announcements/banners.
            if self.is_spectator:
                duel = None
            if duel:
                dtype = duel.get("type")
                participants = duel.get("participants") or []
                # If we're not in this duel, ignore banners/UI.
                if participants and self.local_id not in participants:
                    duel = None
                    dtype = None
                if duel and dtype == "duel_result":
                    winner = duel.get("winner")
                    loser = duel.get("loser")
                    # Failsafe: infer loser when missing so we don't award both sides a win.
                    if not loser and winner:
                        parts = participants or self.duel_ui.get("participants") or []
                        if winner in parts and len(parts) == 2:
                            other = [p for p in parts if p != winner]
                            if other:
                                loser = other[0]
                    if winner:
                        if winner == self.local_id:
                            self.duel_banner = "You won the duel!"
                        self.duel_banner_time = 6.0
                    if loser:
                        if loser == self.local_id:
                            self.is_spectator = True
                            self.spectator_pos = self.player_rect.midbottom
                            self.duel_banner = ""
                            self.duel_banner_time = 0.0
                            self.elim_banner = "Eliminated — Spectating"
                            self.elim_banner_time = 5.0
                            self.eliminated_ids.add(loser)
                        else:
                            self.eliminated_ids.add(loser)
                            actor = self._actor_map.pop(loser, None)
                            if actor and actor in self.other_players:
                                try:
                                    self.other_players.remove(actor)
                                except ValueError:
                                    pass
                    # Only end the whole match early when this duel leaves exactly one competitor (true finals).
                    if winner and winner == self.local_id and (self.remaining is not None and self.remaining <= 1):
                        self.duel_banner = "YOU WIN! Press Esc to exit."
                        self.duel_banner_time = 999.0
                        self.match_over = True
                        self.match_outcome_msg = "You win the match!"
                    elif loser and loser == self.local_id and (self.remaining is not None and self.remaining <= 1):
                        self.duel_banner = "YOU LOSE. Press Esc to exit."
                        self.duel_banner_time = 999.0
                        self.match_over = True
                        self.match_outcome_msg = "You lost the match"
                    # Clear duel UI after resolution.
                    self.duel_ui.update(
                        {
                            "active": False,
                            "entries": [],
                            "participants": [],
                            "selected": None,
                            "duel_id": None,
                            "launched": False,
                            "result_sent": True,
                        }
                    )
                elif duel:
                    parts = duel.get("participants", [])
                    # Only show duel start if we're participating.
                    if self.local_id in parts:
                        # Clear any stale spectator/elimination flags when a new duel that includes us begins.
                        self.eliminated_ids.discard(self.local_id)
                        if self.is_spectator:
                            self.is_spectator = False
                            self.spectator_pos = None
                        self.duel_banner = "Duel starting..."
                        self.duel_banner_time = 6.0
                    self.pending_duel_request = None
                    loser = duel.get("loser")
                    if loser and loser != self.local_id:
                        self.eliminated_ids.add(loser)
                        actor = self._actor_map.pop(loser, None)
                        if actor and actor in self.other_players:
                            try:
                                self.other_players.remove(actor)
                            except ValueError:
                                pass
                    if not self.is_spectator and self.local_id in parts:
                        self._start_duel_ui(duel)
        if self.client and self.client.last_minigame:
            mg = self.client.last_minigame
            self.client.last_minigame = None
            minigame_id = mg.get("minigame")
            participants = mg.get("participants") or []
            if minigame_id and (self.local_id in participants):
                # Re-enable ourselves if a duel minigame is targeted at us.
                if self.is_spectator:
                    self.is_spectator = False
                    self.spectator_pos = None
                    self.eliminated_ids.discard(self.local_id)
                self._launch_duel_minigame(minigame_id, duel_id=mg.get("duel_id"), participants=participants)
        if self.client and self.client.last_duel_request and not self.is_spectator:
            req = self.client.last_duel_request
            self.client.last_duel_request = None
            if req.get("to") == self.local_id:
                self.pending_duel_request = {"from": req.get("from"), "ts": time.time()}
                self.duel_banner = "Duel request received. Press E to accept."
                self.duel_banner_time = 5.0
        if self.client and self.client.last_elimination:
            eliminated = self.client.last_elimination
            self.client.last_elimination = None
            # Ignore local elimination so we can keep playing; still remove remote actors.
            if eliminated != self.local_id:
                self.eliminated_ids.add(eliminated)
                actor = self._actor_map.pop(eliminated, None)
                if actor and actor in self.other_players:
                    try:
                        self.other_players.remove(actor)
                    except ValueError:
                        pass
        # If match is flagged over, show a proper end screen once.
        if self.match_over and not self.match_end_screen_shown:
            self._trigger_match_end(self.match_outcome_msg or "Match over")

    def _send_inputs(self):
        keys = pygame.key.get_pressed()
        vx = vy = 0.0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            vx -= 1.0
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            vx += 1.0
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            vy -= 1.0
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            vy += 1.0
        vec = (vx, vy)
        now = time.time()
        if vec != self._last_input_vec or now - self._last_input_time > 0.15:
            self._last_input_vec = vec
            self._last_input_time = now
            if self.client and not self.is_spectator:
                self.client.send_match_input(vx, vy)

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_F9 and self.client:
                target = self._find_nearby_opponent()
                if target:
                    self.client.send_debug_duel(target)
                    self._log_local(f"Requested duel (debug) vs {target}.")
                else:
                    self._log_local("No opponent in range for duel.")
            if event.key == pygame.K_e and self.client and not self.is_spectator:
                self._challenge_action()
            if event.key == pygame.K_ESCAPE:
                # allow pause
                super().handle_event(event)
                return
        super().handle_event(event)

    def _log_local(self, msg: str):
        try:
            print(f"[Multiplayer] {msg}")
        except Exception:
            pass

    def _find_nearby_opponent(self, radius: float = 36.0) -> Optional[str]:
        px, py = self.player_rect.midbottom
        best = None
        best_d = radius * radius
        for pid, actor in self._actor_map.items():
            if not actor:
                continue
            rx, ry = actor["rect"].midbottom
            dx = px - rx
            dy = py - ry
            d2 = dx * dx + dy * dy
            if d2 <= best_d:
                best = pid
                best_d = d2
        return best

    def _challenge_action(self):
        """Handle both sending and accepting duel challenges."""
        if self.is_spectator or not self.client:
            return False
        # If a duel is spinning/active, ignore further requests.
        if self.duel_ui.get("active") or self.duel_ui.get("duel_id"):
            return False
        now = time.time()
        # Accept pending request first.
        if self.pending_duel_request and (now - self.pending_duel_request.get("ts", 0) < 8.0):
            target = self.pending_duel_request.get("from")
            if target:
                self.client.send_duel_request(target)
                self._log_local(f"Accepted duel request from {target}.")
                self.duel_banner = ""
                self.duel_banner_time = 0.0
                self.pending_duel_request = None
                return True
        # Otherwise send a new challenge to the nearest opponent.
        target = self._find_nearby_opponent()
        if target:
            self.client.send_duel_request(target)
            self._log_local(f"Requested duel vs {target}.")
            self.duel_banner = "Challenge sent."
            self.duel_banner_time = 3.0
            return True
        self._log_local("No opponent nearby to challenge.")
        return False

    def _challenge_button_enabled(self) -> bool:
        if self.is_spectator:
            return False
        if self.duel_ui.get("active") or self.duel_ui.get("duel_id"):
            return False
        now = time.time()
        if self.pending_duel_request and (now - self.pending_duel_request.get("ts", 0) < 8.0):
            return True
        return bool(self._find_nearby_opponent())

    def _lerp_entities(self, dt):
        lerp_rate = 12.0
        if self._player_target and not self.is_spectator:
            px, py = self.player_rect.midbottom
            tx, ty = self._player_target
            t = min(1.0, lerp_rate * dt)
            self.player_rect.midbottom = (px + (tx - px) * t, py + (ty - py) * t)
            vx = (self.player_rect.midbottom[0] - self._last_draw_pos[0]) / max(dt, 1e-3)
            vy = (self.player_rect.midbottom[1] - self._last_draw_pos[1]) / max(dt, 1e-3)
            self.anim.update(dt, vx, vy)
            self._last_draw_pos = self.player_rect.midbottom
        elif self.is_spectator and self.spectator_pos:
            # Keep player rect aligned to spectator position for camera.
            self.player_rect.midbottom = self.spectator_pos

        for actor in list(self._actor_map.values()):
            if not actor:
                continue
            target = actor.get("target") or actor["rect"].midbottom
            cx, cy = actor["rect"].midbottom
            t = min(1.0, lerp_rate * dt)
            actor["rect"].midbottom = (cx + (target[0] - cx) * t, cy + (target[1] - cy) * t)
            vx = (actor["rect"].midbottom[0] - cx) / max(dt, 1e-3)
            vy = (actor["rect"].midbottom[1] - cy) / max(dt, 1e-3)
            self._update_actor_anim(actor, vx, vy, dt)

    def _spectator_move(self, dt: float):
        """Free camera movement for eliminated players."""
        keys = pygame.key.get_pressed()
        vx = vy = 0.0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            vx -= 1.0
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            vx += 1.0
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            vy -= 1.0
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            vy += 1.0
        speed = 220.0
        dx = vx * speed * dt
        dy = vy * speed * dt
        if self.spectator_pos is None:
            self.spectator_pos = self.player_rect.midbottom
        x, y = self.spectator_pos
        x += dx
        y += dy
        maxx, maxy = getattr(self, "map_w_px", 640), getattr(self, "map_h_px", 480)
        x = max(0, min(x, maxx))
        y = max(0, min(y, maxy))
        self.spectator_pos = (x, y)
        self.player_rect.midbottom = self.spectator_pos

    def _start_duel_ui(self, duel: Dict):
        entries = duel.get("wheel_entries") or duel.get("wheel") or []
        seed = duel.get("wheel_spin_seed")
        forced = duel.get("selected_entry")
        participants = duel.get("participants") or []
        # Only show spinner for participants; others just keep the banner.
        active = self.local_id in participants
        # Clear any stale request state once a duel is starting.
        now = time.monotonic()
        spin_time = 3.0
        # Guarantee at least 2 seconds of result hold after the spin stops.
        result_hold = max(2.0, 3.0)
        self.pending_duel_request = None
        self.duel_ui.update(
            {
                "active": active,
                "entries": entries,
                "seed": seed,
                "start": now,
                "duration": spin_time,
                "hold": result_hold,
                "post_pause": 0.0,  # extra pause after spin before launching
                "launch_at": None,
                "participants": participants,
                "result_sent": False,
                "selected": None,
                "duel_id": duel.get("duel_id"),
                "launched": False,
            }
        )
        self.spectator_pos = self.player_rect.midbottom
        if forced:
            self.duel_ui["selected"] = forced
        elif entries and seed is not None:
            rng = random.Random(seed)
            self.duel_ui["selected"] = rng.choice(entries)
        # If only one entry, skip the long spin.
        if len(entries) == 1:
            self.duel_ui["duration"] = 0.35
            self.duel_ui["hold"] = max(1.0, result_hold * 0.75)
            self.duel_ui["post_pause"] = 0.0
            # Start the timer as if it already spun to avoid extra waiting.
            self.duel_ui["start"] = time.monotonic() - self.duel_ui["duration"]
        else:
            # Slow the spin a bit so the wheel is readable.
            self.duel_ui["duration"] = spin_time
            self.duel_ui["hold"] = result_hold
            self.duel_ui["post_pause"] = 0.0
        # Set a fixed launch timestamp based on spin+hold so the result stays up.
        self.duel_ui["launch_at"] = self.duel_ui["start"] + self.duel_ui["duration"] + self.duel_ui["hold"] + self.duel_ui.get("post_pause", 0.0)
        if active and self.duel_ui["selected"] is not None:
            self.duel_banner = "Duel starting..."
            self.duel_banner_time = max(self.duel_banner_time, 3.0)

    def _update_duel(self, dt: float):
        if not self.duel_ui["active"]:
            return
        now = time.monotonic()
        elapsed = now - self.duel_ui["start"]
        duration = self.duel_ui.get("duration", 3.0)
        post_pause = self.duel_ui.get("post_pause", 0.0)
        hold = self.duel_ui.get("hold", 3.0)
        spin_end = self.duel_ui["start"] + duration
        result_end = spin_end + hold
        launch_at = self.duel_ui.get("launch_at")
        # Ensure launch_at is defined so comparisons are safe. Launch happens strictly after spin + hold.
        if launch_at is None:
            launch_at = result_end + post_pause
            self.duel_ui["launch_at"] = launch_at
        # Do not launch until the result has been held on screen for the full hold window.
        if now < result_end:
            return
        # After spin stops, launch minigame for participants once.
        if (
            now >= launch_at
            and (self.local_id in self.duel_ui.get("participants", []))
            and not self.is_spectator
        ):
            if not self.duel_ui.get("launched"):
                self._launch_duel_minigame(
                    self.duel_ui.get("selected"),
                    duel_id=self.duel_ui.get("duel_id"),
                    participants=self.duel_ui.get("participants"),
                )
                self.duel_ui["launched"] = True
                self.duel_ui["active"] = False
                self.duel_banner = ""
                self.duel_banner_time = 0.0
        # Non-participants do nothing beyond this point.
        if self.local_id not in self.duel_ui.get("participants", []):
            return

    def _launch_duel_minigame(self, entry: Optional[str], duel_id: Optional[str] = None, participants=None):
        if not entry:
            return
        mod = load_minigame_module(entry)
        if not mod:
            self._log_local(f"Failed to import minigame {entry}")
            return
        if not hasattr(mod, "launch"):
            self._log_local(f"Minigame {entry} missing launch()")
            return

        def on_exit(ctx):
            details = ctx.last_result or {}
            result = details.get("outcome", "ack")
            choice = details.get("choice") or details.get("entry") or entry
            winner = details.get("winner")
            loser = details.get("loser")
            # Fallback inference if winner/loser not set in details.
            if not winner and not loser and self.local_id and participants:
                opp = [p for p in participants if p != self.local_id]
                opp_id = opp[0] if opp else None
                if result == "win":
                    winner, loser = self.local_id, opp_id
                elif result in ("lose", "forfeit"):
                    winner, loser = opp_id, self.local_id
            payload = {
                "entry": choice,
                "player_id": self.local_id,
                "outcome": result,
                "duel_id": duel_id or self.duel_ui.get("duel_id"),
            }
            if winner:
                payload["winner"] = winner
            if loser:
                payload["loser"] = loser
            if self.client:
                self.client.send_duel_result(payload)
            self._log_local(f"Duel result sent from minigame: {payload}")

        try:
            # Ensure context carries duel metadata for minigames that care.
            if not hasattr(self.context, "flags"):
                self.context.flags = {}
            if duel_id:
                self.context.flags["duel_id"] = duel_id
            if participants:
                self.context.flags["participants"] = participants
            force_kwargs = {
                "duel_id": duel_id,
                "participants": participants,
                "multiplayer_client": self.client,
                "local_player_id": self.local_id,
            }
            self.manager.push(mod.launch(self.manager, self.context, on_exit, **force_kwargs))
        except Exception as exc:
            self._log_local(f"Failed to launch {entry}: {exc}")

    def _draw_duel_spinner(self):
        entries = self.duel_ui.get("entries") or []
        if not self.duel_ui.get("active"):
            return
        if not entries:
            return
        elapsed = time.monotonic() - self.duel_ui["start"]
        duration = self.duel_ui.get("duration", 3.0)
        hold = self.duel_ui.get("hold", 3.0)
        # Safety: stop rendering after spin+hold window.
        if elapsed > duration + hold + 0.5:
            return
        tau = math.tau if hasattr(math, "tau") else 2 * math.pi
        step = tau / max(1, len(entries))
        progress = 1.0 if duration <= 0 else max(0.0, min(1.0, elapsed / duration))
        seed = float(self.duel_ui.get("seed") or 0.0)
        base_angle = seed * tau
        total_spin = 6 * math.pi  # slower spin for readability
        # Freeze angle after the spin finishes so the result is readable.
        if elapsed >= duration:
            spin_angle = 0.0
        else:
            spin_angle = (1.0 - progress) * total_spin
        offset = base_angle + spin_angle - math.pi / 2
        selected_entry = self.duel_ui.get("selected")
        if elapsed >= duration and selected_entry in entries:
            highlight_idx = entries.index(selected_entry)
        else:
            highlight_idx = int(((0 - offset) % tau) // step) % len(entries)
        cx = self.screen.get_width() // 2
        cy = self.screen.get_height() // 2
        radius_outer = 160
        radius_inner = 64
        font = pygame.font.SysFont(None, 30)
        # Draw pointer
        tip = (cx, cy - radius_outer - 18)
        left = (cx - 10, cy - radius_outer - 2)
        right = (cx + 10, cy - radius_outer - 2)
        pygame.draw.polygon(self.screen, (255, 240, 150), [tip, left, right])
        # Draw wheel slices
        for i, entry in enumerate(entries):
            start_ang = offset + i * step
            end_ang = start_ang + step
            points = [(cx, cy)]
            segs = 16
            for s in range(segs + 1):
                a = start_ang + (s / segs) * step
                points.append((cx + math.cos(a) * radius_outer, cy + math.sin(a) * radius_outer))
            # inner arc back to center (optional for nicer shape)
            points.append((cx, cy))
            color = (255, 240, 150) if i == highlight_idx else (200, 210, 235)
            pygame.draw.polygon(self.screen, color, points)
            # entry label
            mid_ang = start_ang + step * 0.5
            tx = cx + math.cos(mid_ang) * ((radius_outer + radius_inner) * 0.5)
            ty = cy + math.sin(mid_ang) * ((radius_outer + radius_inner) * 0.5)
            txt = font.render(entry.replace("_", " ").title(), True, (30, 35, 55))
            self.screen.blit(txt, txt.get_rect(center=(tx, ty)))
        # Draw inner circle to clean center
        pygame.draw.circle(self.screen, (20, 24, 36), (cx, cy), radius_inner)
        if selected_entry and elapsed >= duration:
            sel_txt = font.render(selected_entry.replace("_", " ").title(), True, (255, 240, 180))
            self.screen.blit(sel_txt, sel_txt.get_rect(center=(cx, cy)))

    def _get_or_create_actor(self, pid: str, ent: Dict):
        if pid == self.local_id or pid in self.eliminated_ids:
            return None
        actor = self._actor_map.get(pid)
        if actor:
            return actor
        rect = pygame.Rect(0, 0, 12, 14)
        pos = ent.get("pos") or (0, 0)
        rect.midbottom = (int(pos[0]), int(pos[1]))
        label = ent.get("name") or self._label_for(pid) or "Player"
        skin = ent.get("char")
        anim = self._load_anim_for_skin(pid, skin, ent.get("npc", False))
        surf, foot = self._pick_idle_sprite(pid, skin_name=skin, allow_random=ent.get("npc", False))
        actor = {
            "player_id": pid,
            "rect": rect,
            "label": label,
            "color": (120, 180, 255) if ent.get("npc") else (255, 200, 120),
            "is_bot": ent.get("npc", False),
            "sprite": surf,
            "foot_y": foot,
            "char": skin,
            "anim": anim,
            "anim_state": {"index": 0.0, "facing": "down", "state": "idle"},
        }
        self.other_players.append(actor)
        self._actor_map[pid] = actor
        return actor

    def _load_anim_for_skin(self, identifier: str, skin_name: Optional[str], allow_random: bool):
        chars = self.character_folders or []
        if not chars:
            return None
        skin_dir = None
        if skin_name:
            for c in chars:
                if c.name == skin_name:
                    skin_dir = c
                    break
        if skin_dir is None and allow_random:
            rng = random.Random(f"{self.seed}-anim-{identifier}")
            skin_dir = rng.choice(chars)
        if skin_dir is None:
            skin_dir = chars[0]
        try:
            idle_img = pygame.image.load(skin_dir / "idle.png").convert_alpha()
            walk_img = pygame.image.load(skin_dir / "walk.png").convert_alpha()
        except Exception:
            return None
        def slice_sheet(sheet):
            rows = 4
            fh = sheet.get_height() // rows
            fw = fh
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
        return {
            "idle": slice_sheet(idle_img),
            "walk": slice_sheet(walk_img),
            "foot_y": max(0, walk_img.get_height() // 4 - 4),
            "dir_rows": {"down": 0, "up": 1, "left": 2, "right": 3},
        }

    def _update_actor_anim(self, actor: Dict, vx: float, vy: float, dt: float):
        anim = actor.get("anim")
        if not anim:
            return
        state = actor.setdefault("anim_state", {"index": 0.0, "facing": "down", "state": "idle"})
        moving = abs(vx) + abs(vy) > 0.1
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
        speed = 10.0 if moving else 2.0
        state["index"] = (state.get("index", 0.0) + speed * dt) % max(1, len(row))
        actor["sprite"] = row[int(state["index"]) % len(row)]
        actor["foot_y"] = anim.get("foot_y", row[0].get_height() if row else 0)

    def _predict_if_stale(self, dt: float):
        # If host snapshots are missing/stale, nudge local player using latest inputs so controls aren't frozen.
        if self.is_spectator:
            return
        if not self._player_target:
            return
        age = time.time() - self._last_snapshot_time if self._last_snapshot_time else 0
        if age < 0.25:
            return
        vx, vy = self._last_input_vec
        if vx == 0 and vy == 0:
            return
        speed = 150.0
        dx = vx * speed * dt
        dy = vy * speed * dt
        rect = self.player_rect.copy()
        rect.x += int(round(dx))
        rect.y += int(round(dy))
        for c in getattr(self, "colliders", []):
            if rect.colliderect(c):
                if dx > 0:
                    rect.right = c.left
                elif dx < 0:
                    rect.left = c.right
                if dy > 0:
                    rect.bottom = c.top
                elif dy < 0:
                    rect.top = c.bottom
        # Clamp to map bounds to avoid drifting outside the arena during prediction.
        maxx, maxy = getattr(self, "map_w_px", rect.right), getattr(self, "map_h_px", rect.bottom)
        rect.left = max(0, min(rect.left, maxx - rect.width))
        rect.top = max(0, min(rect.top, maxy - rect.height))
        self.player_rect = rect
        self._player_target = rect.midbottom

    def draw(self):
        super().draw()
        font = pygame.font.SysFont(None, 22)
        details = f"{self.match_info.get('map', '').replace('_', ' ').title()} • {self.match_info.get('mode', '').title()}"
        info = font.render(details, True, (220, 230, 245))
        self.screen.blit(info, info.get_rect(topright=(self.screen.get_width() - 12, 8)))
        if self.remaining is not None:
            rem_font = pygame.font.SysFont(None, 28)
            rem = rem_font.render(f"Rivals Remaining: {self.remaining}", True, (200, 230, 255))
            rem_rect = rem.get_rect(midtop=(self.screen.get_width() // 2, 8))
            self.screen.blit(rem, rem_rect)
        # Out-of-circle warning (UI layer so it doesn't scale with zoom), centered on screen.
        warn_seconds = None
        ctrl = getattr(self, "controller", None)
        if ctrl and hasattr(ctrl, "safe_center") and hasattr(ctrl, "safe_radius") and not self.is_spectator:
            cx, cy = ctrl.safe_center or (None, None)
            r = ctrl.safe_radius
            if cx is not None and r:
                px, py = self.player_rect.center
                dist = math.hypot(px - cx, py - cy)
                if dist > r:
                    timer = 0.0
                    if hasattr(ctrl, "outside_timers"):
                        timer = ctrl.outside_timers.get("player", 0.0)
                    warn_seconds = max(0, int(5 - timer))
        if warn_seconds is not None and warn_seconds > 0:
            warn_font = pygame.font.SysFont(None, 44)
            warn_txt = warn_font.render(f"Return inside: {warn_seconds}s", True, (255, 120, 120))
            warn_rect = warn_txt.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2))
            self.screen.blit(warn_txt, warn_rect)
        if self._last_snapshot_time:
            age_ms = int((time.time() - self._last_snapshot_time) * 1000)
            net_txt = font.render(f"Net age: {age_ms} ms", True, (200, 210, 235))
            self.screen.blit(net_txt, net_txt.get_rect(topright=(self.screen.get_width() - 12, 28)))
        # Challenge prompt (accept or send) replaces the old button.
        self.challenge_btn_rect = None
        if not self.is_spectator and not self.duel_ui.get("active") and not self.duel_ui.get("duel_id"):
            pending = self.pending_duel_request and (time.time() - self.pending_duel_request.get("ts", 0) < 8.0)
            near = self._find_nearby_opponent()
            if pending or near:
                label = "Press E to Accept Challenge" if pending else "Press E to Challenge"
                prompt_font = pygame.font.SysFont(None, 24)
                txt = prompt_font.render(label, True, (255, 240, 150))
                rect = txt.get_rect(midbottom=(self.screen.get_width() // 2, self.screen.get_height() - 28))
                self.screen.blit(txt, rect)
        if self.duel_banner_time > 0 and self.duel_banner and self.mode != "sandbox":
            self.duel_banner_time = max(0.0, self.duel_banner_time - 1 / 60.0)
            big_font = pygame.font.SysFont(None, 64)
            txt = big_font.render(self.duel_banner, True, (255, 240, 150))
            self.screen.blit(txt, txt.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2)))
        if self.duel_ui["active"] and self.mode != "sandbox":
            self._draw_duel_spinner()
        if getattr(self, "is_spectator", False):
            spec = font.render("Spectating", True, (200, 230, 255))
            self.screen.blit(spec, spec.get_rect(topright=(self.screen.get_width() - 12, 48)))
        if self.elim_banner_time > 0 and self.elim_banner:
            self.elim_banner_time = max(0.0, self.elim_banner_time - 1 / 60.0)
            big_font = pygame.font.SysFont(None, 36)
            txt = big_font.render(self.elim_banner, True, (255, 180, 180))
            self.screen.blit(txt, txt.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2)))

    def _cleanup(self):
        # Disconnect clients and stop any global lobby to avoid duplicate hosts after exiting.
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        stop_active_lobby()

    def __del__(self):
        # Ensure background lobby server/client are torn down when leaving the match.
        self._cleanup()

    def _trigger_match_end(self, message: str):
        """Cleanly end the match and show a menu button."""
        if self.match_end_screen_shown:
            return
        self.match_end_screen_shown = True
        try:
            self._cleanup()
        except Exception:
            pass
        try:
            self.manager.switch(MatchEndScene(self.manager, message))
        except Exception:
            # Fallback: stop manager if switch fails.
            self.manager.running = False


class MatchEndScene(Scene):
    """Simple end-of-match screen with a back-to-menu button."""

    def __init__(self, manager, message: str = "Match over"):
        super().__init__(manager)
        self.screen = manager.screen
        self.message = message or "Match over"
        self.font_title = pygame.font.SysFont(None, 64)
        self.font_body = pygame.font.SysFont(None, 28)
        self.button_rect = pygame.Rect(0, 0, 260, 64)
        self.button_rect.center = (self.screen.get_width() // 2, self.screen.get_height() // 2 + 70)
        self.button_hover = False

    def handle_event(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.button_hover = self.button_rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.button_rect.collidepoint(event.pos):
                self._go_home()
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE):
                self._go_home()

    def update(self, dt):
        pass

    def draw(self):
        self.screen.fill((12, 14, 26))
        title = self.font_title.render(self.message, True, (255, 240, 180))
        self.screen.blit(title, title.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2 - 40)))
        sub = self.font_body.render("Match has ended.", True, (210, 220, 235))
        self.screen.blit(sub, sub.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2 + 10)))
        # Button
        btn_color = (120, 170, 255) if self.button_hover else (90, 130, 200)
        pygame.draw.rect(self.screen, btn_color, self.button_rect, border_radius=12)
        pygame.draw.rect(self.screen, (30, 40, 60), self.button_rect, width=2, border_radius=12)
        label = self.font_body.render("Back to Home", True, (15, 18, 28))
        self.screen.blit(label, label.get_rect(center=self.button_rect.center))

    def _go_home(self):
        try:
            from main_menu import MainMenu
            self.manager.switch(MainMenu(self.manager))
        except Exception:
            self.manager.pop()
