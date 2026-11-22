#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * Tests for write_measures (measures.js).
 *
 * Invented data in temp directories. Each project is written by write_project, and one table file
 * is added by hand with CRLF line endings, two tables in one file, and the reserved names KPI and
 * Calculations. Every write is checked three ways: the text added is exactly what was returned,
 * every other byte of the file is unchanged, and no other file in the project changed.
 *
 *   node test-measures.js
 */

const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const project = require("./project.js");
const measures = require("./measures.js");
const srv = require("./server.js");
const tmdl = require("./tmdl.js");
const T = require("./tmdl-tree.js");

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (e) { results.push(["FAIL", name, String(e && e.stack || e).split("\n").slice(0, 2).join(" | ").slice(0, 220)]); }
};
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-wm-"));
const RULES = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "skills", "report-authoring", "rules.json"), "utf8")).rules;
const deps = { validateReport: srv.validateReport, resolveProject: srv.resolveProject, loadRules: srv.loadRules };
const add = (args) => measures.writeMeasures(args, deps);

const CSV = ["Date,Region,Scenario,Amount",
  ...["2024", "2025"].flatMap((y) => ["01", "02", "03"].flatMap((m) => [
    `${y}-${m}-01,North,Actual,${Number(m) * 10}`, `${y}-${m}-01,South,Actual,${Number(m) * 7}`,
    `${y}-${m}-01,North,Budget,${Number(m) * 11}`]))].join("\n") + "\n";

// A table file written by hand: CRLF, two tables in one file, reserved names.
const EXTRA = [
  "table KPI", "",
  "\tcolumn Name", "\t\tdataType: string", "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Name]", "",
  "\tpartition KPI = calculated", "\t\tmode: import", "\t\tsource =", "\t\t\t\tDATATABLE ( \"Name\", STRING, { {\"Revenue\"}, {\"Cost\"} } )", "",
  "/// Items for the side panel", "table Calculations", "",
  "\tcolumn Item", "\t\tdataType: string", "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Item]", "",
  "\tpartition Calculations = calculated", "\t\tmode: import", "\t\tsource =", "\t\t\t\tDATATABLE ( \"Item\", STRING, { {\"A\"} } )", "",
].join("\r\n");

let n = 0;
function base({ extra = false } = {}) {
  const dir = path.join(TMP, `p${++n}`);
  fs.mkdirSync(path.join(dir, "src"), { recursive: true });
  fs.writeFileSync(path.join(dir, "src", "sales.csv"), CSV);
  const out = path.join(dir, "proj");
  project.writeProject({ name: "Shop", outPath: out, source: { path: path.join(dir, "src") } }, deps);
  if (extra) fs.writeFileSync(path.join(out, "Shop.SemanticModel", "definition", "tables", "Extra.tmdl"), EXTRA);
  return out;
}
const tablePath = (p, f) => path.join(p, "Shop.SemanticModel", "definition", "tables", f);
function hashes(dir, base_ = dir, out = {}) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) hashes(p, base_, out);
    else out[path.relative(base_, p)] = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
  }
  return out;
}
/** The file after the call is the file before with `added` inserted at one place, nothing else. */
function assertInsertedOnly(before, after, added) {
  assert.strictEqual(after.length, before.length + added.length, "length differs by more than the added text");
  const i = after.indexOf(added);
  assert.ok(i >= 0, "the returned text is not in the file");
  assert.ok(Buffer.from(after.slice(0, i) + after.slice(i + added.length)).equals(Buffer.from(before)), "bytes outside the added text changed");
  return i;
}
function refuses(fn, re) {
  let err = null;
  try { fn(); } catch (e) { err = e; }
  assert.ok(err && err.name !== "AssertionError", "it did not refuse");
  assert.ok(re.test(err.message), `refusal did not match ${re}: ${err.message}`);
}

// --- appending ------------------------------------------------------------------------------

