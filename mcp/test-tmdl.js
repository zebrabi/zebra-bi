#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * Tests for the TMDL reader and the three questions it answers.
 *
 * The parser tests use the exact shapes the skill documents, tabs included, because a parser
 * tested only against text the test author invented proves the test author is consistent.
 *
 *   node test-tmdl.js
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const t = require("./tmdl.js");
const srv = require("./server.js");

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (e) { results.push(["FAIL", name, String(e.message).split("\n")[0].slice(0, 92)]); }
};

// The calendar exactly as `model-layer.md` writes it, tabs and all.
const CALENDAR = [
  "table Calendar",
  "\tdataCategory: Time",
  "",
  "\tcolumn Date",
  "\t\tisKey",
  "\t\tformatString: General Date",
  "\t\tsummarizeBy: none",
  "",
  "\tcolumn Month",
  "\t\tsortByColumn: MonthNo",
  "",
].join("\n");

const FACT = [
  "/// The fact table",
  "table Financials",
  "",
  "\tmeasure 'Value AC' = CALCULATE(SUM(Financials[Amount]), Financials[Scenario] = \"AC\")",
  "\t\tformatString: #,0",
  "",
  "\tmeasure 'Value PL' = CALCULATE(SUM(Financials[Amount]), Financials[Scenario] = \"PL\")",
  "\t\tformatString: #,0",
  "",
  // The body sits one level DEEPER than the properties. Desktop's reader takes every line deeper
  // than the measure as DAX once a multi-line body opens, so a body at property depth would
  // swallow the formatString line -- measured 2026-09-05, the engine then reports state 5.
  "\tmeasure 'Margin %' =",
  "\t\t\tDIVIDE (",
  "\t\t\t\t[Value AC] - [Cost AC],",
  "\t\t\t\t[Value AC]",
  "\t\t\t)",
  "\t\tformatString: 0.0%",
  "",
  "\tcolumn Amount",
  "\t\tdataType: double",
  "",
].join("\n");

// --- the parser ------------------------------------------------------------------------------

check("parses a table and its dataCategory", () => {
  const m = t.parseTmdl(CALENDAR);
  assert.strictEqual(m.tables.length, 1);
  assert.strictEqual(m.tables[0].name, "Calendar");
  assert.strictEqual(m.tables[0].dataCategory, "Time");
});

check("parses columns, isKey and sortByColumn", () => {
  const [tb] = t.parseTmdl(CALENDAR).tables;
  const date = tb.columns.find((c) => c.name === "Date");
  const month = tb.columns.find((c) => c.name === "Month");
  assert.ok(date.isKey, "isKey (a bare property) was not read");
  assert.strictEqual(date.formatString, "General Date");
  assert.strictEqual(month.sortByColumn, "MonthNo");
  assert.ok(!month.isKey, "isKey leaked from the previous column");
});

check("unquotes a name containing spaces", () => {
  const [tb] = t.parseTmdl(FACT).tables;
  assert.ok(tb.measures.some((m) => m.name === "Value AC"), "quoted measure name not unquoted");
});

check("reads a formatString off a measure", () => {
  const [tb] = t.parseTmdl(FACT).tables;
  assert.strictEqual(tb.measures.find((m) => m.name === "Margin %").formatString, "0.0%");
});

// The reason depth matters rather than a regex: DAX contains colons and commas, and a
// multi-line expression's body must not be read as properties of the measure.
check("a multi-line DAX body is not parsed as properties", () => {
  const [tb] = t.parseTmdl(FACT).tables;
  const m = tb.measures.find((x) => x.name === "Margin %");
  assert.ok(/DIVIDE/.test(m.expression), "the expression body was dropped");
  assert.strictEqual(Object.keys(m).filter((k) => /^(VAR|RETURN)$/i.test(k)).length, 0);
});

check("/// descriptions are skipped, not treated as structure", () => {
  assert.strictEqual(t.parseTmdl(FACT).tables.length, 1);
});

check("a column and a measure in one table do not merge", () => {
  const [tb] = t.parseTmdl(FACT).tables;
  assert.strictEqual(tb.columns.length, 1);
  assert.ok(tb.measures.length >= 3);
});

// --- the three questions ---------------------------------------------------------------------

const model = (...parts) => ({ present: true, files: ["m.tmdl"],
  tables: parts.flatMap((p) => t.parseTmdl(p).tables.map((x) => ({ ...x, file: "m.tmdl" }))) });

check("scenario measures are found by name", () => {
  const s = t.scenarioMeasures(model(FACT));
  assert.ok(s.hasActual, "AC not found");
  assert.ok(s.hasComparative, "PL not found");
});

check("an actuals-only model reports no comparative", () => {
  const s = t.scenarioMeasures(model("table F\n\tmeasure 'Value AC' = 1\n"));
  assert.ok(s.hasActual && !s.hasComparative);
});

check("a marked date table is recognised", () => {
  const d = t.dateTable(model(CALENDAR));
  assert.ok(d.marked && d.table === "Calendar" && d.keyColumn === "Date");
});

// The near miss is a different conversation from "no calendar at all", so it is reported.
check("dataCategory without a key column is NOT marked, and names the candidate", () => {
  const d = t.dateTable(model("table Calendar\n\tdataCategory: Time\n\n\tcolumn Date\n\t\tformatString: General Date\n"));
  assert.strictEqual(d.marked, false);
  assert.strictEqual(d.candidate, "Calendar");
});

check("percent format is detected in both forms", () => {
  assert.ok(t.isPercentFormat("0.0%") && t.isPercentFormat("Percent") && !t.isPercentFormat("#,0"));
});

// --- end to end through validate_report -------------------------------------------------------

function project({ calendar = CALENDAR, fact = FACT, units = null, valuesRef = null,
                   extraTmdl = null, bom = false, compatibilityLevel = null,
                   cultures = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-t-"));
  const def = path.join(root, "Demo.Report", "definition");
  const vd = path.join(def, "pages", "P1", "visuals", "V1");
  fs.mkdirSync(vd, { recursive: true });
  const md = path.join(root, "Demo.SemanticModel", "definition", "tables");
  fs.mkdirSync(md, { recursive: true });
  fs.writeFileSync(path.join(md, "model.tmdl"), [calendar, fact].filter(Boolean).join("\n"));
  // A second file, so a fixture can carry a fault without disturbing the healthy model above.
  if (extraTmdl !== null) {
    const body = Buffer.from(extraTmdl, "utf8");
    fs.writeFileSync(path.join(md, "extra.tmdl"),
      bom ? Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), body]) : body);
  }
  if (cultures) {
    const cd = path.join(root, "Demo.SemanticModel", "definition", "cultures");
    fs.mkdirSync(cd, { recursive: true });
    fs.writeFileSync(path.join(cd, "en-US.tmdl"), "cultureInfo en-US" + String.fromCharCode(10));
  }
  if (compatibilityLevel !== null) {
    fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "database.tmdl"),
      `database Demo\n\tcompatibilityLevel: ${compatibilityLevel}\n`);
  }
  fs.writeFileSync(path.join(root, "Demo.pbip"), JSON.stringify({ $schema:
    "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json" }));
  fs.writeFileSync(path.join(def, "definition.pbir"), JSON.stringify(
    { datasetReference: { byPath: { path: "../Demo.SemanticModel" } } }));
  fs.writeFileSync(path.join(def, "report.json"), JSON.stringify(
    { $schema: "https://x", themeCollection: { baseTheme: { name: "CY24SU10" } },
      publicCustomVisuals: ["ZebraBITablesBAE31B370F254F808553548EFB35BFA5"] }));
  fs.writeFileSync(path.join(def, "pages", "pages.json"),
    JSON.stringify({ pageOrder: ["P1"], activePageName: "P1" }));
  fs.writeFileSync(path.join(def, "pages", "P1", "page.json"), JSON.stringify({ name: "P1" }));
  const objects = {};
  if (units) objects.dataLabelSettings = [{ properties: { units: { expr: { Literal: { Value: `'${units}'` } } } } }];
  const qs = valuesRef ? { Values: { projections: [{ field: {}, queryRef: valuesRef, nativeQueryRef: valuesRef }] } } : {};
  fs.writeFileSync(path.join(vd, "visual.json"), JSON.stringify({ name: "V1", position: {},
    visual: { visualType: "ZebraBITablesBAE31B370F254F808553548EFB35BFA5", objects, query: { queryState: qs } } }));
  return root;
}
const codes = (root) => new Set(srv.validateReport({ projectPath: root }).findings.map((f) => f.code));

check("a healthy model produces no model findings", () => {
  const c = codes(project());
  assert.ok(!c.has("model-has-no-scenario-measures"), "fired on a model that has AC and PL");
  assert.ok(!c.has("no-date-table-marked"), "fired on a marked calendar");
});

check("an actuals-only model is reported", () => {
  assert.ok(codes(project({ fact: "table F\n\tmeasure 'Value AC' = 1\n" }))
    .has("model-has-no-scenario-measures"));
});

