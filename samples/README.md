# Piano sample libraries

Working set of open-source SFZ piano libraries for the accompaniment system.

## ✅ Downloaded

| Library | Path | Size | License | Notes |
|---|---|---|---|---|
| Salamander Grand Piano V3 | `SalamanderGrandPiano/` | ~1.4 GB | CC-BY-3.0 | Yamaha C5, 48 kHz/24-bit, 16 velocity layers, ARIA extensions. Best-supported free piano; use as the default engine. |
| Splendid Grand Piano | `SplendidGrandPiano/` | ~154 MB | Public Domain (AKAI) | Steinway, 4 velocity layers, FLAC. Needs an ARIA-compatible player (sforzando) for string resonance + half-pedal. |

Source: <https://github.com/sfzinstruments> (SFZ Instruments project).

### Player note

Both use SFZ v2 + ARIA extensions, so they sound best in **sforzando** or another
ARIA-compatible engine. `sfizz` (BSD-2, open source) supports the core SFZ opcodes
but not the full ARIA superset — expect string resonance / half-pedal to be
missing there. Check `sfzinstruments.github.io` compatibility notes per instrument.

## ❌ Sofia MZ "European Concert Grands" — upstream is gone

Sofia MZ's collection (Rivai D'Arsiè / Hamburg Steinway D, Borgo Del Cervo /
Bösendorfer Imperial, Santa Marta / Fazioli F308, Contessa Bazzi / Blüthner Model 1,
Castel Volturno / C. Bechstein D282, Monte Mario / Estonia 274) was distributed from
two hosts, and **both are now dead**:

| Host | What was there | Status now |
|---|---|---|
| `luciphercode.wixsite.com/sofia-mz` | 2.0 stable releases (5 pianos) | **404** — Wix site unpublished. Only the Wayback capture of the *homepage* (2023-11-28) survives; no subpages or binaries were ever archived. |
| `sourceforge.net/projects/{santa-marta,contessa-bazzi,castel-volturno,borgo-del-cervo,rivai-d-arsi}-grand-piano` | 1.0 releases + per-piano 2.0 files | **Projects deleted.** SF serves `/directory/…` instead of the project; `files/latest/download` → 404. Verified against a live control project. |
| `musical-artifacts.com` (artifacts 3237/3275/3282/3292/3316/3359/3360/3361/3362) | catalogue + mirror links | Metadata + JSON API still live, but every mirror points at the deleted SourceForge files. |
| Dropbox links posted in PianoClack | user-modified SFZ files | "File Deleted" |

Notes for the record:

- **License is CC-BY-3.0, not CC0.** Her own announcement says the *sample libraries*
  are "Creative Commons Attribution", while *music/sound-effects made with them* are
  CC0. Attribution is required when shipping the samples. (Earlier notes in this
  project said CC0 — that was wrong.)
- SourceForge and Musical Artifacts still list the files in their metadata, so
  search results will keep pointing at dead links.
- If she republishes, the likely canonical home is a new Wix/SourceForge URL; the
  Musical Artifacts JSON (`https://musical-artifacts.com/artifacts.json?q=sofia`)
  is the best place to re-check, since it mirrors her mirror list.

## Next step

Wire up a player (sfizz or sforzando) and the MIDI pipeline in this repo against
`SalamanderGrandPiano/` as the reference instrument.