const p1 = base();
const f1 = tablePath(p1, "sales.tmdl");
const before1 = fs.readFileSync(f1, "utf8");
const hashes1 = hashes(p1);
let r1;
check("a measure is appended to the table file and the returned text is exactly what was added", () => {
  r1 = add({ projectPath: p1, table: "sales", measures: [{ name: "Amount YTD",
    expression: "TOTALYTD (\n    [Amount AC],\n    Calendar[Date]\n)", formatString: "#,0", description: "Actuals, year to date", displayFolder: "Time" }] });
  const after = fs.readFileSync(f1, "utf8");
  const at = assertInsertedOnly(before1, after, r1.added);
  assert.strictEqual(at, before1.length, "not appended at the end of the only table's block");
  assert.strictEqual(r1.file, "Shop.SemanticModel/definition/tables/sales.tmdl");
  assert.deepStrictEqual(r1.measures, ["sales[Amount YTD]"]);
});
check("no other file in the project changed", () => {
  const now = hashes(p1);
  for (const [k, h] of Object.entries(hashes1)) if (!k.endsWith("sales.tmdl")) assert.strictEqual(now[k], h, `${k} changed`);
  assert.deepStrictEqual(Object.keys(now).sort(), Object.keys(hashes1).sort(), "a file was added or removed");
});
check("the measure is laid out body-below-equals, three tabs in, properties at two", () => {
  assert.strictEqual(r1.added, "\n\t/// Actuals, year to date\n\tmeasure 'Amount YTD' =\n\t\t\tTOTALYTD (\n\t\t\t    [Amount AC],\n\t\t\t    'Calendar'[Date]\n\t\t\t)\n\t\tformatString: #,0\n\t\tdisplayFolder: Time\n");
  const tree = T.parseTree(fs.readFileSync(f1, "utf8"));
  assert.deepStrictEqual(tree.faults, []);
  const m = tree.nodes.find((x) => x.type === "measure" && x.name === "Amount YTD");
  assert.strictEqual(T.propValue(m, "formatString"), "#,0");
  assert.deepStrictEqual(m.desc, ["Actuals, year to date"]);
});
check("the model reads the measure back, and every rule still finds nothing that blocks", () => {
  const t = tmdl.readModel(p1).tables.find((x) => x.name === "sales");
  const m = t.measures.find((x) => x.name === "Amount YTD");
  assert.strictEqual(m.expression, "TOTALYTD ( [Amount AC], 'Calendar'[Date] )");
  assert.strictEqual(r1.validation.rulesRun, RULES.length);
  assert.strictEqual(r1.validation.wontOpen + r1.validation.blank, 0, JSON.stringify(r1.validation.findings));
  assert.deepStrictEqual(r1.quoted, ["Amount YTD: Calendar -> 'Calendar'"]);
});
check("a one-line expression still goes below the equals sign", () => {
  const r = add({ projectPath: p1, table: "sales", measures: [{ name: "Rows", expression: "COUNTROWS ( sales )", formatString: "0" }] });
  assert.strictEqual(r.added, "\n\tmeasure Rows =\n\t\t\tCOUNTROWS ( 'sales' )\n\t\tformatString: 0\n");
});

// --- references -----------------------------------------------------------------------------

check("a string literal and a comment that look like references are not read as references", () => {
  const p = base();
  const r = add({ projectPath: p, table: "sales", measures: [{ name: "Note",
    expression: "-- see Nowhere[Gone] and [Missing]\n\"Totals[Amount] from \"\"Nowhere\"\"[x]\" /* Gone[y] */", formatString: "General" }] });
  assert.ok(r.added.includes("\"Totals[Amount] from \"\"Nowhere\"\"[x]\""), "the string was changed");
  assert.deepStrictEqual(r.quoted, []);
});
check("an unresolved column, measure or table is refused with the nearest names, and nothing is written", () => {
  const p = base();
  const f = tablePath(p, "sales.tmdl");
  const was = fs.readFileSync(f);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "X", expression: "SUM ( sales[Amout] )", formatString: "#,0" }] }),
    /sales has no column or measure 'Amout' \(nearest: Amount/);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "X", expression: "[Amont AC] * 2", formatString: "#,0" }] }),
    /no measure or column 'Amont AC' in the model \(nearest: Amount AC/);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "X", expression: "COUNTROWS ( 'Sale' )", formatString: "#,0" }] }),
    /no table 'Sale' in the model \(nearest: sales/);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "X", expression: "[X] + 1", formatString: "#,0" }] }), /refers to itself/);
  assert.ok(fs.readFileSync(f).equals(was), "a refusal changed the file");
});
check("a measure may use another measure written in the same call, and a column named in its own strings", () => {
  const p = base();
  const r = add({ projectPath: p, table: "sales", measures: [
    { name: "Base", expression: "[Amount AC]", formatString: "#,0" },
    { name: "Top", expression: "MAXX ( ADDCOLUMNS ( VALUES ( sales[Region] ), \"Total\", [Base] ), [Total] )", formatString: "#,0" }] });
  assert.ok(r.added.includes("'sales'[Region]") && r.added.includes("[Total]"));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0);
});

