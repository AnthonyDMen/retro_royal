"""
game_context.py
---------------
Minimal shared player context for Retro Royale.
Tracks wins, losses, per-minigame stats, total playtime, and credits.
"""


class GameContext:
    def __init__(self):
        self.stats = {
            "wins": 0,
            "losses": 0,
            "minigames_played": {},  # e.g. {"rps_duel": {"wins": 2, "losses": 1}}
            "total_time": 0.0,  # seconds across arena + minigames
            "credits": 0,
        }

        self.flags = {}  # map or mode state (rounds, mode, etc.)
        self.last_result = {}  # filled after each minigame

    # -----------------------------------------------------
    #   Core logic
    # -----------------------------------------------------
    def apply_result(self):
        """Apply the most recent minigame result to cumulative stats."""
        if not self.last_result:
            return

        r = self.last_result
        mg = r.get("minigame", "unknown")
        outcome = r.get("outcome", "")

        record = self.stats["minigames_played"].setdefault(
            mg, {"wins": 0, "losses": 0}
        )

        if outcome == "win":
            self.stats["wins"] += 1
            record["wins"] += 1
            self.stats["credits"] += 10
        elif outcome == "lose":
            self.stats["losses"] += 1
            record["losses"] += 1

    def add_playtime(self, dt):
        """Add delta-time (in seconds) to total runtime."""
        self.stats["total_time"] += dt

    def summary(self):
        return {
            "stats": self.stats,
            "flags": self.flags,
            "last_result": self.last_result,
        }

    def __repr__(self):
        return (
            f"<GameContext wins={self.stats['wins']} "
            f"losses={self.stats['losses']} credits={self.stats['credits']} "
            f"time={self.stats['total_time']:.1f}s>"
        )
