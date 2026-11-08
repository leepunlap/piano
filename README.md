# Piano — smart accompaniment system

A system that **learns how you play**, then **quantises and corrects** your
performance against a fixed song — without making you sound like a machine.

```
  live MIDI ──► score following ──► learn your timing/dynamics ──► correct ──► audio
   (keyboard)     (DTW / online)        (per-note offsets, vel curve)   (quantise + repair)
```

## Status

| Component | State |
|---|---|
| MIDI read/write | ✅ working, round-trip verified |
| Offline alignment (DTW) | ✅ working |
| Online tracker (real-time) | ✅ working, 16 tests |
| Player profile learning | ✅ working, persists to JSON |
| Quantisation (humanised + strict) | ✅ working |
| Pitch correction | ✅ working |
| Velocity curve | ✅ working |
| Audio render (sfizz) | ✅ working, pitch-accurate to ±0.15 semitone |
| Live MIDI capture | ✅ implemented (no MIDI device attached to this Mac to test with) |

**43 tests passing** (`python3 -m tests.run_all`).

## Quick start

```bash
cd /Users/blee/src/piano

# 1. Render a MIDI file to audio (Salamander Grand Piano)
python3 -m piano.render songs/prelude.mid work/prelude.wav

# 2. Learn from a performance
python3 -m piano.cli learn songs/prelude.mid work/prelude.take.mid --player demo --song prelude

# 3. Correct it
python3 -m piano.cli correct songs/prelude.mid work/prelude.take.mid \
    --player demo --song prelude --division eighth --strict \
    --out work/prelude.corrected.mid

# 4. Inspect what was learned
python3 -m piano.cli profile --player demo --song prelude

# 5. Run the self-test
python3 -m tests.run_all
```

## The two correction modes

This is the central design decision, and it is worth understanding.

| Mode | What it does | When to use |
|---|---|---|
| **humanised** (default) | Removes your *jitter* but keeps your learned systematic offset — you keep playing slightly late, just consistently | Practising, keeping your own feel |
| **strict** (`--strict`) | Snaps everything exactly onto the grid | The output must match a fixed song precisely |

Measured on the bundled `prelude` example (a simulated player who is 43 ms late with ±21 ms jitter):

```
  raw take     cov 84.4% | err  +42.7ms sd 42.6 | wrong 3 | max off-grid 110.8ms
  humanised    cov 87.5% | err  +37.2ms sd 42.2 | wrong 0 | max off-grid 106.7ms
  strict       cov 96.9% | err   -4.3ms sd  1.4 | wrong 1 | max off-grid   4.8ms
```

## Architecture

```
piano/
  core/
    notes.py     Note / Sequence - one representation for score and performance
    tempo.py     TempoMap, Grid - musical time and quantisation grids
    align.py     DTW score following (offline), key detection
    tracker.py   OnlineTracker - streaming note matching for real-time use
    profile.py   PlayerProfile - learned per-position timing + velocity curve
    correct.py   Quantisation, pitch repair, velocity re-mapping
  io/
    midi_io.py   MIDI read/write (mido), tempo-map aware
    recorder.py  Live capture from a MIDI keyboard
    audio.py     sfizz rendering, level analysis
  render.py      CLI: MIDI -> WAV
  cli.py         CLI: render / learn / correct / profile / ports / demo
```

### Why the pieces are separated

- **`notes.py`** — one `Note` type for both the score and the take means the
  alignment stage can compare them directly. The strike list (onsets in time
  order) is the canonical index space used by profiles, alignment and
  corrections alike.
- **`align.py` vs `tracker.py`** — offline DTW is exact and needs the whole
  take; the online tracker decides *now* which score note you just played.
  Both use the same cost model so they agree.
- **`profile.py`** — stores per-position timing statistics (Welford's
  algorithm) rather than one global offset, because players are not
  uniformly late: the melody may be late while the accompaniment is early.
  That is exactly what the bundled example demonstrates.

## Instrument

Two open-source SFZ libraries are bundled under `samples/` (see
`samples/README.md` for provenance and licensing):

- **Salamander Grand Piano V3** — CC-BY-3.0, Yamaha C5, 16 velocity layers
- **Splendid Grand Piano** — Public Domain (AKAI), Steinway, 4 velocity layers

Both are **SFZ v2 + ARIA extensions**. `sfizz` (BSD-2, open source) handles
the core SFZ opcodes but not the full ARIA superset — string resonance and
half-pedalling are missing there. For the reference sound, use **sforzando**
(free but closed-source) or accept the loss in sfizz.

`bin/sfizz_render` and `bin/lib/libsfizz.dylib` come from the
[sfizz 1.2.3 macOS release](https://github.com/sfztools/sfizz/releases).

## Recording a live take

```python
from piano.io.recorder import MidiRecorder, RecorderConfig

recorder = MidiRecorder(RecorderConfig(count_in_beats=4, count_in_bpm=92))
result = recorder.record(port="Keyboard", duration=13.0)
result.sequence   # -> Sequence of what you actually played
```

The recorder timestamps against a monotonic clock (not the MIDI clock),
tolerates unmatched note-on/note-off, and counts in before arming.

## Known gaps

- **Live capture is untested against a real device** — no MIDI keyboard is
  attached to this Mac. The code path is complete but needs a device to
  validate latency and drop handling.
- **The online tracker is not yet wired into a live correction loop.** It is
  tested in isolation and agrees with the offline aligner, but the loop that
  would correct a note while it is still sounding does not exist yet.
- **`add_missing` is conservative by design** — it only fills a gap when
  neighbouring notes confirm the context, so it under-fills rather than
  inventing notes.
- **Sofia MZ's piano libraries could not be obtained.** Her official site
  (Wix) is unpublished and her SourceForge projects are deleted; see
  `samples/README.md` for the full evidence trail.

## Licensing note

Salamander is **CC-BY-3.0** — attribution is required if you redistribute it.
If you ship this system with bundled sounds, credit Alexander Holm /
Chisato Yamauchi / the SFZ Instruments project.
