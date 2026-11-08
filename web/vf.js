/**
 * Draw the recognised notes as notation, in the browser, with VexFlow 5.
 *
 * This is the same drawing code as tools/vf/render.mjs, but running where the
 * Bravura font and the text metrics actually exist: a server-side SVG comes
 * out with every notehead a blank box, because the font is a web font and the
 * measurements come from a canvas.
 *
 * The point of the drawing is the comparison: the recognised notes are
 * engraved, and the engraved result sits under the printed page, so that the
 * two can be read against each other bar by bar.  A recognised score is a
 * guess, and a guess is much easier to doubt when it is drawn as music.
 */

export const VexDuration = {
  whole: "w", half: "h", quarter: "q", eighth: "8", sixteenth: "16", thirtysecond: "32",
};

/** "e/5" -> a VexFlow pitch key; anything odd falls back to middle C. */
function toKey(spec) {
  if (typeof spec !== "string") return "c/4";
  const m = /^([a-g])([#b]*)\/(-?\d)$/.exec(spec.trim());
  if (!m) return "c/4";
  return `${m[1]}${m[2]}/${m[3]}`;
}

/** VexFlow duration strings, with the "r" suffix for a rest. */
function toDuration(spec, isRest) {
  let d = VexDuration[String(spec || "").toLowerCase()];
  if (!d) d = "q";
  return isRest ? `${d}r` : d;
}

/**
 * The beats a note is worth, for the proportional layout.  VexFlow's
 * Formatter spreads notes to fill the stave without regard to their
 * proportions, so four crotchets and a minim come out the same width; a bar
 * with a crotchet and three quavers has to look like that, because that is
 * how the print looks and comparing the two is the whole point.
 */
function beatValue(note) {
  const base = String(note.duration || "quarter").toLowerCase();
  const value = {
    whole: 4, half: 2, quarter: 1, eighth: 0.5, sixteenth: 0.25, thirtysecond: 0.125,
  }[base] ?? 1;
  return note.dotted ? value * 1.5 : value;
}

/**
 * Draw one document into a container.
 *
 * `doc` is { title, key, time, systems: [ { measures: [ { beats, notes } ] } ] }.
 * Returns the SVG element.
 */
export async function renderScore(doc, container, options = {}) {
  const { Renderer, Stave, StaveNote, Voice, Formatter, Dot } = await import(
    new URL("../vendor/vexflow/entry/vexflow-bravura.js", import.meta.url).href
  );

  const width = options.width || container.clientWidth || 1200;
  const barsPerLine = options.barsPerLine || 4;
  const systems = doc.systems || [];

  const rows = [];
  for (let i = 0; i < systems.length; i += barsPerLine) rows.push(systems.slice(i, i + barsPerLine));

  const staveHeight = 90;
  const rowHeight = staveHeight + 40;
  const marginTop = options.title ? 34 : 14;
  const height = marginTop + rows.length * rowHeight + 10;

  container.innerHTML = "";
  const renderer = new Renderer(container, Renderer.Backends.SVG);
  renderer.resize(width, height);
  const ctx = renderer.getContext();
  ctx.setFillStyle("#111");
  ctx.setStrokeStyle("#111");

  if (options.title) {
    ctx.setFont(" Academico", 15);
    ctx.fillText(options.title, 20, 22);
  }

  rows.forEach((row, rowIndex) => {
    const top = marginTop + rowIndex * rowHeight;
    const measureCount = row.reduce((n, sys) => n + sys.measures.length, 0);
    const barWidth = (width - 40) / Math.max(1, measureCount);

    let x = 20;
    row.forEach((sys) => {
      sys.measures.forEach((measure, measureIndex) => {
        const stave = new Stave(x, top, barWidth, 5);
        if (rowIndex === 0 && measureIndex === 0) {
          stave.addClef("treble");
          if (doc.key) stave.addKeySignature(doc.key);
          if (doc.time) stave.addTimeSignature(doc.time);
        }
        stave.setContext(ctx).draw();

        const notes = measure.notes || [];
        if (notes.length) {
          const staveNotes = notes.map((note) => {
            const sn = new StaveNote({
              keys: [note.rest ? "b/4" : toKey(note.key)],
              duration: toDuration(note.duration, note.rest),
            });
            if (note.dotted) Dot.buildAndAttach([sn], { all: true });
            if (note.accidental) {
              // VexFlow draws the accidental from the key, but a printed
              // accidental that is not in the key signature has to be said.
              const { Accidental } = window.__vexAccidental || {};
              if (Accidental) sn.addModifier(new Accidental(note.accidental), 0);
            }
            return sn;
          });
          const voice = new Voice({ numBeats: measure.beats || 4, beatValue: 4 });
          voice.setStrict(false);
          voice.addTickables(staveNotes);

          const values = notes.map(beatValue);
          const total = values.reduce((a, b) => a + b, 0);
          const usable = stave.getNoteEndX() - stave.getNoteStartX();
          const widths = values.map((v) =>
            total > 0 ? (v / total) * usable : usable / Math.max(1, values.length)
          );

          const formatter = new Formatter();
          formatter.joinVoices([voice]).format([voice], widths);
          voice.draw(ctx, stave);
        }
        x += barWidth;
      });
    });
  });

  return container.querySelector("svg");
}

/** Load a JSON file. */
export async function loadJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}
