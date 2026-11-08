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
  omr_sheet.py            read the music out of Audiveris's own analysis
  reader.py               read the noteheads, stems, beams and flags (kept)
  read_page.py            read a whole page (kept)
  compare.py              show what was read against what is printed
  ocr_chords.py           read the chord symbols
  build_song.py           assemble a song: strips, notes, chords
  song_to_vf.py           the song as notation data
  song_to_midi.py         make a MIDI of the melody and the chords
  vf/render.mjs           draw the notation with VexFlow, on the server
  make_site.py            build the web pages
  omr_song.sh             the whole pipeline for one song
  build_all.sh            the whole pipeline for all four songs

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
   │  omr_sheet.py              read the music out of Audiveris's sheet XML
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

So a reader of one's own was written, to find noteheads by measuring the
shape of the ink.  It found the notes, but it found only about a third of
them, and the bar lines it found were not the bar lines: a stem is as long as
a bar line, and it took the measure of a bar from the distance between them,
so the beats were wrong and the piece came out as noise.

**The answer turned out to be that Audiveris had already done the hard part
and thrown it away.**  Its `.omr` file is not just a picture of the score;
it is Audiveris's whole analysis, in a sheet XML, and it holds:

* the staff lines, as polylines, so the warp of a photograph is carried
  through rather than guessed at;
* the measures, as `stack` elements with a left and a right edge, so the bar
  lines are known exactly;
* the slots of each measure with their time offsets, so the rhythm is known
  exactly;
* every note head with the staff-relative step it sits on — a geometric
  measurement, and therefore trustworthy;
* the rests and their shapes, the augmentation dots, the beams, the clefs,
  the key signatures and the time signatures.

What Audiveris does *not* know is the note **name**: it records how far up
the staff a head sits, not what the note is called.  Turning that into a
name takes three things — the clef, which says where the staff's middle line
falls in absolute terms; the step itself, which gives the letter; and the key
signature, which gives the alteration.  `tools/omr_sheet.py` does exactly
that, and nothing else.

So the reading is Audiveris's, with the note names worked out afterwards.

### Checking it

A recognised score is a guess, and a guess is easy to believe when it is a
list of numbers and easy to doubt when it is drawn as music.  So the reading
is engraved with **VexFlow** and printed under the scanned page, bar by bar:
`web/verify.html` shows the printed system and the redrawn one together, and
any bar where they differ is a bar to look at.

VexFlow is the same library MusicApp6 uses.  It is a renderer, not a reader:
it cannot look at a photograph and say what the notes are.  What it can do is
take the reading and draw it as notation, which is exactly what is needed to
judge a reading.

## What is exact and what is approximate

**Exact** — the score on the web page is the printed score, cleaned up.  It
is the original notation, not a redrawing, so the chords above the staff and
the lyrics below it are the ones that were printed.  The bars, the key, the
time signature and the fingering are all as printed.

**Approximate** — the melody that is played comes from the reading.  The
bar lines, the rhythm, the rests and the note positions come from Audiveris
and are right; the note *names* are worked out from the clef and the key
afterwards, and are right for the clean digital score and mostly right for
the photographed ones, where the paper is bent and the staff is not quite
where it looks.  The chord symbols are read by OCR and come out right for
Besame Mucho and 沉默是金; the two book scores print theirs inside boxes next
to guitar diagrams, and those have not been read yet.

The point of the page is therefore to be checked against the printed score,
and the point of `web/verify.html` is to make that checking quick.

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