check("an unmarked calendar is reported", () => {
  // The fact now computes PY with DATEADD. Without time intelligence there is nothing for an
  // unmarked date table to break, and since 2026-08-05 the rule says so -- so a fixture that omits
  // it would assert the rule fires while describing a model that has no fault.
  assert.ok(codes(project({ calendar: "table Calendar\n\n\tcolumn Date\n\t\tisKey\n",
    fact: "table F\n\tmeasure 'Value AC' = 1\n\tmeasure 'Value PY' = CALCULATE([Value AC], DATEADD('Calendar'[Date], -1, YEAR))\n" }))
    .has("no-date-table-marked"));
});

// From the corpus run: 8 of 20 shipped templates have no marked date table, and at least two compute no
// time intelligence at all -- `Annual Comparative income statement` derives PY from
// SELECTEDVALUE(PnL[Year]). Nothing for the marking to fix, so nothing to report.
check("an unmarked calendar with NO time intelligence is NOT reported", () => {
  assert.ok(!codes(project({ calendar: "table Calendar\n\n\tcolumn Date\n\t\tisKey\n",
    fact: "table F\n\tmeasure 'Value AC' = 1\n\tmeasure 'Value PY' = SELECTEDVALUE(F[PriorYear])\n" }))
    .has("no-date-table-marked"), "fired on a model that computes no time intelligence");
});

// `Brand portfolio analysis - FMCG` fired `model-has-no-scenario-measures` while carrying
// Revenue / Revenue PY, Units Sold / Units Sold PY. The actual is the BASE measure and is never
// named "AC", so requiring that name was a convention test dressed as a structural one.
// ☠️ THESE TWO FIXTURES EXIST BECAUSE THE CHECKER WAS BLIND.
// Its first version iterated `p.files` looking for `.tmdl`. `p.files` is the REPORT layer and
// holds no TMDL at all, so it matched nothing and scored a clean zero across all 20 shipped
// templates -- indistinguishable from a correct corpus. TMDL text lives in `model.sources`.
// Neither of these may be deleted: the first proves it fires, the second proves it discriminates.
check("a multi-line measure starting DAX on the = line is caught", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure 'Gross margin' = DIVIDE(\n\t\t\t[Revenue] - [COGS],\n\t\t\t[Revenue]\n\t\t)\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "did not fire on a split multi-line measure");
});

check("an ordinary single-line measure is NOT flagged as multi-line", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure Revenue = SUM(F[Amount])\n\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"),
    "fired on the normal form, which is most measures in every shipped template");
});

// The VAR form. `measure X = VAR a = 1` has balanced parens, so the paren test -- which was the
// whole rule until 2026-08-28 -- passed it. A VAR without a RETURN is not valid DAX, so every
// VAR measure written this way continues below and every one fails the cold open. The field
// reported it on 2026-08-22 while the rule that names the fault sat green: it had one fixture,
// the DIVIDE( shape, and one fixture is not the class.
check("a VAR measure starting DAX on the = line is caught", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure 'Margin pct' = VAR r = [Revenue]\n\t\tRETURN DIVIDE(r - [COGS], r)\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "did not fire on a VAR/RETURN measure");
});

// A complete one-line `VAR ... RETURN ...` measure opens and renders in Desktop (field report
// 2026-10-21, two opens), so it must stay silent. Both controls below: the same line with an open
// paren still continues below and still fires, and so does a VAR whose RETURN is only in a comment.
check("a one-line VAR ... RETURN measure is NOT flagged", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure X = VAR a = [Revenue] VAR b = [COGS] RETURN DIVIDE(a - b, a)\n"
    + "\t\tformatString: 0.0%\n\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "fired on a complete one-line VAR/RETURN");
});

check("a one-line VAR ... RETURN with an open paren is still caught", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure X = VAR a = 1 RETURN CALCULATE(\n\t\t\t[Revenue], Dates[Year] = 2024)\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "an open paren after RETURN was missed");
});

// The RETURN keyword itself, not a word that contains it or a name that spells it. Each of these
// has no real RETURN on the = line, so each continues below and must fire.
for (const [name, line, body] of [
  ["a trailing RETURN with its body on the next line", "VAR a = [Revenue] RETURN", "\t\ta\n"],
  ["RETURN inside a bracketed measure name", "VAR r = [Return on Equity]", "\t\tRETURN r\n"],
  ["RETURN inside a bracketed column name", "VAR r = SUM(Sales[Return])", "\t\tRETURN r\n"],
  ["RETURN inside a quoted table name", "VAR r = SUM('Return Items'[Qty])", "\t\tRETURN r\n"],
  ["RETURN inside a block comment", "VAR a = [Revenue] /* RETURN */", "\t\tRETURN a\n"],
]) {
  check(`a VAR with ${name} is still caught`, () => {
    assert.ok(codes(project({ fact: `table F\n\tmeasure X = ${line}\n${body}`
      + "\tcolumn Amount\n\t\tdataType: double\n" }))
      .has("multiline-measure-dax-on-equals-line"), `missed: ${line}`);
  });
}

check("a complete one-line VAR ... RETURN with bracketed names and a block comment is NOT flagged", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure X = VAR r = [Return on Equity] /* note */ RETURN r * SUM(Sales[Return])\n"
    + "\t\tformatString: 0.0%\n\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "fired on a complete one-line VAR/RETURN");
});

check("a VAR whose RETURN is only in a comment is still caught", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure X = VAR a = [Revenue] -- RETURN below\n\t\tRETURN a\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "a RETURN in a comment hid the continuation");
});

// The negative control the VAR clause needs, and the one that killed an earlier draft: ``` opens
// the LEGAL fenced form, whose body belongs on the lines below. A draft that judged continuation
// by "the next line does not look like a property" reported 11 of these across the vendor
// templates and the SaaS MRR reference model -- every one correct TMDL.
check("the fenced multi-line form is NOT flagged", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure 'Margin pct' = ```\n\t\t\tDIVIDE([Revenue] - [COGS], [Revenue])\n\t\t```\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"),
    "fired on the fenced form, which is how every vendor template writes a long measure");
});

// What Power BI Desktop actually writes under a measure. None of the four metadata lines is DAX,
// and until 2026-09-01 the two without a colon were appended to the expression -- every
// Desktop-authored measure shipped to the agent as `SUM(...) changedProperty = FormatString
// annotation PBI_FormatHint = {...}`. And `RETURN` on its own line, which is how Desktop formats
// every VAR measure, matched the bare-identifier branch and vanished.
const DESKTOP_TMDL = [
  "table Sales",
  "\tmeasure Revenue = SUM(Sales[Amount])",
  "\t\tformatString: #,0",
  "\t\tlineageTag: aaaa-bbbb",
  "\t\tchangedProperty = FormatString",
  "",
  "\t\tannotation PBI_FormatHint = {\"isGeneralNumber\":true}",
  "",
  "\tmeasure Margin =",
  "\t\t\tVAR r = [Revenue]",
  "\t\t\tRETURN",
  "\t\t\tDIVIDE(r - [COGS], r)",
  "\t\tformatString: 0.0%",
  "",
  "\tcolumn Amount",
  "\t\tdataType: double",
  "",
  "\tcolumn Total = [Amount] * 2",
  "\t\tdataType: double",
  "\t\tisDataTypeInferred",
  "",
].join("\n");

check("Desktop metadata lines under a measure are not part of its expression", () => {
  const [tb] = t.parseTmdl(DESKTOP_TMDL).tables;
  const rev = tb.measures.find((m) => m.name === "Revenue");
  assert.strictEqual(rev.expression, "SUM(Sales[Amount])", `expression was "${rev.expression}"`);
});

check("RETURN on its own line survives into a multi-line expression", () => {
  const [tb] = t.parseTmdl(DESKTOP_TMDL).tables;
  const m = tb.measures.find((x) => x.name === "Margin");
  assert.match(m.expression, /\bRETURN\b/, `RETURN was dropped: "${m.expression}"`);
  assert.match(m.expression, /VAR r = \[Revenue\]\s+RETURN\s+DIVIDE/, `order lost: "${m.expression}"`);
});

// `column Total = [Amount] * 2` is a CALCULATED column. Reading `(.+)` as the name made it a column
// called `Total = [Amount] * 2`, which no rule comparing names could ever match.
check("a calculated column keeps its name, and carries its expression separately", () => {
  const [tb] = t.parseTmdl(DESKTOP_TMDL).tables;
  const c = tb.columns.find((x) => x.isCalculated);
  assert.ok(c, "no column was recognised as calculated");
  assert.strictEqual(c.name, "Total", `name was "${c.name}"`);
  assert.strictEqual(c.expression, "[Amount] * 2");
  assert.strictEqual(tb.columns.find((x) => x.name === "Amount").isCalculated, false);
});

// The consequence that matters: the wont-open collision rule fired on `column Total` and stayed
// silent on the identical collision written as a calculated column.
check("measure/column name collision is caught on a CALCULATED column too", () => {
  const fact = "table F\n\tmeasure Total = SUM(F[Amount])\n\tcolumn Amount\n\t\tdataType: double\n"
    + "\tcolumn Total = [Amount] * 2\n\t\tdataType: double\n";
  assert.ok(codes(project({ fact })).has("tmdl-measure-column-name-collision"),
    "silent on a calculated column named like a measure");
});

