"""
sound_engine.py — simple pygame sound manager
---------------------------------------------
Handles:
 • play_music(name)   → loop background music (.ogg or .wav)
 • stop_music()       → stop background track
 • play_sfx(name)     → play one-shot effect
 • play_step()        → throttled footstep sound
 • load_map_profile() → same interface as before
"""

import pygame, random, time, importlib.util, sys
from pathlib import Path

# --- directories ---
ROOT = Path(__file__).parent
AUDIO_PATH = ROOT / "audio"
SFX_PATH = AUDIO_PATH / "sfx"
MUSIC_PATH = AUDIO_PATH / "music"

# --- settings ---
MUSIC_VOL = 0.6
SFX_VOL = 0.8
STEP_DELAY = 0.25
STEP_SOUNDS = ["step.wav"]

# --- init mixer once ---
pygame.mixer.pre_init(44100, -16, 2, 1024)
pygame.mixer.init()
pygame.mixer.set_num_channels(16)

_last_step_time = 0.0


# --- background music ---
def play_music(filename):
    try:
        track = MUSIC_PATH / filename
        if not track.exists():
            print(f"[Sound] Missing music file: {filename}")
            return
        pygame.mixer.music.load(track)
        pygame.mixer.music.set_volume(MUSIC_VOL)
        pygame.mixer.music.play(-1)
        print(f"[Sound] Playing music: {filename}")
    except Exception as e:
        print(f"[Sound] Music error: {e}")


def stop_music(fade_ms=500):
    try:
        pygame.mixer.music.fadeout(fade_ms)
    except Exception:
        pass


# --- sfx ---
def play_sfx(name):
    try:
        path = SFX_PATH / name
        if not path.exists():
            print(f"[Sound] Missing SFX: {name}")
            return
        snd = pygame.mixer.Sound(path)
        snd.set_volume(SFX_VOL)
        pygame.mixer.find_channel(True).play(snd)
    except Exception as e:
        print(f"[Sound] SFX error: {e}")


def play_step():
    """Throttled footstep sound."""
    global _last_step_time
    now = time.time()
    if now - _last_step_time < STEP_DELAY:
        return
    _last_step_time = now
    try:
        sfx = random.choice(STEP_SOUNDS)
        play_sfx(sfx)
    except Exception as e:
        print(f"[Sound] Step error: {e}")


# --- map profile loader (unchanged) ---
def load_map_profile(module_path):
    try:
        path = Path(module_path).parent / "map_profile.py"
        if not path.exists():
            print(f"[MapProfile] No map_profile.py in {path.parent}")
            return None
        spec = importlib.util.spec_from_file_location("map_profile", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["map_profile"] = mod
        spec.loader.exec_module(mod)
        print(f"[MapProfile] Loaded from {path.parent.name}")
        return mod
    except Exception as e:
        print(f"[MapProfile] Load failed: {e}")
        return None