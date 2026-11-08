#!/bin/bash
# Build everything: read the four scanned scores, make the MIDI and the audio,
# and put the site together.
#
#   build_all.sh [song ...]
#
# The tempo of each piece is the one printed on its score.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

declare -a TEMPO=(
  "besame:45"
  "yinxiang:84"
  "tieta:97"
  "chenmo:60"
)

SONGS=("$@")
if [ ${#SONGS[@]} -eq 0 ]; then
  SONGS=(besame yinxiang tieta chenmo)
fi

for entry in "${TEMPO[@]}"; do
  song="${entry%%:*}"
  bpm="${entry##*:}"
  case " ${SONGS[*]} " in
    *" $song "*) ;;
    *) continue ;;
  esac

  echo "=== $song (♩=$bpm) ==="
  WORK="work/songs/$song"

  python3 tools/build_song.py "$song" \
    --pages "work/omr/pages/$song" \
    --prep "$WORK/prep" --omr "$WORK/omr1" --masked "$WORK/masked" \
    --strips "$WORK/strips" --out "$WORK/song.json" \
    --chords "$WORK/chords.json" --bpm "$bpm"

  python3 tools/song_to_vf.py "$WORK/song.json" "$WORK/vf.json"
  python3 tools/song_to_midi.py "$WORK/song.json" "$WORK/song.mid"
  python3 -c "
import sys; sys.path.insert(0, '.')
from piano.io.audio import render_midi_to_wav
render_midi_to_wav('$WORK/song.mid', '$WORK/song.wav')
"
done

python3 tools/make_site.py --songs work/songs --out web
echo "done"
