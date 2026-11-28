// Runs skills/excel-visual/scripts/visual-from-selection.js in plain Node with a stub Excel, the way an agent runs it
// (as the body of an async function), and keeps the workbook it asks Excel to create.
//
//   node excel_stub.mjs <script.js> <scenario.json> <out.xlsx>
//
// scenario.json: {"values": rows of the selected range, "text": rows of the text the cells show (default: each value as
// text), "address": "Sheet1!A1:C9", "title": ..., "costs": ..., "visual": ...}.
// The three settings at the top of the script are set by exact replacement of their lines; a missing or repeated
// line is an error. Prints JSON: {"created": number of createWorkbook calls, "error": message or null, "log": [...]}.
import fs from "fs";

const [scriptPath, scenarioPath, outPath] = process.argv.slice(2);
let source = fs.readFileSync(scriptPath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

const settings = { TITLE: scenario.title ?? null, COSTS: scenario.costs ?? false, VISUAL: scenario.visual ?? "auto" };
for (const [name, value] of Object.entries(settings)) {
  const line = new RegExp("^const " + name + " = [^\\n]*$", "gm");
  const found = source.match(line) || [];
  if (found.length !== 1) {
    console.log(JSON.stringify({ created: 0, error: "settings line for " + name + " found " + found.length + " times", log: [] }));
    process.exit(2);
  }
  source = source.replace(line, () => "const " + name + " = " + JSON.stringify(value) + ";");
}

const log = [];
let created = 0;
let bytes = null;
globalThis.console = { log: (...a) => log.push(a.join(" ")) };
globalThis.Excel = {
  createWorkbook: async (base64) => { created += 1; bytes = Buffer.from(base64, "base64"); },
  run: async (fn) => fn({
    workbook: { getSelectedRange: () => ({ values: scenario.values, text: scenario.text || scenario.values.map((r) => r.map((v) => (v === null || v === undefined ? "" : String(v)))),
      address: scenario.address || "Sheet1!A1", load() {} }) },
    sync: async () => {},
  }),
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
let error = null;
try {
  await new AsyncFunction(source)();
} catch (e) {
  error = e.message;
}
if (bytes) fs.writeFileSync(outPath, bytes);
process.stdout.write(JSON.stringify({ created, error, log }) + "\n");
