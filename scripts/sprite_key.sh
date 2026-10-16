#!/usr/bin/env bash
# Chroma-key a sprite: green-screen source in, transparent PNG out.
#
#   scripts/sprite_key.sh in.jpeg out.png
#
# ffmpeg only decodes; the key itself is RGB greenness in sprite_key.py, which
# survives the gradient screens the model likes to add (ffmpeg's YUV chromakey
# does not — see the note in that file).
set -euo pipefail

IN="${1:?usage: sprite_key.sh in.jpeg out.png}"
OUT="${2:?usage: sprite_key.sh in.jpeg out.png}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -t spritekey).png"

ffmpeg -hide_banner -loglevel error -y -i "$IN" -pix_fmt rgba -frames:v 1 "$TMP"
python3 "$HERE/sprite_key.py" "$TMP" "$OUT"
rm -f "$TMP"