check("and stays silent when the calculated column has a different name", () => {
  const fact = "table F\n\tmeasure Total = SUM(F[Amount])\n\tcolumn Amount\n\t\tdataType: double\n"
    + "\tcolumn Doubled = [Amount] * 2\n\t\tdataType: double\n";
  assert.ok(!codes(project({ fact })).has("tmdl-measure-column-name-collision"),
    "fired on a calculated column that collides with nothing");
});

// A `(` inside a DAX COMMENT must not either, and this one shipped: the counter walked the
// expression handling string literals and not comments, so `-- FY23 (unadjusted` read as an open
// paren and the rule fired at `wont-open` -- "the model loads with zero tables and Desktop opens
// Untitled" -- on a model that opens fine. One comment habit on a 400-measure model was 400
// findings. A rule that fires on a good report is the worse failure (AGENTS.md:21). Found by
// review 2026-08-31.
check("a paren inside a `--` DAX comment does NOT count as unbalanced", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure Revenue = SUM(F[Amount])  -- FY23 (unadjusted\n\t\tformatString: #,0\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "fired on a paren inside a -- comment");
});

check("a paren inside a `//` DAX comment does NOT count as unbalanced", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure Revenue = SUM(F[Amount])  // net (of returns\n\t\tformatString: #,0\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "fired on a paren inside a // comment");
});

// The positive arm the two controls above need: a genuinely unbalanced paren still fires even
// when a comment is present, so the fix narrowed the rule rather than switching it off.
check("a real continuation IS still caught when a comment is also on the line", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure 'Margin pct' = DIVIDE(  -- see FY23 notes\n\t\t\t[Revenue] - [COGS],\n\t\t\t[Revenue]\n\t\t)\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "a comment made the rule blind to a real fault");
});

// And the blank line: the rule bailed on `!next.trim()`, so the same cold-open fault written with
// a blank line after the `=` was missed. Same rule, same cost, opposite direction.
check("a blank line before the continuation is still caught", () => {
  assert.ok(codes(project({ fact:
    "table F\n\tmeasure 'Margin pct' = DIVIDE(\n\n\t\t\t[Revenue] - [COGS],\n\t\t\t[Revenue]\n\t\t)\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "a blank line hid the continuation");
});

// A `(` inside a string literal must not open a phantom continuation.
check("a paren inside a string literal does NOT count as unbalanced", () => {
  assert.ok(!codes(project({ fact:
    "table F\n\tmeasure Label = \"Revenue (net)\"\n\t\tformatString: 0\n"
    + "\tcolumn Amount\n\t\tdataType: double\n" }))
    .has("multiline-measure-dax-on-equals-line"), "fired on a paren inside a caption");
});


check("scenario pairs named <metric> and <metric> PY are recognised", () => {
  assert.ok(!codes(project({ fact: "table F\n\tmeasure Revenue = 1\n\tmeasure 'Revenue PY' = 2\n"
    + "\tmeasure 'Units Sold' = 3\n\tmeasure 'Units Sold PY' = 4\n" }))
    .has("model-has-no-scenario-measures"), "fired on a model whose actual is the base measure");
});

check("percent units on a percent measure is caught", () => {
  assert.ok(codes(project({ units: "P", valuesRef: "Margin %" }))
    .has("percent-measure-double-scaled"));
});

check("percent units on a plain measure is NOT caught", () => {
  assert.ok(!codes(project({ units: "P", valuesRef: "Value AC" }))
    .has("percent-measure-double-scaled"), "fired on a measure with no percent format");
});

// --- the five cold-open killers ------------------------------------------------------------
//
// Each has BOTH a positive and a negative control, because the corpus can only prove silence.
// The negative controls are the expensive half: the first version of the `//` check fired 207
// times across 7 of the 20 shipped templates, every hit a commented line of DAX.

const KILLERS = ["tmdl-bom", "tmdl-double-slash-comment", "tmdl-description-on-relationship",
                 "tmdl-measure-column-name-collision", "tmdl-reserved-table-name-measures"];

check("a healthy model fires none of the five killers", () => {
  const c = codes(project());
  const fired = KILLERS.filter((k) => c.has(k));
  assert.strictEqual(fired.length, 0, `fired on a healthy model: ${fired.join(", ")}`);
});

check("a BOM on a TMDL file is caught", () => {
  assert.ok(codes(project({ extraTmdl: "table Extra\n", bom: true })).has("tmdl-bom"));
});

check("no BOM, no finding", () => {
  assert.ok(!codes(project({ extraTmdl: "table Extra\n" })).has("tmdl-bom"));
});

check("a // section comment outside an expression is caught", () => {
  const c = codes(project({ extraTmdl: "table Extra\n\n\t// ---- revenue ----\n\tmeasure X = 1\n" }));
  assert.ok(c.has("tmdl-double-slash-comment"), "missed a // at table level");
});

check("a // DAX comment inside a FENCED body is NOT caught", () => {
  const c = codes(project({ extraTmdl: [
    "table Extra", "", "\tmeasure 'Margin %' = ```",
    "\t\t\t// guard the divisor", "\t\t\tDIVIDE([Profit], [Sales])", "\t\t\t```", "",
  ].join("\n") }));
  assert.ok(!c.has("tmdl-double-slash-comment"), "fired on legal DAX inside a fence");
});

check("a // DAX comment inside an INDENTED body is NOT caught", () => {
  const c = codes(project({ extraTmdl: [
    "table Extra", "", "\tmeasure 'Margin %' =", "\t\t\t// guard the divisor",
    "\t\t\tDIVIDE([Profit], [Sales])", "",
  ].join("\n") }));
  assert.ok(!c.has("tmdl-double-slash-comment"), "fired on legal DAX in an indented body");
});

check("a /// description above a relationship is caught", () => {
  const c = codes(project({ extraTmdl: [
    "/// links the fact to the calendar", "relationship abc-123",
    "\tfromColumn: Financials.Date", "\ttoColumn: Calendar.Date", "",
  ].join("\n") }));
  assert.ok(c.has("tmdl-description-on-relationship"), "missed a /// on a relationship");
});

check("a /// description above a measure is NOT caught", () => {
  const c = codes(project({ extraTmdl:
    "table Extra\n\n\t/// revenue, actuals only\n\tmeasure X = 1\n" }));
  assert.ok(!c.has("tmdl-description-on-relationship"), "fired on a legal description");
});

check("a measure and a column sharing a name in ONE table is caught", () => {
  const c = codes(project({ extraTmdl:
    "table Extra\n\n\tmeasure Sales = 1\n\n\tcolumn Sales\n\t\tdataType: double\n" }));
  assert.ok(c.has("tmdl-measure-column-name-collision"), "missed a same-table collision");
});

check("the same name in DIFFERENT tables is NOT caught", () => {
  const c = codes(project({ extraTmdl: [
    "table Raw", "", "\tcolumn Sales", "\t\tdataType: double", "",
    "table .Measures", "", "\tmeasure Sales = SUM(Raw[Sales])", "",
  ].join("\n") }));
  assert.ok(!c.has("tmdl-measure-column-name-collision"), "fired across two different tables");
});

check("a table named Measures is caught", () => {
  assert.ok(codes(project({ extraTmdl: "table Measures\n\n\tmeasure X = 1\n" }))
    .has("tmdl-reserved-table-name-measures"));
});

check("a table named .Measures is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: "table .Measures\n\n\tmeasure X = 1\n" }))
    .has("tmdl-reserved-table-name-measures"));
});

// compatibilityLevel is a FACT, not a fault. All 20 shipped templates sit below 1606 and open
// fine, so a finding here would warn on correct reports; the number still has to reach the caller
// because reload is refused without it.
check("compatibilityLevel 1600 is reported in `checked`, never as a finding", () => {
  const r = srv.validateReport({ projectPath: project({ compatibilityLevel: 1600 }) });
  assert.ok(r.checked.some((c) => /compatibilityLevel: 1600/.test(c)), "the level was not reported");
  assert.ok(r.checked.some((c) => /refused as a downgrade/.test(c)), "the consequence was not stated");
  assert.ok(!r.findings.some((f) => /compatibility/i.test(f.code)), "raised a finding on a fine model");
});

check("compatibilityLevel 1606 is reported without the reload warning", () => {
  const r = srv.validateReport({ projectPath: project({ compatibilityLevel: 1606 }) });
  assert.ok(r.checked.some((c) => /compatibilityLevel: 1606/.test(c)), "the level was not reported");
  assert.ok(!r.checked.some((c) => /refused as a downgrade/.test(c)), "warned about a 1606 model");
});

// --- the TMDL grammar rules, 2026-09-05 -------------------------------------------------------
//
// Every fixture below is the exact text that was reloaded into Power BI Desktop 2.157.879.0 and
// refused (or, for the two silent ones, accepted and then read back broken from the DMVs). Each
// rule has a firing fixture and a clean twin, because a rule that has only ever been seen to pass
// is indistinguishable from a rule that reads nothing.

const CLEAN_MODEL = [
  "table Sales", "",
  "\tmeasure AC = SUM(Sales[Amount])", "\t\tformatString: #,##0", "",
  "\tmeasure 'AC PY' =", "\t\t\tCALCULATE(", "\t\t\t\t[AC],", "\t\t\t\tSAMEPERIODLASTYEAR('Calendar'[Date])", "\t\t\t)",
  "\t\tformatString: #,##0", "",
  "\tcolumn Date", "\t\tdataType: dateTime", "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Date]", "",
  "\tcolumn Region", "\t\tdataType: string", "\t\tisHidden", "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Region]", "",
  "\tpartition Sales = calculated", "\t\tmode: import", "\t\tsource = DATATABLE(\"Date\", DATETIME, \"Region\", STRING, {{\"2024-01-01\", \"EMEA\"}})", "",
].join("\n");

const REL_OK = "relationship SalesToCalendar\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar.Date\n";

const fires = (code, name, extra) => check(name, () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: extra }));
  assert.ok(c.has(code), `did not fire: ${code}`);
  const clean = codes(project({ fact: CLEAN_MODEL, extraTmdl: REL_OK }));
  assert.ok(!clean.has(code), `FIRES ON A CLEAN MODEL: ${code}`);
});

