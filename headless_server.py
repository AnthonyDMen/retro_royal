import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import argparse
import asyncio
import logging
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request, abort, render_template

from multiplayer import LobbyServer


@dataclass
class HeadlessConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    web_host: str = "0.0.0.0"
    web_port: int = 5000
    auto_start: bool = True
    min_players: int = 2
    ready_required: bool = True
    ready_timeout: float = 0.0
    start_delay: float = 30.0
    reset_delay: float = 4.0
    map_name: str = "test_arena"


class HeadlessController:
    def __init__(self, server: LobbyServer, config: HeadlessConfig):
        self.server = server
        self._config = config
        self._config_lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._min_players_since: Optional[float] = None
        self._eligible_since: Optional[float] = None
        self._pending_reset_at: Optional[float] = None
        self._last_match_active = False
        self._last_start_request = 0.0
        self._last_meta: Optional[Dict[str, Any]] = None
        self.started_at = time.time()

    def start(self) -> bool:
        self.server.allow_join_during_match = False
        self.server.allow_join_in_lobby = True
        if not self.server.start():
            return False
        self._loop_thread = threading.Thread(target=self._loop, daemon=True)
        self._loop_thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=2)
        self.server.stop()

    def get_config(self) -> Dict[str, Any]:
        with self._config_lock:
            return asdict(self._config)

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        def _coerce_bool(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return default

        def _coerce_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except Exception:
                return default

        def _coerce_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except Exception:
                return default

        with self._config_lock:
            cfg = self._config
            if "auto_start" in payload:
                cfg.auto_start = _coerce_bool(payload.get("auto_start"), cfg.auto_start)
            if "min_players" in payload:
                cfg.min_players = max(1, _coerce_int(payload.get("min_players"), cfg.min_players))
            if "ready_required" in payload:
                cfg.ready_required = _coerce_bool(payload.get("ready_required"), cfg.ready_required)
            if "ready_timeout" in payload:
                cfg.ready_timeout = max(0.0, _coerce_float(payload.get("ready_timeout"), cfg.ready_timeout))
            if "start_delay" in payload:
                cfg.start_delay = max(0.0, _coerce_float(payload.get("start_delay"), cfg.start_delay))
            if "reset_delay" in payload:
                cfg.reset_delay = max(0.0, _coerce_float(payload.get("reset_delay"), cfg.reset_delay))
        return self.get_config()

    def kick(self, player_id: str) -> bool:
        if not player_id:
            return False
        return self._submit(self.server._remove_player(player_id), timeout=3.0)

    def force_start(self, seed: Optional[str] = None) -> bool:
        if self.server.match_active:
            return False
        now = time.time()
        if now - self._last_start_request < 1.0:
            return False
        self._last_start_request = now
        state = self.server.snapshot_state()
        players = state.get("players") or []
        if not players:
            return False
        match, map_json = self._build_match(players, seed=seed)
        return self._submit(self._start_match(match, map_json), timeout=3.0)

    def reset_lobby(self) -> bool:
        return self._submit(self._reset_match_state(), timeout=3.0)

    def set_lobby_lock(self, locked: bool) -> bool:
        self.server.allow_join_in_lobby = not locked
        self._submit(self.server._broadcast_state(), timeout=2.0)
        return True

    def get_countdown(self) -> Optional[int]:
        cfg = self.get_config()
        if not cfg.get("auto_start"):
            return None
        if self._eligible_since is None:
            return None
        start_delay = float(cfg.get("start_delay", 0.0))
        remaining = max(0.0, start_delay - (time.time() - self._eligible_since))
        return int(round(remaining))

    def _build_server_meta(self, match_active: bool) -> Dict[str, Any]:
        cfg = self.get_config()
        join_locked = bool(
            (match_active and not self.server.allow_join_during_match)
            or (not match_active and not self.server.allow_join_in_lobby)
        )
        return {
            "auto_start": cfg.get("auto_start"),
            "min_players": cfg.get("min_players"),
            "ready_required": cfg.get("ready_required"),
            "ready_timeout": cfg.get("ready_timeout"),
            "start_delay": cfg.get("start_delay"),
            "reset_delay": cfg.get("reset_delay"),
            "auto_start_in": self.get_countdown(),
            "lobby_locked": bool(not self.server.allow_join_in_lobby),
            "join_locked": join_locked,
        }

    def _loop(self):
        while not self._stop_event.is_set():
            now = time.time()
            match_active = bool(self.server.match_active)
            if match_active:
                self._eligible_since = None
                self._min_players_since = None
            if self._last_match_active and not match_active:
                self._pending_reset_at = now + self._config.reset_delay
            self._last_match_active = match_active
            if self._pending_reset_at and now >= self._pending_reset_at:
                self.reset_lobby()
                self._pending_reset_at = None
            if not match_active and not self._pending_reset_at:
                self._maybe_auto_start(now)
            meta = self._build_server_meta(match_active)
            if meta != self._last_meta:
                self._last_meta = meta
                with self.server._lock:
                    self.server.server_meta = meta
                self._submit(self.server._broadcast_state(), timeout=2.0)
            time.sleep(0.5)

    def _maybe_auto_start(self, now: float):
        cfg = self.get_config()
        if not cfg.get("auto_start"):
            self._min_players_since = None
            self._eligible_since = None
            return
        state = self.server.snapshot_state()
        players = state.get("players") or []
        count = len(players)
        if count < max(1, int(cfg.get("min_players", 1))):
            self._min_players_since = None
            self._eligible_since = None
            return
        if self._min_players_since is None:
            self._min_players_since = now
        ready_required = bool(cfg.get("ready_required"))
        ready_timeout = float(cfg.get("ready_timeout", 0.0))
        all_ready = all(p.get("ready") for p in players) if players else False
        if ready_required:
            if all_ready:
                eligible = True
            elif ready_timeout > 0.0 and (now - self._min_players_since) >= ready_timeout:
                eligible = True
            else:
                eligible = False
        else:
            eligible = True
        if not eligible:
            self._eligible_since = None
            return
        if self._eligible_since is None:
            self._eligible_since = now
            return
        start_delay = float(cfg.get("start_delay", 0.0))
        if (now - self._eligible_since) >= start_delay:
            self.force_start()
            self._eligible_since = None

    def _build_match(self, players: list[Dict[str, Any]], seed: Optional[str] = None):
        cfg = self.get_config()
        map_name = cfg.get("map_name") or "test_arena"
        map_json = Path(__file__).parent / "maps" / map_name / "map.json"
        seed = seed or os.urandom(8).hex()
        spawns = self.server._build_spawns_from_map(map_json, seed, len(players))
        for idx, p in enumerate(players):
            if idx < len(spawns):
                spawns[idx]["player_id"] = p["player_id"]
            else:
                spawns.append({"player_id": p["player_id"], "pos": spawns[0]["pos"]})
        match = {
            "map": map_name,
            "mode": "tournament",
            "seed": seed,
            "allow_npc": False,
            "players": players,
            "spawns": spawns,
        }
        return match, map_json

    async def _start_match(self, match: Dict[str, Any], map_json: Path):
        await self.server._broadcast({"type": "start_match", "match": match})
        await self.server._begin_match(match, map_json)

    async def _reset_match_state(self):
        self.server.match_active = False
        if self.server.match_task:
            try:
                self.server.match_task.cancel()
            except Exception:
                pass
            self.server.match_task = None
        self.server.match_players.clear()
        self.server.match_inputs.clear()
        self.server.eliminated.clear()
        self.server.eliminated_humans.clear()
        self.server.pending_duels.clear()
        self.server.pending_duel_requests.clear()
        self.server.duel_active = False
        self.server.duel_participants = []
        self.server.duel_cooldown = 0.0
        self.server.duel_timeout = 0.0
        self.server.auto_duel_timer = 0.0
        self.server.npc_busy = {}
        self.server.npc_duel_cooldown = 0.0
        self.server.npc_idle_timers = {}
        self.server.match_tick = 0
        self.server.safe_center = None
        self.server.safe_radius = None
        with self.server._lock:
            for player in self.server.state.players:
                player.ready = False
        await self.server._broadcast_state()

    def _submit(self, coro: asyncio.Future, timeout: float = 2.0) -> bool:
        if not self.server.loop.is_running():
            return False
        fut = asyncio.run_coroutine_threadsafe(coro, self.server.loop)
        try:
            fut.result(timeout=timeout)
            return True
        except Exception:
            return False


def _build_app(controller: HeadlessController, admin_token: str) -> Flask:
    app = Flask(__name__)
    last_status_signature = None

    class _StatusLogFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            return "GET /status" not in message and "POST /status" not in message

    logging.getLogger("werkzeug").addFilter(_StatusLogFilter())

    def require_token():
        if not admin_token:
            return
        token = request.headers.get("X-Admin-Token") or ""
        if token != admin_token:
            abort(401)

    @app.get("/")
    def index():
        return render_template("headless_admin.html")

    @app.get("/status")
    def status():
        nonlocal last_status_signature
        state = controller.server.snapshot_state()
        players = state.get("players") or []
        ready_count = sum(1 for p in players if p.get("ready"))
        match_active = controller.server.match_active
        join_locked = bool(
            (match_active and not controller.server.allow_join_during_match)
            or (not match_active and not controller.server.allow_join_in_lobby)
        )
        payload = {
            "lobby": state,
            "match_active": match_active,
            "match_player_count": len(controller.server.match_players),
            "player_count": len(players),
            "ready_count": ready_count,
            "join_locked": join_locked,
            "lobby_locked": bool(not controller.server.allow_join_in_lobby),
            "auto_start_in": controller.get_countdown(),
            "config": controller.get_config(),
            "uptime_sec": int(time.time() - controller.started_at),
        }
        signature = (
            payload["match_active"],
            payload["match_player_count"],
            payload["player_count"],
            payload["ready_count"],
            payload["join_locked"],
            payload["lobby_locked"],
            payload["auto_start_in"],
            tuple(
                (
                    p.get("player_id"),
                    p.get("name"),
                    p.get("char_name"),
                    p.get("ready"),
                )
                for p in players
            ),
        )
        if signature != last_status_signature:
            last_status_signature = signature
            app.logger.info(
                "Status change: players=%s ready=%s match=%s join_locked=%s",
                payload["player_count"],
                payload["ready_count"],
                payload["match_active"],
                payload["join_locked"],
            )
        return jsonify(payload)

    @app.post("/kick")
    def kick():
        require_token()
        data = request.get_json(silent=True) or {}
        player_id = data.get("player_id") or ""
        ok = controller.kick(player_id)
        return jsonify({"ok": ok})

    @app.post("/start")
    def start_match():
        require_token()
        data = request.get_json(silent=True) or {}
        ok = controller.force_start(seed=data.get("seed"))
        return jsonify({"ok": ok})

    @app.post("/reset")
    def reset_match():
        require_token()
        ok = controller.reset_lobby()
        return jsonify({"ok": ok})

    @app.post("/config")
    def update_config():
        require_token()
        data = request.get_json(silent=True) or {}
        cfg = controller.update_config(data)
        return jsonify(cfg)

    @app.post("/lock")
    def lock_lobby():
        require_token()
        data = request.get_json(silent=True) or {}
        locked = bool(data.get("locked"))
        ok = controller.set_lobby_lock(locked)
        return jsonify({"ok": ok, "locked": locked})

    return app


def main():
    parser = argparse.ArgumentParser(description="Headless lobby server with admin web hub.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=5000)
    parser.add_argument("--auto-start", action="store_true", default=True)
    parser.add_argument("--no-auto-start", dest="auto_start", action="store_false")
    parser.add_argument("--min-players", type=int, default=2)
    parser.add_argument("--ready-required", action="store_true", default=True)
    parser.add_argument("--no-ready-required", dest="ready_required", action="store_false")
    parser.add_argument("--ready-timeout", type=float, default=0.0)
    parser.add_argument("--start-delay", type=float, default=30.0)
    parser.add_argument("--reset-delay", type=float, default=4.0)
    parser.add_argument("--map-name", default="test_arena")
    args = parser.parse_args()

    admin_token = os.getenv("HEADLESS_ADMIN_TOKEN", "")

    config = HeadlessConfig(
        host=args.host,
        port=args.port,
        web_host=args.web_host,
        web_port=args.web_port,
        auto_start=args.auto_start,
        min_players=max(1, args.min_players),
        ready_required=args.ready_required,
        ready_timeout=max(0.0, args.ready_timeout),
        start_delay=max(0.0, args.start_delay),
        reset_delay=max(0.0, args.reset_delay),
        map_name=args.map_name or "test_arena",
    )

    server = LobbyServer(host=config.host, port=config.port)
    controller = HeadlessController(server, config)
    if not controller.start():
        raise SystemExit("Failed to start lobby server.")

    app = _build_app(controller, admin_token=admin_token)
    app.run(host=config.web_host, port=config.web_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
