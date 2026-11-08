#!/bin/bash
# Full OMR pipeline for one song: pages -> clean score images -> note data.
#
# Each page is tried at a few scales, and the one that gives Audiveris a
# rhythm is kept: a page with note heads but no rhythm is no use.
#
#   omr_song.sh <song> <pages-dir> <work-dir>
set -euo pipefail
SONG="$1"; PAGES="$2"; WORK="$3"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$WORK"
for f in "$PAGES"/*.png; do
  python3 "$ROOT/tools/omr_page.py" "$f" "$WORK"
done

python3 "$ROOT/tools/build_song.py" "$SONG" \
  --pages "$PAGES" --prep "$WORK/prep" --omr "$WORK/omr1" --masked "$WORK/masked" \
  --strips "$WORK/strips" --out "$WORK/song.json" "${@:4}"

# The song as notation, so that it can be drawn next to the printed page and
# the two compared.
python3 "$ROOT/tools/song_to_vf.py" "$WORK/song.json" "$WORK/vf.json"