fires("tmdl-unknown-property", "a misspelled property (isHiden) is caught",
  "table Extra\n\n\tcolumn K\n\t\tdataType: string\n\t\tisHiden\n\t\tsummarizeBy: none\n");
fires("tmdl-unknown-property", "discourageReportMeasures on the model is caught by name",
  "model Model\n\tculture: en-US\n\tdiscourageReportMeasures\n");
check("every property Desktop writes on a column is known", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: [
    "table Extra", "", "\tcolumn K", "\t\tdataType: string", "\t\tisHidden", "\t\tisKey", "\t\tisNullable: false",
    "\t\tisAvailableInMdx: false", "\t\tisDefaultLabel", "\t\tformatString: 0", "\t\tlineageTag: a-b", "\t\tdataCategory: Country",
    "\t\tdisplayFolder: Attributes", "\t\tsummarizeBy: none", "\t\tsourceColumn: K", "\t\tsortByColumn: K", "\t\tisNameInferred",
    "\t\tisDataTypeInferred: false", "", "\t\tchangedProperty = IsHidden", "", "\t\tannotation SummarizationSetBy = Automatic", "",
    "\t\textendedProperty ParameterMetadata =", "\t\t\t\t{", "\t\t\t\t  \"version\": 3", "\t\t\t\t}", "",
    "\t\trelatedColumnDetails", "\t\t\tgroupByColumn: K", "",
  ].join("\n") }));
  assert.ok(!c.has("tmdl-unknown-property"), "fired on properties Desktop itself writes");
});
fires("tmdl-enum-value-invalid", "summarizeBy: total is caught",
  "table Extra\n\n\tcolumn K\n\t\tdataType: string\n\t\tsummarizeBy: total\n");
fires("tmdl-enum-value-invalid", "dataType: integer is caught",
  "table Extra\n\n\tcolumn K\n\t\tdataType: integer\n\t\tsummarizeBy: none\n");
check("PascalCase enum values are NOT caught (Desktop reads them case-insensitively)", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Extra\n\n\tcolumn K\n\t\tDataType: Int64\n\t\tSummarizeBy: None\n" }));
  assert.ok(!c.has("tmdl-enum-value-invalid") && !c.has("tmdl-unknown-property"));
});
fires("tmdl-duplicate-property", "the same property twice on one column is caught",
  "table Extra\n\n\tcolumn K\n\t\tdataType: string\n\t\tdataType: string\n\t\tsummarizeBy: none\n");
check("associatedColumn twice in a calendarColumnGroup is NOT caught (repeatable)", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Extra\n\n\tcolumn A\n\t\tdataType: string\n\n\tcolumn B\n\t\tdataType: string\n\n\tcalendar G\n\t\tcalendarColumnGroup = month\n\t\t\tprimaryColumn: A\n\t\t\tassociatedColumn: B\n\t\t\tassociatedColumn: A\n" }));
  assert.ok(!c.has("tmdl-duplicate-property"));
});
fires("tmdl-duplicate-object", "the same measure in a second file is caught",
  "table Sales\n\n\tmeasure AC = 1\n\t\tformatString: #,##0\n");
check("a table split over two files with DIFFERENT measures is NOT caught", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Sales\n\n\tmeasure 'Split AC' = [AC]\n\t\tformatString: #,##0\n" }));
  assert.ok(!c.has("tmdl-duplicate-object"), "fired on a legal partial declaration");
});
fires("tmdl-relationship-endpoint-unresolved", "a relationship onto a column that does not exist is caught",
  "relationship X\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar.Datum\n");
fires("tmdl-relationship-endpoint-unresolved", "the DAX bracket form Calendar[Date] is caught",
  "relationship X\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar[Date]\n");
check("a relationship with quoted parts resolves", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "relationship X\n\tfromColumn: 'Sales'.'Date'\n\ttoColumn: Calendar.Date\n" }));
  assert.ok(!c.has("tmdl-relationship-endpoint-unresolved"));
});
fires("tmdl-description-gap", "a /// separated from its object by a blank line is caught",
  "table Extra\n\n\t/// described\n\n\tmeasure D = 1\n\t\tformatString: #,##0\n");
fires("tmdl-description-gap", "a fence that is never closed is caught",
  "table Extra\n\n\tmeasure F = ```\n\t\t\t[AC] * 2\n\t\tformatString: #,##0\n");
check("a multi-line /// description that touches its object is NOT caught", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Extra\n\n\t/// one\n\t/// two\n\tmeasure D = 1\n\t\tformatString: #,##0\n" }));
  assert.ok(!c.has("tmdl-description-gap"));
});
check("database.tmdl with only the property line is caught, with the database line it is not", () => {
  const root = project({ fact: CLEAN_MODEL, compatibilityLevel: 1606 });
  const db = path.join(root, "Demo.SemanticModel", "definition", "database.tmdl");
  fs.writeFileSync(db, "compatibilityLevel: 1606\n");
  assert.ok(codes(root).has("tmdl-database-line-missing"), "did not fire on a bare property");
  fs.writeFileSync(db, "database\n\tcompatibilityLevel: 1606\n");
  assert.ok(!codes(root).has("tmdl-database-line-missing"), "fired on Desktop's own bare `database` form");
});
fires("tmdl-duplicate-lineagetag", "two columns sharing a lineageTag are caught",
  "table Extra\n\n\tcolumn A\n\t\tdataType: string\n\t\tlineageTag: 11111111-1111-1111-1111-111111111111\n\n\tcolumn B\n\t\tdataType: string\n\t\tlineageTag: 11111111-1111-1111-1111-111111111111\n");
check("the same lineageTag on objects of different tables is NOT caught", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Extra\n\n\tcolumn A\n\t\tdataType: string\n\t\tlineageTag: 22222222-1111-1111-1111-111111111111\n\ntable Extra2\n\n\tcolumn A\n\t\tdataType: string\n\t\tlineageTag: 22222222-1111-1111-1111-111111111111\n" }));
  assert.ok(!c.has("tmdl-duplicate-lineagetag"));
});
// --- the three DAX rules ---------------------------------------------------------------------
// Each fires on the fault and stays silent on the CORRECT form of the same construct. For
// `dax-removefilters-as-table-expression` the clean twin matters more than the firing fixture:
// REMOVEFILTERS inside CALCULATE is the form the skill actively recommends, so a rule that flagged
// it would fire on every report we tell people to write.

fires("dax-removefilters-as-table-expression", "REMOVEFILTERS as RANKX's table argument is caught",
  "table Rank1\n\n\tmeasure R = RANKX(REMOVEFILTERS(Sales), [AC])\n\t\tformatString: #,##0\n");
fires("dax-removefilters-as-table-expression", "REMOVEFILTERS as TOPN's table argument is caught",
  "table Rank2\n\n\tmeasure R = COUNTROWS(TOPN(1, REMOVEFILTERS(Sales), [AC], DESC))\n\t\tformatString: #,##0\n");
fires("dax-removefilters-as-table-expression", "REMOVEFILTERS assigned to a VAR is caught",
  "table Rank3\n\n\tmeasure R =\n\t\t\tVAR t = REMOVEFILTERS(Sales)\n\t\t\tRETURN COUNTROWS(t)\n\t\tformatString: #,##0\n");

check("REMOVEFILTERS inside CALCULATE -- the recommended form -- does NOT fire", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Ok1\n\n\tmeasure R = CALCULATE([AC], REMOVEFILTERS(Sales))\n\t\tformatString: #,##0\n" +
    "\n\tmeasure R2 = CALCULATETABLE(VALUES(Sales[Region]), REMOVEFILTERS(Sales))\n" }));
  assert.ok(!c.has("dax-removefilters-as-table-expression"),
    "FIRES ON THE RECOMMENDED FORM: REMOVEFILTERS as a CALCULATE filter");
});

check("REMOVEFILTERS in a comment or a caption does NOT fire", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Ok2\n\n\tmeasure R = [AC] // was RANKX(REMOVEFILTERS(Sales), [AC])\n" +
    "\n\tmeasure R2 = [AC] -- RANKX(REMOVEFILTERS(Sales), [AC])\n" +
    "\n\tmeasure R3 = \"RANKX(REMOVEFILTERS(Sales), [AC])\"\n" +
    "\n\t/// see RANKX(REMOVEFILTERS(Sales), [AC])\n\tmeasure R4 = [AC]\n" }));
  assert.ok(!c.has("dax-removefilters-as-table-expression"),
    "fired on commented / quoted DAX -- the 207-false-positive failure mode");
});

