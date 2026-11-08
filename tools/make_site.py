#!/usr/bin/env python3
"""Build the web pages for the songs.

One page per song, and an index.  Each song page shows:

  * the music, as printed, one system per strip — cleaned up, with the chords
    above the staff and the lyrics below it, readable on a screen;
  * a play button, and a marker that moves along with the music;
  * the melody as data, so that the page can be wired to a keyboard later.

The score on the page is the printed score, not a redrawing of it, so what the
player reads is what the page reads.  The melody underneath it is what was
recognised, and it is what the music is played from.

Usage:
    make_site.py --songs work/songs --out web
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

TITLES = {
    "besame": "Besame Mucho",
    "yinxiang": "印象",
    "tieta": "鐵塔凌雲",
    "chenmo": "沉默是金",
}

TEMPOS = {"besame": 45, "yinxiang": 84, "tieta": 97, "chenmo": 60}

PAGE_CSS = """
:root {
  --ink: #14110e;
  --paper: #fbf8f2;
  --rule: #ddd5c6;
  --accent: #8c2f39;
  --muted: #6d6459;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.6 "Iowan Old Style", "Palatino Linotype", Palatino, "Songti TC", serif;
}
header {
  padding: 28px 32px 18px;
  border-bottom: 1px solid var(--rule);
  display: flex;
  align-items: baseline;
  gap: 20px;
  flex-wrap: wrap;
}
h1 { font-size: 26px; margin: 0; letter-spacing: .02em; }
.sub { color: var(--muted); font-size: 14px; }
main { padding: 24px 32px 64px; }
a { color: var(--accent); }
.back { text-decoration: none; font-size: 14px; color: var(--muted); }

.controls {
  display: flex; align-items: center; gap: 14px;
  margin: 0 0 22px; flex-wrap: wrap;
}
button {
  font: inherit; font-size: 15px;
  background: var(--ink); color: var(--paper);
  border: 0; border-radius: 2px; padding: 9px 20px; cursor: pointer;
}
button.ghost { background: transparent; color: var(--ink); border: 1px solid var(--rule); }
button:disabled { opacity: .4; cursor: default; }
.tempo { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--muted); }
.tempo input { width: 120px; }

.system {
  position: relative;
  margin: 0 0 6px;
  border-bottom: 1px solid var(--rule);
  padding-bottom: 6px;
}
.system img { display: block; width: 100%; height: auto; }
.hit {
  position: absolute; top: 0; bottom: 0; width: 3px;
  background: var(--accent);
  opacity: 0; transition: opacity .12s;
  pointer-events: none;
}
.hit.on { opacity: .85; }

.chords { margin: 10px 0 0; font-size: 14px; color: var(--muted); }
.chords b { color: var(--accent); font-weight: 600; }

.legend { margin-top: 28px; font-size: 13px; color: var(--muted); max-width: 60ch; }
"""


def song_page(song: dict, slug: str, audio_name: str | None) -> str:
    title = TITLES.get(slug, song.get("title", slug))
    bpm = TEMPOS.get(slug, 84)
    parts = [
        "<!doctype html>",
        '<html lang="zh-HK"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{title} · 練習</title>",
        f"<style>{PAGE_CSS}</style></head><body>",
        '<header><a class="back" href="index.html">← 全部曲目</a>',
        f"<h1>{title}</h1>",
        f'<span class="sub">♩ = {bpm} · {len(song.get("systems", []))} 行 · '
        f'{sum(len(s["notes"]) for s in song.get("systems", []))} 個音符</span>',
        "</header><main>",
    ]

    controls = ['<div class="controls">']
    if audio_name:
        controls.append('<button id="play">▶ 播放</button>')
        controls.append('<button id="stop" class="ghost">■ 停止</button>')
    controls.append(
        f'<span class="tempo">速度 <input id="rate" type="range" min="50" max="140" value="{bpm}">'
        f' <b id="rateval">{bpm}</b></span>'
    )
    controls.append("</div>")
    parts.append("".join(controls))

    for system in song.get("systems", []):
        box = system["box"]
        width = max(1, box[2] - box[0])
        parts.append('<div class="system" data-page="%s" data-system="%d">' % (system["page"], system["system"]))
        parts.append(f'<img src="strips/{system["image"]}" alt="system {system["system"]}">')
        parts.append('<div class="hit"></div>')
        parts.append("</div>")

    parts.append(
        '<p class="legend">樂譜為掃描原稿清理後的影像，'
        "和弦與歌詞保留在譜面之上與之下；"
        "播放的是從譜面讀出的旋律。</p>"
    )
    parts.append("</main>")

    # The marks: where each note sits on the page and when it sounds.
    marks = []
    for system in song.get("systems", []):
        box = system["box"]
        width = max(1, box[2] - box[0])
        for note in system["notes"]:
            if note.get("rest"):
                continue
            marks.append(
                {
                    "page": system["page"],
                    "system": system["system"],
                    "left": 100.0 * (note["x"] - box[0]) / width,
                    "beat": note.get("beat", 0.0),
                    "pitch": f"{note['pitch']}{note['octave']}",
                }
            )

    data = {"bpm": bpm, "marks": marks, "audio": audio_name}
    parts.append(
        "<script>const SONG = " + json.dumps(data) + ";</script>"
    )
    parts.append(
        """
