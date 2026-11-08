#!/bin/bash
# Read the chord symbols off every page of a song.
#   ocr_song_chords.sh <song> <work-dir>
set -euo pipefail
SONG="$1"; WORK="$2"; ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$WORK/chords.jsonl"; : > "$OUT"
for omr in "$WORK/omr1"/*.omr; do
  base=$(basename "$omr" .omr)
  python3 - "$omr" "$base" <<'PY'
import sys, zipfile
omr, base = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(omr) as z:
    name = next(n for n in z.namelist() if n.endswith('BINARY.png'))
    open(f'/tmp/_bin_{base}.png','wb').write(z.read(name))
PY
  python3 "$ROOT/tools/ocr_chords.py" "$omr" "/tmp/_bin_$base.png" --out "/tmp/_ch_$base.json" >/dev/null 2>&1 || true
  python3 - "$OUT" "/tmp/_ch_$base.json" "$base" <<'PY'
import json, sys
out, src, page = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    items = json.load(open(src))
except Exception:
    items = []
with open(out,'a') as f:
    for it in items:
        it['page'] = page
        f.write(json.dumps(it)+'\n')
PY
done
python3 -c "
import json,sys
items=[json.loads(l) for l in open('$OUT')]
json.dump(items, open('$WORK/chords.json','w'), indent=1)
print(len(items), 'chords')
"
