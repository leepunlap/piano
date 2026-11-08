#!/bin/bash
# Full OMR pipeline for one song: pages -> clean score images -> note data.
#   omr_song.sh <song> <pages-dir> <work-dir> [scale]
set -euo pipefail
SONG="$1"; PAGES="$2"; WORK="$3"; SCALE="${4:-2.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$WORK/prep" "$WORK/omr1" "$WORK/masked" "$WORK/strips"
python3 "$ROOT/tools/prep_images.py" --indir "$PAGES" --outdir "$WORK/prep" --scale "$SCALE" --border 30 >/dev/null

for f in "$WORK/prep"/*.png; do
  base=$(basename "$f" .png)
  "$ROOT/tools/audiveris" -batch -export -output "$WORK/omr1" "$f" > "$WORK/omr1/$base.log" 2>&1 || true
  python3 "$ROOT/tools/omr_mask.py" "$WORK/omr1/$base.omr" "$WORK/masked/$base.png" >/dev/null
done

python3 "$ROOT/tools/build_song.py" "$SONG" \
  --pages "$PAGES" --prep "$WORK/prep" --omr "$WORK/omr1" --masked "$WORK/masked" \
  --strips "$WORK/strips" --out "$WORK/song.json"

# The song as notation, so that it can be drawn next to the printed page and
# the two compared.
python3 "$ROOT/tools/song_to_vf.py" "$WORK/song.json" "$WORK/vf.json"