fires("dax-var-named-after-a-function", "a VAR named LastDate is caught",
  "table V1\n\n\tmeasure M =\n\t\t\tVAR LastDate = MAX(Sales[Date])\n\t\t\tRETURN COUNTROWS(Sales)\n\t\tformatString: #,##0\n");

// ☠️ REGRESSION. The first version of this rule listed "DAX function names" and fired on
// `VAR mid = ISINSCOPE(...)` in the SHIPPED `Financial statements` template. MID is a function and
// a legal variable name; 129 of 234 names tested are. This fixture is the shipped line.
check("a VAR named after a scalar function (mid/round/format) does NOT fire", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table V3\n\n\tmeasure M =\n" +
    "\t\t\tVAR low = 1\n\t\t\tVAR mid = 2\n\t\t\tVAR high = 3\n" +
    "\t\t\tVAR Round = 4\n\t\t\tVAR Format = 5\n\t\t\tVAR Search = 6\n\t\t\tVAR Left = 7\n" +
    "\t\t\tRETURN low + mid + high\n" }));
  assert.ok(!c.has("dax-var-named-after-a-function"),
    "FIRES ON A SHIPPED TEMPLATE: these names are legal VAR names");
});

check("a VAR whose name merely CONTAINS a function name does not fire", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table V2\n\n\tmeasure M =\n\t\t\tVAR LastDateValue = MAX(Sales[Date])\n" +
    "\t\t\tVAR SumOfAmount = SUM(Sales[Amount])\n\t\t\tRETURN SumOfAmount\n" }));
  assert.ok(!c.has("dax-var-named-after-a-function"), "the collision is exact-match, not substring");
});

fires("dax-unquoted-calculations-table", "an unquoted Calculations reference is caught",
  "table Calculations\n\n\tmeasure Z = 1\n\t\tformatString: #,##0\n" +
  "\ntable UsesIt\n\n\tmeasure U = COUNTROWS(Calculations)\n\t\tformatString: #,##0\n");

check("a QUOTED Calculations reference does not fire", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Calculations\n\n\tmeasure Z = 1\n" +
    "\ntable UsesIt\n\n\tmeasure U = COUNTROWS('Calculations')\n" }));
  assert.ok(!c.has("dax-unquoted-calculations-table"), "single quotes are the fix; they must pass");
});

check("Calculations is not flagged when the model declares no such table", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl:
    "table Other\n\n\tmeasure U = COUNTROWS(Calculations)\n" }));
  assert.ok(!c.has("dax-unquoted-calculations-table"),
    "without the table the fault is a different one with a different message");
});

check("a DAX function at compatibilityLevel 1606 is caught, at 1702 it is not", () => {
  const fn = "/// tax\nfunction AddTax = (amount : NUMERIC) => amount * 1.1\n";
  assert.ok(codes(project({ fact: CLEAN_MODEL, extraTmdl: fn, compatibilityLevel: 1606 })).has("tmdl-feature-below-compat"));
  assert.ok(!codes(project({ fact: CLEAN_MODEL, extraTmdl: fn, compatibilityLevel: 1702 })).has("tmdl-feature-below-compat"));
});
check("a calendar object is always reported (preview feature)", () => {
  assert.ok(codes(project({ fact: CLEAN_MODEL, compatibilityLevel: 1702, extraTmdl:
    "table Extra\n\n\tcolumn Y\n\t\tdataType: int64\n\n\tcalendar G\n\t\tcalendarColumnGroup = year\n\t\t\tprimaryColumn: Y\n" })).has("tmdl-feature-below-compat"));
});
check("a calculation group without discourageImplicitMeasures is caught; with it, not", () => {
  const cg = "table TI\n\n\tcalculationGroup\n\t\tcalculationItem Current = SELECTEDMEASURE()\n\n\tcolumn Period\n\t\tdataType: string\n\t\tsourceColumn: Name\n\n\tpartition TI = calculationGroup\n";
  assert.ok(codes(project({ fact: CLEAN_MODEL, extraTmdl: cg })).has("tmdl-calc-group-needs-discourage-implicit"));
  assert.ok(!codes(project({ fact: CLEAN_MODEL, extraTmdl: "model Model\n\tculture: en-US\n\tdiscourageImplicitMeasures\n\n" + cg })).has("tmdl-calc-group-needs-discourage-implicit"));
});
fires("tmdl-format-string-and-definition", "formatString plus formatStringDefinition on one measure is caught",
  "table Extra\n\n\tmeasure M = 1\n\t\tformatString: #,##0\n\n\t\tformatStringDefinition = \"#,0\"\n");
check("formatStringDefinition alone is NOT caught", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Extra\n\n\tmeasure M = 1\n\n\t\tformatStringDefinition = \"#,0\"\n" }));
  assert.ok(!c.has("tmdl-format-string-and-definition"));
});
fires("tmdl-security-filter-needs-both-directions", "securityFilteringBehavior both on a one-way relationship is caught",
  "relationship X\n\tsecurityFilteringBehavior: bothDirections\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar.Date\n");
check("securityFilteringBehavior both WITH crossFilteringBehavior both is NOT caught", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "relationship X\n\tcrossFilteringBehavior: bothDirections\n\tsecurityFilteringBehavior: bothDirections\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar.Date\n" }));
  assert.ok(!c.has("tmdl-security-filter-needs-both-directions"));
});
fires("tmdl-unquoted-dot-name", "an unquoted measure name with a dot is caught",
  "table Extra\n\n\tmeasure AC.v2 = 1\n\t\tformatString: #,##0\n");
check("a quoted name with a dot is NOT caught, nor is a GUID relationship name", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Extra\n\n\tmeasure 'AC.v2' = 1\n\t\tformatString: #,##0\n\nrelationship 8c8b00e4-961a-489c-9a8b-812542526d79\n\tfromColumn: Sales.Date\n\ttoColumn: Calendar.Date\n" }));
  assert.ok(!c.has("tmdl-unquoted-dot-name"));
});
fires("tmdl-measure-expression-swallowed", "`measure X =` with a property on the next line is caught",
  "table Extra\n\n\tmeasure Empty =\n\t\tformatString: #,##0\n");
fires("tmdl-measure-expression-swallowed", "a body at property depth that swallows formatString is caught",
  "table Extra\n\n\tmeasure Shallow =\n\t\t[AC] * 2\n\t\tformatString: #,##0\n");
check("Desktop's own empty `measure X` (no =) is NOT caught, and a proper body is not either", () => {
  const c = codes(project({ fact: CLEAN_MODEL, extraTmdl: "table Extra\n\n\tmeasure Measure\n\t\tlineageTag: a-b\n\n\tmeasure Deep =\n\t\t\t[AC] * 2\n\t\tformatString: #,##0\n" }));
  assert.ok(!c.has("tmdl-measure-expression-swallowed"));
});

// The tree reader itself, on the shapes the regex reader got wrong.
check("a kpi block under a measure is not glued onto its DAX", () => {
  const [tb] = t.parseTmdl("table S\n\n\tmeasure M = [AC]\n\t\tformatString: 0.0%\n\n\t\tkpi\n\t\t\ttargetExpression = 0\n\t\t\tstatusGraphic: Traffic Light\n").tables;
  assert.strictEqual(tb.measures[0].expression, "[AC]");
  assert.strictEqual(tb.measures[0].formatString, "0.0%");
});
check("a two-space-indented file keeps its structure", () => {
  const [tb] = t.parseTmdl("table S\n\n  measure M = [AC]\n    formatString: 0.0%\n\n  column K\n    dataType: string\n    isKey\n").tables;
  assert.strictEqual(tb.measures.length, 1); assert.strictEqual(tb.columns.length, 1);
  assert.ok(tb.columns[0].isKey); assert.strictEqual(tb.measures[0].formatString, "0.0%");
});
check("a fence closed at column 0 ends the measure where Desktop does", () => {
  const [tb] = t.parseTmdl("table S\n\n\tmeasure M = ```\n\t\t\tVAR x = 1\n\t\t\tRETURN x\n```\n\t\tformatString: 0\n\n\tcolumn K\n\t\tdataType: string\n").tables;
  assert.match(tb.measures[0].expression, /RETURN x/);
  assert.strictEqual(tb.measures[0].formatString, "0");
  assert.strictEqual(tb.columns.length, 1);
});

// The name carried "23" until 2026-08-05, when the catalogue went to 22 and the name became a
// lie the body did not tell. Both directions now, and neither states a count.
check("every rule has a checker", () => {
  const rules = srv.loadRules();
  const missing = rules.filter((r) => !srv.checkers[r.check]).map((r) => r.check);
  assert.strictEqual(missing.length, 0, `still open: ${missing.join(", ")}`);
});