<script>
const audio = document.getElementById('play') ? new Audio(SONG.audio) : null;
const playBtn = document.getElementById('play');
const stopBtn = document.getElementById('stop');
const rate = document.getElementById('rate');
const rateVal = document.getElementById('rateval');
const systems = Array.from(document.querySelectorAll('.system'));
const hits = systems.map(s => s.querySelector('.hit'));

if (rate) {
  rate.addEventListener('input', () => {
    rateVal.textContent = rate.value;
    if (audio) audio.playbackRate = SONG.bpm / Number(rate.value);
  });
}

function markAt(beat) {
  // Which system and where on it the music has reached.
  let best = null;
  for (const m of SONG.marks) {
    if (m.beat <= beat + 0.05) best = m;
    else break;
  }
  hits.forEach(h => h.classList.remove('on'));
  if (!best) return;
  const index = systems.findIndex(
    s => s.dataset.page === best.page && s.dataset.system === String(best.system)
  );
  if (index < 0) return;
  const hit = hits[index];
  hit.style.left = best.left + '%';
  hit.classList.add('on');
  const box = systems[index].getBoundingClientRect();
  const top = window.scrollY + box.top;
  if (top < window.scrollY + 60 || top > window.scrollY + window.innerHeight - 120) {
    window.scrollTo({ top: box.top + window.scrollY - 80, behavior: 'smooth' });
  }
}

if (audio) {
  audio.addEventListener('timeupdate', () => {
    const beat = audio.currentTime * (Number(rate.value) / 60);
    markAt(beat);
  });
  audio.addEventListener('ended', () => {
    hits.forEach(h => h.classList.remove('on'));
    playBtn.textContent = '▶ 播放';
  });
  playBtn.addEventListener('click', () => {
    if (audio.paused) { audio.play(); playBtn.textContent = '⏸ 暫停'; }
    else { audio.pause(); playBtn.textContent = '▶ 播放'; }
  });
  stopBtn.addEventListener('click', () => {
    audio.pause(); audio.currentTime = 0; playBtn.textContent = '▶ 播放';
    hits.forEach(h => h.classList.remove('on'));
  });
}
</script>
</body></html>
"""
    )
    return "\n".join(parts)


def index_page(songs: list[tuple[str, dict]]) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="zh-HK"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>2026-11-15 演出譜集</title>",
        f"<style>{PAGE_CSS}</style></head><body>",
        "<header><h1>2026-11-15 演出譜集</h1>",
        '<span class="sub">四首 · 掃描譜的清理與讀譜</span></header><main>',
    ]
    parts.append('<div class="system" style="padding:18px 0">')
    for slug, song in songs:
        title = TITLES.get(slug, slug)
        bpm = TEMPOS.get(slug, 84)
        notes = sum(len(s["notes"]) for s in song.get("systems", []))
        parts.append(
            f'<p style="margin:0 0 6px"><a href="{slug}.html" style="font-size:20px">'
            f"{title}</a> <span class='sub'>♩ = {bpm} · {notes} 音符</span></p>"
        )
    parts.append("</div>")
    parts.append(
        '<p class="legend">每一頁是該曲的樂譜、歌詞與和弦，'
        "可以由頭到尾播放；之後再接上鍵盤，讓你改速度、改風格而保留和弦。</p>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--songs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    strips_dir = args.out / "strips"
    strips_dir.mkdir(exist_ok=True)
    audio_dir = args.out / "audio"
    audio_dir.mkdir(exist_ok=True)

    found: list[tuple[str, dict]] = []
    seen_pages: dict[str, str] = {}
    for slug in TITLES:
        path = args.songs / slug / "song.json"
        if not path.exists():
            continue
        song = json.loads(path.read_text(encoding="utf-8"))
        found.append((slug, song))

        source_strips = args.songs / slug / "strips"
        for system in song.get("systems", []):
            src = source_strips / system["image"]
            if src.exists():
                shutil.copy(src, strips_dir / system["image"])

        audio_name = None
        wav = args.songs / slug / "song.wav"
        if wav.exists():
            shutil.copy(wav, audio_dir / f"{slug}.wav")
            audio_name = f"audio/{slug}.wav"

        (args.out / f"{slug}.html").write_text(
            song_page(song, slug, audio_name), encoding="utf-8"
        )
        print(f"{slug}.html")

    (args.out / "index.html").write_text(index_page(found), encoding="utf-8")
    print("index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
