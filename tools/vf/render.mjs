/**
 * Render recognised notes as real notation with VexFlow 5.
 *
 * The point of this is not decoration.  A recognised score is a guess, and a
 * guess is easy to believe when it is a list of numbers and easy to doubt
 * when it is drawn as music: a bar that should hold four quarter notes and
 * holds one crotchet and three rests looks wrong at a glance, in a way that
 * no amount of staring at JSON will show you.
 *
 * So the reading is engraved, and engraved against the printed page, and the
 * two are compared bar by bar.
 *
 * Input JSON:
 *   {
 *     "title": "Besame Mucho",
 *     "key": "G",                  // VexFlow key signature name
 *     "time": "4/4",
 *     "systems": [
 *       { "measures": [
 *           { "beats": 4, "notes": [
 *               { "key": "e/5", "duration": "h", "dotted": false, "rest": false }
 *           ]}
 *       ]}
 *     ]
 *   }
 *
 * Usage: node render.mjs <input.json> <output.svg> [--width N] [--bars-per-line N]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { JSDOM } from "jsdom";

// VexFlow 5's SVG backend wants an HTMLDivElement to draw into, so it needs a
// DOM even when it is running in Node.  The globals have to be in place
// *before* vexflow is imported, hence the dynamic import below.
const dom = new JSDOM("<!DOCTYPE html><html><body><div id='score'></div></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.HTMLDivElement = dom.window.HTMLDivElement;
globalThis.SVGElement = dom.window.SVGElement;
globalThis.Node = dom.window.Node;
globalThis.Element = dom.window.Element;

const { Renderer, Stave, StaveNote, Voice, Formatter, Dot } = await import("vexflow/bravura");

const VEX_DURATIONS = new Set([
  "w", "h", "q", "8", "16", "32",
  "wr", "hr", "qr", "8r", "16r", "32r",
]);

function argOf(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? Number(process.argv[i + 1]) : fallback;
}

/** Build one StaveNote from a plain description. */
function toStaveNote(note) {
  let duration = note.duration || "q";
  if (!VEX_DURATIONS.has(duration)) duration = "q";
  const staveNote = new StaveNote({
    keys: [note.rest ? "b/4" : note.key || "c/4"],
    duration,
  });
  if (note.dotted) Dot.buildAndAttach([staveNote], { all: true });
  return staveNote;
}

/**
 * VexFlow's Formatter spreads notes to fill the stave, but it does not keep
 * their *proportions*: four crotchets and one minim come out the same width.
 * A bar with a crotchet and three quavers must look like that, because that
 * is how the print looks and the comparison is the whole point.
 */
function layoutProportionally(voice, stave, notes, formatter) {
  const durations = notes.map((n) => {
    const base = (n.duration || "q").replace("r", "");
    const value = { w: 4, h: 2, q: 1, 8: 0.5, 16: 0.25, 32: 0.125 }[base] ?? 1;
    return n.dotted ? value * 1.5 : value;
  });
  const total = durations.reduce((a, b) => a + b, 0);
  const usable = stave.getNoteEndX() - stave.getNoteStartX();
  const widths = durations.map((d) => (total > 0 ? (d / total) * usable : usable / durations.length));
  formatter.joinVoices([voice]).format([voice], widths);
}

function render(doc, outPath) {
  const width = argOf("--width", 1500);
  const barsPerLine = argOf("--bars-per-line", 4);
  const systems = doc.systems || [];

  // Lay out first: how many rows, how tall.
  const rows = [];
  for (let i = 0; i < systems.length; i += barsPerLine) rows.push(systems.slice(i, i + barsPerLine));

  const staveHeight = 90;
  const rowHeight = staveHeight + 34;
  const marginTop = 24;
  const marginBottom = 16;
  const height = marginTop + rows.length * rowHeight + marginBottom;

  const renderer = new Renderer(document.getElementById("score"), Renderer.Backends.SVG);
  renderer.resize(width, height);
  const ctx = renderer.getContext();
  ctx.setFillStyle("#111");

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
          const staveNotes = notes.map(toStaveNote);
          const voice = new Voice({ numBeats: measure.beats || 4, beatValue: 4 });
          voice.setStrict(false);
          voice.addTickables(staveNotes);
          const formatter = new Formatter();
          layoutProportionally(voice, stave, notes, formatter);
          voice.draw(ctx, stave);
        }
        x += barWidth;
      });
    });
  });

  // VexFlow 5 draws into a real SVG element inside the container div, so the
  // markup is taken from the DOM rather than from a toSVG() helper.
  const svg = document.getElementById("score").querySelector("svg");
  if (!svg) throw new Error("VexFlow produced no SVG");
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  writeFileSync(outPath, svg.outerHTML);
  return { width, height, systems: systems.length, measures: systems.reduce((n, s) => n + s.measures.length, 0) };
}

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) {
  console.error("usage: node render.mjs <input.json> <output.svg> [--width N] [--bars-per-line N]");
  process.exit(2);
}
const doc = JSON.parse(readFileSync(input, "utf8"));
const info = render(doc, output);
console.log(`${output}  ${info.width}x${info.height}, ${info.systems} systems, ${info.measures} measures`);
