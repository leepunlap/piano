# 演出譜集 — Smart accompaniment for the 2026-11-15 performance

Four songs, scanned as photographs of printed sheet music, turned into a
readable score you can play from and a piece you can play along with.

| 曲目 | 調 | ♩= | 來源 |
|---|---|---|---|
| Besame Mucho | G 大調 | 45 | MuseScore 螢幕擷取 |
| 印象 | E 大調 → D 小調 | 84 | 書本印刷頁 |
| 鐵塔凌雲 | 降 B 大調 | 97 | 書本印刷頁 |
| 沉默是金 | C 大調 | 60 | 書本印刷頁 |

## What is here

```
web/                      the pages — open web/index.html
  index.html              the four songs
  <song>.html             one song: the score, the chords, the lyrics, a player
  strips/                 the music, one image per system
  audio/<song>.wav        the piece, rendered through sfizz

tools/
  prep_images.py          clean up a scanned page: deskew, flatten the paper
  omr_bands.py            read the staff geometry out of an Audiveris book
  omr_mask.py             take the clutter off a page, leaving the melody staff
  reader.py               read the noteheads, stems, beams and flags
  read_page.py            read a whole page
  compare.py              show what was read against what is printed
  ocr_chords.py           read the chord symbols
  build_song.py           assemble a song: strips, notes, chords
  song_to_midi.py         make a MIDI of the melody and the chords
  make_site.py            build the web pages
  omr_song.sh             the whole pipeline for one song

scores/                   the scanned scores, and the raw photographs
songs/                    (built) one folder per song
```

## The pipeline

```
scores/*.pdf
   │  pdftoppm 200 dpi          the PDF holds a 200 dpi grey JPEG of the page
   ▼
work/omr/pages/<song>/
   │  prep_images.py            strip the editor's header, remove the publisher
   │                            banner, level the staves, flatten the paper
   ▼
work/songs/<song>/prep/
   │  audiveris (pass 1)        find the staves.  Audiveris's grid step is
   │                            very good at this, and it models every staff
   │                            line as a polyline, so it copes with the warp
   │                            of a photographed page.
   ▼
work/songs/<song>/omr1/
   │  omr_mask.py               keep the melody staff, throw the rest away
   ▼
work/songs/<song>/masked/
   │  reader.py                 find the noteheads and read them
   ▼
work/songs/<song>/song.json    the song: the music as images, and as notes
   │  ocr_chords.py             the chord symbols
   │  song_to_midi.py           the melody and the chords → MIDI
   │  render (sfizz)            MIDI → WAV
   ▼
web/
```

## Why the reading is done this way

Audiveris was tried first, on the whole page.  It finds the staves perfectly —
that is what its grid step is for — but it cannot read the notes, because a
score page is full of things that are not notes:

* **chord symbols** above the staff, which look like noteheads;
* **guitar chord diagrams**, which are little grids of lines and look exactly
  like staves;
* **lyrics** between the staves, which look like beams;
* **fingering numbers**, which are small round blobs and look like noteheads.

It read them all as notes, and the result was worse than nothing — a wrong
reference is worse than no reference, because the practice system would then
"correct" a correct performance into an incorrect one.

So the geometry is taken from Audiveris and the notes are read separately, on
a page with the clutter taken off.  The note reading itself uses three
measurements of the shape of the ink — how much of a notehead-sized ellipse is
full, how much of its middle is full, and how wide the thing is — because a
notehead is the only thing on a staff that is solid all the way through, or
solid at the rim and hollow in the middle, and about one and a third
interlines across.

`tools/compare.py` draws what was read back onto the printed page.  That
picture is the evidence for the reading, and it is the thing to look at when
a bar looks wrong.

## What is exact and what is approximate

**Exact** — the score on the web page is the printed score, cleaned up.  It
is the original notation, not a redrawing, so the chords above the staff and
the lyrics below it are the ones that were printed.  The bars, the key, the
time signature and the fingering are all as printed.

**Approximate** — the melody that is played comes from the reading.  It gets
most of the notes and most of the rhythm right, but not all of it: the dense
passages, where the beams are steep and the notes are close together, are the
hardest and the least certain.  The chord symbols are read by OCR and come
out right for Besame Mucho and 沉默是金; the two book scores print theirs
inside boxes next to guitar diagrams, and those have not been read yet.

The point of the page is therefore to be checked against the printed score,
and the point of `tools/compare.py` is to make that checking quick.

## Running it

```bash
# the whole pipeline for one song
tools/omr_song.sh besame work/omr/pages/besame work/songs/besame 3.0
tools/ocr_song_chords.sh besame work/songs/besame
tools/song_to_midi.py work/songs/besame/song.json work/songs/besame/song.mid \
    --chords work/songs/besame/chords.json --bpm 45
tools/make_site.py --songs work/songs --out web

# look at it
cd web && python3 -m http.server 8765
```

## Notes on the sources

* The scores were photographed off a screen (Besame Mucho) and off a printed,
  spiral-bound book (the other three).  沉默是金's book pages 54 and 55 are
  the same photograph, so that page is only read once.
* Audiveris is under the AGPL; its macOS build is in `tools/Audiveris.app`.
* Tesseract reads the chord symbols; the language data is in `tools/tessdata`.
* The piano is the Salamander Grand in `samples/` (CC-BY-3.0), played through
  sfizz.