// --- reserved names, CRLF, two tables in one file ---------------------------------------------

check("bare reserved table names are quoted and reported; a CRLF file stays CRLF; the right table gets each measure", () => {
  const p = base({ extra: true });
  const f = tablePath(p, "Extra.tmdl");
  const before = fs.readFileSync(f, "utf8");
  const r = add({ projectPath: p, table: "KPI", measures: [{ name: "Selected KPI",
    expression: "SELECTEDVALUE ( KPI[Name] )", formatString: "General" }] });
  assert.strictEqual(r.lineEnding, "CRLF");
  assert.deepStrictEqual(r.quoted, ["Selected KPI: KPI -> 'KPI'"]);
  assert.ok(r.added.includes("'KPI'[Name]") && !/[^\r]\n/.test(r.added) && r.added.includes("\r\n"));
  const after = fs.readFileSync(f, "utf8");
  const at = assertInsertedOnly(before, after, r.added);
  assert.ok(at < after.indexOf("/// Items for the side panel"), "inserted after the next table's description");
  const m = tmdl.readModel(p);
  assert.ok(m.tables.find((t) => t.name === "KPI").measures.some((x) => x.name === "Selected KPI"));
  assert.ok(!m.tables.find((t) => t.name === "Calculations").measures.length);
  assert.ok(!/(^|[^\r])\n/.test(after), "a bare LF entered a CRLF file");
  const r2 = add({ projectPath: p, table: "Calculations", measures: [{ name: "Items", expression: "COUNTROWS ( Calculations )", formatString: "0" }] });
  assert.deepStrictEqual(r2.quoted, ["Items: Calculations -> 'Calculations'"]);
  assert.ok(!r2.validation.findings.some((x) => x.code === "dax-unquoted-calculations-table"));
  assert.ok(fs.readFileSync(f, "utf8").endsWith("'Calculations' )\r\n\t\tformatString: 0\r\n"));
});
check("an LF file gets LF only", () => {
  assert.strictEqual(r1.lineEnding, "LF");
  assert.ok(!fs.readFileSync(f1, "utf8").includes("\r"));
});

// --- names ----------------------------------------------------------------------------------

check("names that collide are refused: a column, a measure of the table, a measure elsewhere, a repeat", () => {
  const p = base({ extra: true });
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "Amount", expression: "1", formatString: "0" }] }), /'Amount' is a column of sales/);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "amount ac", expression: "1", formatString: "0" }] }), /already has a measure named 'Amount AC'\. Pass replace: true/);
  refuses(() => add({ projectPath: p, table: "KPI", measures: [{ name: "Amount PL", expression: "1", formatString: "0" }] }), /already exists on sales; measure names are unique across the model/);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "A", expression: "1", formatString: "0" }, { name: "a", expression: "2", formatString: "0" }] }), /two measures in this call are named 'a'/);
  refuses(() => add({ projectPath: p, table: "Nope", measures: [{ name: "A", expression: "1", formatString: "0" }] }), /table 'Nope' is not in the model/);
});
check("replace: true replaces only that measure's block and returns what it removed", () => {
  const p = base();
  const f = tablePath(p, "sales.tmdl");
  const before = fs.readFileSync(f, "utf8");
  const r = add({ projectPath: p, table: "sales", replace: true, measures: [{ name: "Amount PL", expression: "CALCULATE ( [Amount AC], sales[Region] = \"North\" )", formatString: "#,0.0" }] });
  assert.strictEqual(r.added, "");
  const [rep] = r.replaced;
  assert.strictEqual(rep.removed, "\tmeasure 'Amount PL' =\n\t\t\tCALCULATE ( SUM ( 'sales'[Amount] ), 'sales'[Scenario] = \"Budget\" )\n\t\tformatString: #,0");
  const after = fs.readFileSync(f, "utf8");
  assert.strictEqual(after, before.replace(rep.removed, rep.added));
  const t = tmdl.readModel(p).tables.find((x) => x.name === "sales");
  assert.strictEqual(t.measures.filter((x) => x.name === "Amount PL").length, 1);
  assert.strictEqual(t.measures.find((x) => x.name === "Amount PL").formatString, "#,0.0");
});

