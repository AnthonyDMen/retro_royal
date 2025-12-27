import json
from datetime import datetime
from pathlib import Path

SCORE_DIR = Path.home() / ".retro_royale_scores"
SCORE_DIR.mkdir(exist_ok=True)


def _file_for(map_name):
    return SCORE_DIR / f"{map_name}_scores.json"


def load_scores(map_name):
    f = _file_for(map_name)
    if f.exists():
        with open(f, "r") as fh:
            return json.load(fh)
    return []


def save_score(context, map_name, result):
    """Record the end-of-run stats into a per-map leaderboard file."""
    if map_name == "test_arena":
        return  # no scoreboard for sandbox

    f = _file_for(map_name)
    scores = load_scores(map_name)

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "map": map_name,
        "wins": context.stats["wins"],
        "losses": context.stats["losses"],
        "credits": context.stats["credits"],
        "total_time": round(context.stats["total_time"], 1),
        "result": result,
    }

    scores.append(entry)
    # keep champions sorted by fastest time
    champions = [s for s in scores if s["result"] == "Champion"]
    champions.sort(key=lambda s: s["total_time"])
    with open(f, "w") as fh:
        json.dump(champions[:10], fh, indent=2)

    print(f"[Scoreboard] {map_name} updated — {len(champions[:10])} entries saved.")


import pygame


def draw_highscores(screen, font, map_name="tutor_forest", scroll_y=0):
    """Draw top 10 scores for a given map directly onto the screen."""
    scores = load_scores(map_name)
    w, h = screen.get_size()

    title_font = pygame.font.SysFont(None, 60)
    sub_font = font or pygame.font.SysFont(None, 28)
    small_font = pygame.font.SysFont(None, 22)

    screen.fill((18, 20, 29))

    title = title_font.render("Hall of Champions", True, (255, 235, 140))
    screen.blit(title, title.get_rect(center=(w // 2, 60)))

    subtitle = sub_font.render(
        f"Top 10 – {map_name.replace('_', ' ').title()}", True, (200, 220, 255)
    )
    screen.blit(subtitle, subtitle.get_rect(center=(w // 2, 120)))

    y = 160 + scroll_y
    if not scores:
        msg = small_font.render("No champion runs recorded yet.", True, (180, 180, 200))
        screen.blit(msg, msg.get_rect(center=(w // 2, h // 2)))
    else:
        for i, s in enumerate(scores[:10], start=1):
            line = (
                f"#{i:2d}  {s['total_time']:>6.1f}s   "
                f"{s['wins']}W/{s['losses']}L   {s['credits']}cr   {s['timestamp']}"
            )
            color = (255, 245, 180) if i == 1 else (220, 220, 240)
            entry = small_font.render(line, True, color)
            screen.blit(entry, (80, y))
            y += 30

    hint = small_font.render(
        "ESC to return  •  UP/DOWN to scroll", True, (180, 180, 190)
    )
    screen.blit(hint, hint.get_rect(center=(w // 2, h - 30)))