// The reverse. A checker with no rule cannot fire and cannot be reported, so it reads as coverage
// while contributing none -- and it is the residue left behind when a rule is retired.
check("every checker has a rule", () => {
  const claimed = new Set(srv.loadRules().map((r) => r.check));
  const orphans = Object.keys(srv.checkers).filter((c) => !claimed.has(c));
  assert.strictEqual(orphans.length, 0, `checker with no rule: ${orphans.join(", ")}`);
});

// --- a relationship end on a calculated table -----------------------------------------------
//
// Scoped to RELATED columns, so it needs three fixtures rather than two: fire on the broken form,
// stay silent on the correct form, and stay silent on the broken form when nothing relates to it.
// That third one IS the scoping, and without a test it would quietly rot into noise.
const NL = String.fromCharCode(10);
const CALC_BAD = [
  "table Facts", "	column K", "		dataType: string", "		summarizeBy: none",
  "		sourceColumn: K", "",
  "	partition Facts = calculated", "		mode: import",
  "		source = DATATABLE(\"K\", STRING, {{\"a\"}})", "",
].join(NL);
const CALC_GOOD = CALC_BAD.replace("		sourceColumn: K",
  "		isNameInferred" + NL + "		sourceColumn: [K]");
const REL = ["", "relationship FactsToDim", "	fromColumn: Facts.K", "	toColumn: Dim.K", ""].join(NL);

check("a relationship onto a calculated column bound by SOURCE name is caught", () => {
  assert.ok(codes(project({ extraTmdl: CALC_BAD + REL }))
    .has("calculated-column-relationship-binding"),
    "did not fire on the shape that failed cold open with 'invalid column ID 17'");
});

check("the same relationship onto an isNameInferred column is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: CALC_GOOD + REL }))
    .has("calculated-column-relationship-binding"),
    "fired on the correct form, which is what every shipped model uses");
});

check("an unbracketed sourceColumn with NO relationship is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: CALC_BAD }))
    .has("calculated-column-relationship-binding"),
    "fired without a relationship -- harmless there, and flagging it would be pure noise");
});


// --- the three TMDL rules added 2026-08-28 ------------------------------------------------
//
// Each gets a positive fixture AND the negative control that discriminates it. All three screened
// against 1,325 real .tmdl files with zero hits before being written, so the negative controls
// below are the part that is actually load-bearing: a rule that never fires also scores zero.

check("description: as a property is caught", () => {
  assert.ok(codes(project({ extraTmdl: "table T\n\tmeasure R = 1\n\t\tdescription: revenue\n" }))
    .has("tmdl-description-property"), "did not fire on description: under a measure");
});

check("a /// description is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: "table T\n\t/// revenue\n\tmeasure R = 1\n" }))
    .has("tmdl-description-property"), "fired on ///, which is the correct form");
});

check("an unquoted name containing a space is caught", () => {
  assert.ok(codes(project({ extraTmdl: "table Financial Data\n\tcolumn A\n" }))
    .has("tmdl-unquoted-name-with-space"), "did not fire on `table Financial Data`");
});

check("a QUOTED name containing a space is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: "table 'Financial Data'\n\tcolumn A\n" }))
    .has("tmdl-unquoted-name-with-space"), "fired on the correct quoted form");
});

// The discriminator that matters most: spaces after `=` are ordinary DAX and are none of this
// rule's business. Without this the rule would fire on essentially every measure ever written.
check("spaces inside a measure EXPRESSION are NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: "table T\n\tmeasure Margin = [A] - [B]\n" }))
    .has("tmdl-unquoted-name-with-space"), "fired on spaces in the expression");
});

check("a table named Goal is caught", () => {
  assert.ok(codes(project({ extraTmdl: "table Goal\n\tcolumn A\n" }))
    .has("tmdl-reserved-table-name-goal"), "did not fire on `table Goal`");
});

check("a table named Goals is NOT caught", () => {
  assert.ok(!codes(project({ extraTmdl: "table Goals\n\tcolumn A\n" }))
    .has("tmdl-reserved-table-name-goal"), "fired on Goals, which is a legal name");
});

// --- partition `source =` must start below the equals line (17 Nov 2026) -----------------------------
//
// Desktop: "TMDL Format Error: Parsing error type - InvalidLineType ... Line - '{"Revenue",1},'".
// The table below is the shape an agent-built project had: the DATATABLE
// opens on the `source =` line and its rows follow underneath. The controls are the corrected form
// Desktop accepted, a one-line expression, an M partition and a fenced body, because a rule that also
// fired on those would flag most partitions in every shipped template.
const PART_HEAD = "table KPI\n\tcolumn KPI\n\t\tdataType: string\n\t\tsourceColumn: [KPI]\n\n\tpartition KPI = calculated\n\t\tmode: import\n";
const partitionCodes = (source) => codes(project({ extraTmdl: PART_HEAD + source }));

check("a DATATABLE that opens on the source = line and continues below is caught", () => {
  const bad = "\t\tsource = DATATABLE ( \"KPI\", STRING, \"Sort\", INTEGER, {\n\t\t\t{\"Revenue\",1},\n\t\t\t{\"Gross profit\",2}\n\t\t} )\n";
  assert.ok(partitionCodes(bad).has("tmdl-partition-source-on-equals-line"), "did not fire on the shape Desktop refused");
  const unindented = "\t\tsource = DATATABLE ( \"KPI\", STRING, {\n{\"Revenue\",1}\n} )\n";
  assert.ok(partitionCodes(unindented).has("tmdl-partition-source-on-equals-line"), "missed rows with no indentation at all");
});

check("a bare source = with no indented body is caught", () => {
  assert.ok(partitionCodes("\t\tsource =\n\t\tDATATABLE (\"KPI\", STRING, {{\"Revenue\"}})\n")
    .has("tmdl-partition-source-on-equals-line"), "missed a body at the same indent as source");
});

check("the corrected multi-line form, a one-line expression, an M partition and a fenced body are NOT flagged", () => {
  const forms = [
    "\t\tsource =\n\t\t\tDATATABLE (\n\t\t\t\t\"KPI\", STRING,\n\t\t\t\t{\n\t\t\t\t\t{\"Revenue\"}\n\t\t\t\t}\n\t\t\t)\n",
    "\t\tsource = DATATABLE ( \"KPI\", STRING, { {\"Revenue\"}, {\"Gross profit\"} } )\n",
    "\t\tsource =\n\t\t\t\tlet\n\t\t\t\t    Source = Csv.Document(File.Contents(\"C:\\d.csv\"), [Delimiter=\",\"])\n\t\t\t\tin\n\t\t\t\t    Source\n",
    "\t\tsource = ```\n\t\t\tDATATABLE ( \"KPI\", STRING, { {\"Revenue\"} } )\n\t\t```\n",
    "\t\tsource = SELECTCOLUMNS ( \"a (b\", 1 ) -- note (\n\t\tannotation PBI_Id = x\n",
  ];
  forms.forEach((f, i) => assert.ok(!partitionCodes(f).has("tmdl-partition-source-on-equals-line"),
    `fired on normal form #${i + 1}`));
});

// A lowercase `source = ...` M step inside a `let` body is expression text, not the partition's
// `source` property, whether the body belongs to a partition or to an `expression`.
check("a multi-line `source = ...` step inside an M body (partition or expression) is NOT flagged", () => {
  const inPartition = "table T\n\tpartition T = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
    + "\t\t\t\t\tsource = Table.TransformColumnTypes(raw,{\n\t\t\t\t\t\t{\"A\", type text}})\n\t\t\t\tin\n\t\t\t\t\tsource\n";
  assert.ok(!codes(project({ extraTmdl: inPartition })).has("tmdl-partition-source-on-equals-line"),
    "fired on an M step inside a partition's let body");
  const inExpression = "expression src =\n\t\tlet\n\t\t\tsource = Json.Document(Web.Contents(\"https://x\",[\n"
    + "\t\t\t\tHeaders=[]]))\n\t\tin\n\t\t\tsource\n";
  assert.ok(!codes(project({ extraTmdl: inExpression })).has("tmdl-partition-source-on-equals-line"),
    "fired on an M step inside an expression body");
});

// --- model.tmdl ref lines start at column 0 (17 Nov 2026) --------------------------------------
// Desktop: "Unexpected line type: ReferenceObject! Document - './model'. Line - '\tref table Financials'".
// Controls are the shape a project that Desktop opened writes: properties indented, refs at column 0.
const modelTmdl = (refIndent) => [
  "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3", "",
  `${refIndent}ref table Financials`, `${refIndent}ref table Calendar`, `${refIndent}ref table 'Time View'`, "",
].join("\n");
const withModel = (refIndent) => {
  const root = project();
  fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "model.tmdl"), modelTmdl(refIndent));
  return root;
};

check("an indented ref table line in model.tmdl is caught, with its line", () => {
  for (const ind of ["\t", "    "]) {
    const r = srv.validateReport({ projectPath: withModel(ind) });
    const f = r.findings.find((x) => x.code === "tmdl-ref-line-indented");
    assert.ok(f, `did not fire on a ref indented with ${JSON.stringify(ind)}`);
    assert.strictEqual(f.severity, "wont-open");
    assert.ok(/model\.tmdl:5$/.test(f.location), f.location);
  }
});