// --- refusals that keep the file loading --------------------------------------------------------

check("formatString is required, a multi-line string and a fence are refused", () => {
  refuses(() => add({ projectPath: p1, table: "sales", measures: [{ name: "Q", expression: "1" }] }), /formatString is required/);
  refuses(() => add({ projectPath: p1, table: "sales", measures: [{ name: "Q", expression: "\"a\nb\"", formatString: "0" }] }), /runs over a line break/);
  refuses(() => add({ projectPath: p1, table: "sales", measures: [{ name: "Q", expression: "```\n1\n```", formatString: "0" }] }), /fence/);
  refuses(() => add({ projectPath: p1, table: "sales", measures: [{ name: "Q", expression: "1", formatString: "0", colour: "red" }] }), /unknown field/);
});
check("a VAR named after a DAX function is refused by the validator's own rule, before writing", () => {
  const p = base();
  const f = tablePath(p, "sales.tmdl");
  const was = fs.readFileSync(f);
  refuses(() => add({ projectPath: p, table: "sales", measures: [{ name: "V", expression: "VAR Sum = [Amount AC]\nRETURN Sum * 2", formatString: "#,0" }] }),
    /dax-var-named-after-a-function/);
  assert.ok(fs.readFileSync(f).equals(was));
});

// --- negative controls ------------------------------------------------------------------------

check("negative control: the added body moved to property depth fires the swallowed-expression rule", () => {
  const p = base();
  const r = add({ projectPath: p, table: "sales", measures: [{ name: "Twice", expression: "[Amount AC] * 2", formatString: "#,0" }] });
  const f = tablePath(p, "sales.tmdl");
  fs.writeFileSync(f, fs.readFileSync(f, "utf8").replace(r.added, r.added.replace("\t\t\t[Amount AC]", "\t\t[Amount AC]")));
  const codes = srv.validateReport({ projectPath: p }).findings.map((x) => x.code);
  assert.ok(codes.includes("tmdl-measure-expression-swallowed"), codes.join(", "));
});
check("negative control: the added DAX started on the equals line fires its rule", () => {
  const p = base();
  const r = add({ projectPath: p, table: "sales", measures: [{ name: "Long", expression: "CALCULATE (\n    [Amount AC]\n)", formatString: "#,0" }] });
  const f = tablePath(p, "sales.tmdl");
  fs.writeFileSync(f, fs.readFileSync(f, "utf8").replace(r.added, r.added.replace("measure Long =\n\t\t\tCALCULATE (", "measure Long = CALCULATE (")));
  const codes = srv.validateReport({ projectPath: p }).findings.map((x) => x.code);
  assert.ok(codes.includes("multiline-measure-dax-on-equals-line"), codes.join(", "));
});
check("negative control: an unquoted Calculations reference fires its rule", () => {
  const p = base({ extra: true });
  const r = add({ projectPath: p, table: "Calculations", measures: [{ name: "Items", expression: "COUNTROWS ( Calculations )", formatString: "0" }] });
  const f = tablePath(p, "Extra.tmdl");
  fs.writeFileSync(f, fs.readFileSync(f, "utf8").replace(r.added, r.added.replace("'Calculations'", "Calculations")));
  const codes = srv.validateReport({ projectPath: p }).findings.map((x) => x.code);
  assert.ok(codes.includes("dax-unquoted-calculations-table"), codes.join(", "));
});

check("the server lists write_measures, labelled as editing a file", () => {
  const e = srv.CALLABLE.write_measures;
  assert.ok(e && typeof e.fn === "function");
  assert.deepStrictEqual(e.schema.required, ["projectPath", "table", "measures"]);
});

for (const [s, name, d] of results) console.log(`  ${s}  ${name}${d ? `  <- ${d}` : ""}`);
const failed = results.filter(([s]) => s === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} passed`);
fs.rmSync(TMP, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
