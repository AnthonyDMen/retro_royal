#!/usr/bin/env bash
set -euo pipefail

# Assumes the virtual environment is active.
pyinstaller --noconfirm --clean \
  --add-data "audio:audio" \
  --add-data "characters:characters" \
  --add-data "maps:maps" \
  --add-data "minigames:minigames" \
  boot.py