check("ref lines at column 0 (the form Desktop writes) are NOT flagged; an indented ref outside model.tmdl is not judged", () => {
  assert.ok(!codes(withModel("")).has("tmdl-ref-line-indented"), "fired on the correct form");
  assert.ok(!codes(project({ extraTmdl: "table T\n\tref table X\n" })).has("tmdl-ref-line-indented"),
    "judged a file that is not model.tmdl");
});

// A table named Model lives in definition/tables/Model.tmdl. Its M body may carry a step called `ref`,
// indented like every step. That file is a table, not the model, so the ref-line rule does not read it.
check("a step named ref inside tables/Model.tmdl (a table named Model) is NOT flagged", () => {
  const root = withModel("");
  fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "tables", "Model.tmdl"), [
    "table Model", "\tcolumn A", "\t\tdataType: string", "\t\tsourceColumn: A", "",
    "\tpartition Model = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
    "\t\t\t\t    ref = Csv.Document(File.Contents(\"C:\\d.csv\"), [Delimiter=\",\"]),",
    "\t\t\t\t    Source = ref", "\t\t\t\tin", "\t\t\t\t    Source", "",
  ].join("\n"));
  assert.ok(!codes(root).has("tmdl-ref-line-indented"), "judged a table file named Model.tmdl as the model");
});

// --- DATATABLE rows are constants ----------------------------------------------------------------
// DATE(2025,1,1) in a row makes the calculated table fail on load; an ISO string constant in a DATETIME
// column loads. Must stay silent on: strings, a DATE applied OUTSIDE the DATATABLE by ADDCOLUMNS,
// negative numbers, BLANK(), and a function-looking word in a string.
const dtCodes = (source) => codes(project({ extraTmdl: "table F\n\tpartition F = calculated\n\t\tmode: import\n" + source }));
const DT_HEAD = "\t\tsource =\n\t\t\tDATATABLE ( \"Date\", DATETIME, \"Account\", STRING, \"Amount\", INTEGER, {\n";

check("a DATE() call in a DATATABLE row is caught once, as a blank-severity finding at the row", () => {
  const root = project({ extraTmdl: "table F\n\tpartition F = calculated\n\t\tmode: import\n" + DT_HEAD
    + "\t\t\t\t{ DATE(2025,1,1), \"Revenue\", 473363 },\n\t\t\t\t{ DATE(2025,2,1), \"Revenue\", 492779 }\n\t\t\t} )\n" });
  const hits = srv.validateReport({ projectPath: root }).findings.filter((f) => f.code === "tmdl-datatable-row-calls-a-function");
  assert.strictEqual(hits.length, 1, "expected one finding per DATATABLE, got " + hits.length);
  assert.strictEqual(hits[0].severity, "blank");
  assert.ok(/extra\.tmdl:\d+$/.test(hits[0].location), hits[0].location);
  assert.ok(/DATE\(/.test(hits[0].detail));
});

check("constants, strings, negatives, BLANK(), a DATE outside the DATATABLE and a quoted DATE( are NOT flagged", () => {
  const rowsOk = [
    "\t\t\t\t{ \"2025-01-01\", \"Revenue\", 473363 },\n\t\t\t\t{ \"2025-02-01\", \"Cost\", -257436 }\n\t\t\t} )\n",
    "\t\t\t\t{ \"2025-01-01\", \"Revenue\", BLANK() }\n\t\t\t} )\n",
    "\t\t\t\t{ \"2025-01-01\", \"DATE(2025,1,1) in text\", 1 }\n\t\t\t} )\n",
  ];
  rowsOk.forEach((r, n) => assert.ok(!dtCodes(DT_HEAD + r).has("tmdl-datatable-row-calls-a-function"), "fired on control #" + (n + 1)));
  const outside = "\t\tsource =\n\t\t\tADDCOLUMNS (\n\t\t\t\tDATATABLE ( \"Year\", INTEGER, \"MonthNo\", INTEGER, {\n\t\t\t\t\t{2025,1},\n\t\t\t\t\t{2025,2}\n\t\t\t\t} ),\n\t\t\t\t\"Date\", DATE ( [Year], [MonthNo], 1 )\n\t\t\t)\n";
  assert.ok(!dtCodes(outside).has("tmdl-datatable-row-calls-a-function"), "fired on a DATE applied outside the DATATABLE");
});

// Only DATE is flagged; other calls in a row are not judged. Comments are not code.
check("TRUE()/FALSE() rows, a commented-out DATATABLE and a `//` note after a row are NOT flagged", () => {
  const cases = [
    "\t\tsource =\n\t\t\tDATATABLE(\"B\", BOOLEAN, {\n\t\t\t\t{TRUE()},\n\t\t\t\t{FALSE()}\n\t\t\t})\n",
    "\t\tsource =\n\t\t\t// DATATABLE(\"x\", STRING, {{FORMAT(1)}})\n\t\t\tROW(\"a\",1)\n",
    "\t\tsource =\n\t\t\tDATATABLE(\"N\", STRING, {\n\t\t\t\t{\"a\"} // see note(1)\n\t\t\t})\n",
  ];
  cases.forEach((c, n) => assert.ok(!dtCodes(c).has("tmdl-datatable-row-calls-a-function"), "fired on case #" + (n + 1)));
});

// --- `KPI` is reserved in DAX, like Calculations (22 Nov 2026) ---------------------------------------
// A table named KPI loads; every unquoted KPI[...] fails with "The syntax for 'KPI' is incorrect".
const KPI_TABLE = "table KPI\n\n\tcolumn Name\n\t\tdataType: string\n\t\tsummarizeBy: none\n\t\tsourceColumn: Name\n";
const kpiCodes = (extra) => codes(project({ fact: CLEAN_MODEL, extraTmdl: extra }));

check("an unquoted KPI[Name] reference is caught when the model declares a KPI table; 'KPI'[Name] is not", () => {
  const uses = (ref) => KPI_TABLE + `\ntable UsesIt\n\n\tmeasure U = SELECTEDVALUE ( ${ref} )\n\t\tformatString: #,##0\n`;
  assert.ok(kpiCodes(uses("KPI[Name]")).has("dax-unquoted-kpi-table"), "missed KPI[Name]");
  assert.ok(kpiCodes(uses("kpi[Name]")).has("dax-unquoted-kpi-table"), "missed a different case");
  assert.ok(!kpiCodes(uses("'KPI'[Name]")).has("dax-unquoted-kpi-table"), "fired on the quoted form");
  assert.ok(!kpiCodes(uses("'KPI'[Name]")).has("dax-unquoted-calculations-table"), "the sibling rule fired");
});

check("KPI is NOT flagged without a KPI table, as a column or measure name, in a string or comment, or in M", () => {
  const measure = (dax) => `table UsesIt\n\n\tmeasure U = ${dax}\n\t\tformatString: #,##0\n`;
  // no such table: a different fault with a different message
  assert.ok(!kpiCodes(measure("SELECTEDVALUE ( KPI[Name] )")).has("dax-unquoted-kpi-table"), "fired with no KPI table");
  // the table exists; none of these is an unquoted table reference
  for (const dax of ["SUM ( Sales[KPI] )", "[KPI] * 2", "\"KPI[Name] is text\"", "SUM ( 'KPI'[Value] ) // KPI[Name]",
                     "VAR kpi = 1 RETURN kpi"]) {
    assert.ok(!kpiCodes(KPI_TABLE + "\n" + measure(dax)).has("dax-unquoted-kpi-table"), `fired on ${dax}`);
  }
  // M: KPI[Name] is a field access on a record or step called KPI
  const m = "table Loaded\n\n\tcolumn A\n\t\tdataType: string\n\t\tsourceColumn: A\n\n\tpartition Loaded = m\n\t\tmode: import\n\t\tsource =\n"
    + "\t\t\t\tlet\n\t\t\t\t    KPI = [Name = \"a\"],\n\t\t\t\t    Out = #table({\"A\"}, {{KPI[Name]}})\n\t\t\t\tin\n\t\t\t\t    Out\n";
  assert.ok(!kpiCodes(KPI_TABLE + "\n" + m).has("dax-unquoted-kpi-table"), "read M as DAX");
});

// --- a calculated table's expression is indented with tabs throughout (22 Nov 2026) --------------------
// Desktop: "Invalid indentation was detected!" when DATATABLE rows are space-indented under a tab-indented source.
const CALC_HEAD = "table F\n\tpartition F = calculated\n\t\tmode: import\n\t\tsource =\n";
const ROWS = (lead) => ["{ 2025, 1, \"Revenue\", 473363 },", "{ 2025, 2, \"Revenue\", 492779 }"].map((r) => lead + r).join("\n");
const calcBody = (rowLead) => "\t\t\tADDCOLUMNS (\n\t\t\tDATATABLE (\n\t\t\t\t\"Year\", INTEGER, \"MonthNo\", INTEGER, \"Account\", STRING, \"Amount\", INTEGER,\n\t\t\t\t{\n"
  + ROWS(rowLead) + "\n\t\t\t\t}\n\t\t\t),\n\t\t\t\"Key\", [Year] * 100 + [MonthNo]\n\t\t\t)\n";
const SP = "calculated-source-lines-must-be-tab-indented";
const spaceCodes = (src) => codes(project({ extraTmdl: src }));

check("DATATABLE rows indented with spaces under a tab-indented source are caught, once, at the first such row", () => {
  const root = project({ extraTmdl: CALC_HEAD + calcBody("        ") });
  const hits = srv.validateReport({ projectPath: root }).findings.filter((f) => f.code === "tmdl-calculated-source-space-indented");
  assert.strictEqual(hits.length, 1, `expected one finding, got ${hits.length}`);
  assert.strictEqual(hits[0].severity, "wont-open");
  assert.ok(/extra\.tmdl:9$/.test(hits[0].location), hits[0].location);
});

check("tab-indented rows, a file indented with spaces throughout, tab-plus-alignment-spaces and an M partition are NOT flagged", () => {
  const code = "tmdl-calculated-source-space-indented";
  assert.ok(!spaceCodes(CALC_HEAD + calcBody("\t\t\t\t\t")).has(code), "fired on tab-indented rows");
  const allSpaces = CALC_HEAD.replace(/\t/g, "    ") + calcBody("        ").replace(/\t/g, "    ");
  assert.ok(!spaceCodes(allSpaces).has(code), "fired on a file that is indented with spaces throughout");
  assert.ok(!spaceCodes(CALC_HEAD + calcBody("\t\t\t\t\t  ")).has(code), "fired on tabs followed by alignment spaces");
  const m = "table L\n\tpartition L = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n        Source = 1\n\t\t\t\tin\n\t\t\t\t    Source\n";
  assert.ok(!spaceCodes(m).has(code), "judged an M partition");
  // a property after the expression, at table depth, ends the body: not a space-indented row
  assert.ok(!spaceCodes(CALC_HEAD + "\t\t\t{ 1 }\n\t\tannotation X = y\n").has(code), "read a property as a row");
});

// --- every column of a calculated table carries sourceColumn (22 Nov 2026) -----------------------------
// Desktop: "Column 'Year' in calculated table 'Financials' is missing the SourceColumn property".
const SC = "tmdl-calculated-table-column-without-sourcecolumn";
const calcTable = (cols) => "table Financials\n" + cols + "\tpartition Financials = calculated\n\t\tmode: import\n\t\tsource =\n\t\t\tDATATABLE ( \"Year\", INTEGER, { { 2025 } } )\n";
const scCodes = (tmdl) => codes(project({ extraTmdl: tmdl }));

check("a calculated-table column with no sourceColumn is caught at the column; with sourceColumn: [Name] it is not", () => {
  const bad = "\tcolumn Year\n\t\tdataType: int64\n\t\tisNameInferred\n";
  const good = "\tcolumn Year\n\t\tdataType: int64\n\t\tisNameInferred\n\t\tsourceColumn: [Year]\n";
  const root = project({ extraTmdl: calcTable(bad) });
  const hits = srv.validateReport({ projectPath: root }).findings.filter((f) => f.code === SC);
  assert.strictEqual(hits.length, 1);
  assert.strictEqual(hits[0].severity, "wont-open");
  assert.ok(/extra\.tmdl:2$/.test(hits[0].location), hits[0].location);
  assert.ok(/Year/.test(hits[0].detail) && /Financials/.test(hits[0].detail), hits[0].detail);
  assert.ok(!scCodes(calcTable(good)).has(SC), "fired on the form Desktop writes");
});

check("columns of an imported table, calculated columns and a calculated table with no columns are NOT flagged", () => {
  const imported = "table Sales\n\tcolumn Year\n\t\tdataType: int64\n\t\tsourceColumn: Year\n\n\tcolumn Note\n\t\tdataType: string\n"
    + "\n\tpartition Sales = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n\t\t\t\t    S = 1\n\t\t\t\tin\n\t\t\t\t    S\n";
  assert.ok(!scCodes(imported).has(SC), "fired on an imported table");
  const calcColumn = calcTable("\tcolumn Year\n\t\tdataType: int64\n\t\tisNameInferred\n\t\tsourceColumn: [Year]\n\n"
    + "\tcolumn Date = DATE ( Financials[Year], 1, 1 )\n\t\tdataType: dateTime\n\t\tsummarizeBy: none\n\n");
  assert.ok(!scCodes(calcColumn).has(SC), "fired on a calculated column of a calculated table");
  assert.ok(!scCodes(calcTable("")).has(SC), "fired on a calculated table that declares no columns");
});

// --- name-valued properties take a plain column name (22 Nov 2026) -----------------------------------
// The TMDL loader refuses `sortByColumn: 'Calendar'[Month Number]`: "Parsing error type - InvalidName ...
// single-quote character in name must be escaped!". Same grammar for a level's `column` and `groupByColumn`.
const NAMEID = "tmdl-column-name-property-not-a-plain-name";
const sortTable = (value) => "table Calendar\n\n\tcolumn 'Month Number'\n\t\tdataType: int64\n\t\tsourceColumn: Month Number\n\n"
  + "\tcolumn Month\n\t\tdataType: string\n\t\tsourceColumn: Month\n\t\tsortByColumn: " + value + "\n";
const nameCodes = (tmdl) => codes(project({ extraTmdl: tmdl }));

check("a DAX-style reference or an unescaped quote in sortByColumn is caught, at the property, as wont-open", () => {
  for (const v of ["'Calendar'[Month Number]", "Calendar[MonthNumber]", "'Month Number", "Month's", "'Month Number' x"]) {
    assert.ok(nameCodes(sortTable(v)).has(NAMEID), "missed sortByColumn: " + v);
  }
  const root = project({ extraTmdl: sortTable("'Calendar'[Month Number]") });
  const hits = srv.validateReport({ projectPath: root }).findings.filter((f) => f.code === NAMEID);
  assert.strictEqual(hits.length, 1);
  assert.strictEqual(hits[0].severity, "wont-open");
  assert.ok(/extra\.tmdl:10$/.test(hits[0].location), hits[0].location);
  assert.ok(/DAX reference/.test(hits[0].detail), hits[0].detail);
});

check("a bare name, a quoted name, an escaped quote and a name with brackets inside quotes are NOT flagged", () => {
  for (const v of ["MonthNumber", "'Month Number'", "'Sales ''Region'' key'", "'Sales [x]'"]) {
    assert.ok(!nameCodes(sortTable(v)).has(NAMEID), "fired on sortByColumn: " + v);
  }
  // another table's properties are not read: a measure's formatString with brackets, a description
  assert.ok(!nameCodes("table T\n\n\tmeasure M = 1\n\t\tformatString: [$-409]#,0\n").has(NAMEID), "read a formatString");
});

check("a hierarchy level's column and groupByColumn take the same grammar", () => {
  const level = (v) => "table Calendar\n\n\tcolumn Year\n\t\tdataType: int64\n\t\tsourceColumn: Year\n\n\thierarchy 'Date hierarchy'\n\n\t\tlevel Year\n\t\t\tcolumn: " + v + "\n";
  assert.ok(nameCodes(level("Calendar[Year]")).has(NAMEID), "missed a level column written as a DAX reference");
  assert.ok(!nameCodes(level("Year")).has(NAMEID), "fired on a level column that is a name");
  const grp = (v) => "table P\n\n\tcolumn Name\n\t\tdataType: string\n\t\tsourceColumn: Name\n\n\t\trelatedColumnDetails\n\t\t\tgroupByColumn: " + v + "\n";
  assert.ok(nameCodes(grp("'P'[Order]")).has(NAMEID), "missed groupByColumn as a DAX reference");
  assert.ok(!nameCodes(grp("'Dimension Order'")).has(NAMEID), "fired on a quoted groupByColumn");
});

check("relationship endpoints keep their own rule: Table.Column with quoted parts is not judged here", () => {
  const rel = "relationship r1\n\tfromColumn: 'Sales Data'.'Order Date'\n\ttoColumn: Calendar.Date\n";
  assert.ok(!nameCodes(rel).has(NAMEID), "judged a relationship endpoint");
});

// --- cultures/ is a FACT, like compatibilityLevel -----------------------------------------
//
// It kills `file.reload/v1` while the project cold-opens perfectly, so the caller needs to know
// before it starts iterating -- but the file is legitimate and Desktop is happy with it, so a
// finding would be wrong. Reported in `checked`, never in `findings`. Cost 7 cold opens on
// 2026-08-15 before anyone connected the dead reload to the file.
check("a cultures/ file is reported in `checked`, never as a finding", () => {
  const root = project({ cultures: true });
  const r = srv.validateReport({ projectPath: root });
  assert.ok(r.checked.some((c) => /cultures\/ file/.test(c)), "the cultures file was not reported");
  assert.ok(!r.findings.some((f) => /culture/i.test(f.code)), "it was reported as a fault");
});

check("no cultures/ file, nothing reported", () => {
  const r = srv.validateReport({ projectPath: project() });
  assert.ok(!r.checked.some((c) => /cultures\/ file/.test(c)), "reported a cultures file that is not there");
});

for (const [s, n, d] of results) console.log(`  ${s}  ${n}${d ? "  <- " + d : ""}`);
const failed = results.filter((r) => r[0] === "FAIL").length;
console.log(`
${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
