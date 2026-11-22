#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * Tests for write_project (project.js).
 *
 * Every fixture is invented and written into a temp directory per run. The cross-checks run the
 * real validator over the written project and read it back with the real profilers, and each
 * zero-finding assertion sits beside a negative control that breaks one written file and expects
 * the named rule to fire, so a zero is never a blind zero.
 *
 *   node test-project.js
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const project = require("./project.js");
const srv = require("./server.js");
const tmdl = require("./tmdl.js");
const T = require("./tmdl-tree.js");
const wr = require("./writers.js");

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (e) { results.push(["FAIL", name, String(e && e.stack || e).split("\n").slice(0, 2).join(" | ").slice(0, 220)]); }
};

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-wp-"));
let n = 0;
/** A source folder holding the given files. */
function bed(files) {
  const dir = path.join(TMP, `src${++n}`);
  for (const [rel, content] of Object.entries(files)) {
    const p = path.join(dir, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, content);
  }
  return dir;
}
/** A target folder that does not exist yet. */
const target = () => path.join(TMP, `out${++n}`);
const RULES = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "skills", "report-authoring", "rules.json"), "utf8")).rules;

function write(args) {
  return project.writeProject(args, { validateReport: srv.validateReport, resolveProject: srv.resolveProject });
}
function refuses(args, re) {
  let err = null;
  try { write(args); } catch (e) { err = e; }
  assert.ok(err, "it did not refuse");
  assert.ok(err instanceof Error && err.name !== "AssertionError", `threw ${err}`);
  assert.ok(re.test(err.message), `refusal did not match ${re}: ${err.message}`);
  return err;
}
function tree(dir, base = dir, out = {}) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) tree(p, base, out);
    else out[path.relative(base, p).split(path.sep).join("/")] = fs.readFileSync(p);
  }
  return out;
}
const csv = (rows) => `${rows.map((r) => r.join(",")).join("\n")}\n`;

// --- fixtures -------------------------------------------------------------------------------

/** Long form: Actual Jan 2024 to Jun 2025, Budget for 2025, two regions, three products. */
function longRows() {
  const rows = [["Date", "Region", "Product", "Scenario", "Amount"]];
  const regions = ["North", "South"];
  const products = ["Alpha", "Beta", "Gamma"];
  for (let y = 2024; y <= 2025; y++) {
    for (let m = 1; m <= 12; m++) {
      const d = `${y}-${String(m).padStart(2, "0")}-01`;
      regions.forEach((r, ri) => products.forEach((p, pi) => {
        const base = 100 * (ri + 1) + 37 * pi + m + (y === 2025 ? 3 : 0);
        if (y === 2024 || m <= 6) rows.push([d, r, p, "Actual", base * (ri ? 9 : 1)]);
        if (y === 2025) rows.push([d, r, p, "Budget", base * (ri ? 9 : 1) + 5]);
      }));
    }
  }
  return rows;
}
const LONG = csv(longRows());
const sumWhere = (rows, pred, col) => rows.slice(1).filter(pred).reduce((s, r) => s + Number(r[col]), 0);

// --- the main project -----------------------------------------------------------------------

const longSrc = bed({ "sales.csv": LONG });
const out1 = target();
let r1 = null;
check("a long-form source writes a project and says what it wrote", () => {
  r1 = write({ name: "Sales", outPath: out1, source: { path: longSrc }, pages: "propose" });
  assert.strictEqual(r1.ok, true);
  const paths = r1.files.map((f) => f.path);
  for (const p of ["Sales.pbip", "Sales.Report/definition.pbir", "Sales.Report/definition/report.json",
    "Sales.Report/definition/version.json", "Sales.Report/definition/pages/pages.json",
    "Sales.SemanticModel/definition.pbism", "Sales.SemanticModel/definition/database.tmdl",
    "Sales.SemanticModel/definition/model.tmdl", "Sales.SemanticModel/definition/relationships.tmdl",
    "Sales.SemanticModel/definition/tables/sales.tmdl", "Sales.SemanticModel/definition/tables/Calendar.tmdl"]) {
    assert.ok(paths.includes(p), `${p} not written`);
  }
  for (const f of r1.files) assert.strictEqual(fs.statSync(path.join(out1, f.path)).size, f.bytes, f.path);
  assert.deepStrictEqual(Object.keys(tree(out1)).sort(), paths.slice().sort(), "files on disk differ from the manifest");
  assert.ok(!paths.some((p) => /\.platform$|cultures\/|\.pbi\//.test(p)), "wrote a file it should not");
  assert.deepStrictEqual(r1.model.measures, ["sales[Amount AC]", "sales[Amount PL]", "sales[Amount PY]"]);
  assert.ok(r1.assumptions.length > 3 && r1.assumptions.every((a) => typeof a === "string" && !a.includes("\n")));
  assert.ok(r1.next.some((x) => /not been rendered/.test(x)) && r1.next.some((x) => /refresh.*save|refreshed and saved/i.test(x)));
});

check("the result never carries the data rows", () => {
  const text = JSON.stringify(r1);
  assert.ok(!text.includes("DATATABLE"), "TMDL text came back");
  assert.ok(text.length < 20000, `result is ${text.length} characters`);
});

check("validation ran every rule and found nothing that stops it opening or drawing", () => {
  assert.strictEqual(r1.validation.rulesRun, RULES.length);
  assert.ok(r1.validation.rulesRun > 60);
  const bad = r1.validation.findings.filter((f) => f.severity === "wont-open" || f.severity === "blank");
  assert.deepStrictEqual(bad, []);
  assert.strictEqual(r1.validation.wontOpen, 0);
  assert.strictEqual(r1.validation.blank, 0);
  // And an independent run of the validator over the same folder agrees.
  const v = srv.validateReport({ projectPath: out1 });
  assert.strictEqual(v.rulesRun, RULES.length);
  assert.strictEqual(v.findings.filter((f) => ["wont-open", "blank"].includes(f.severity)).length, 0);
});

check("read back from disk, row counts and column types equal the CSV", () => {
  const rows = longRows();
  const p = srv.profileProjectTool({ projectPath: out1 });
  assert.strictEqual(p.ok, true, p.error);
  const t = p.tables.find((x) => x.name === "sales");
  assert.strictEqual(t.rowCount, rows.length - 1);
  const types = Object.fromEntries(t.columns.map((c) => [c.name, c.dataType]));
  assert.deepStrictEqual(types, { "Date Year": "int64", "Date MonthNo": "int64", "Date Day": "int64",
    Region: "string", Product: "string", Scenario: "string", Amount: "int64", Date: "dateTime" });
  assert.deepStrictEqual(t.columns.find((c) => c.name === "Region").members.sort(), ["North", "South"]);
  const amount = t.columns.find((c) => c.name === "Amount");
  const nums = rows.slice(1).map((r) => r[4]);
  assert.strictEqual(amount.min, Math.min(...nums));
  assert.strictEqual(amount.max, Math.max(...nums));
  assert.ok(r1.validation.readBack.every((x) => x.ok) && r1.validation.readBack.length === 1);
});

check("a rerun into a second folder is byte-identical", () => {
  const out2 = target();
  write({ name: "Sales", outPath: out2, source: { path: longSrc }, pages: "propose" });
  const a = tree(out1), b = tree(out2);
  assert.deepStrictEqual(Object.keys(a).sort(), Object.keys(b).sort());
  for (const k of Object.keys(a)) assert.ok(a[k].equals(b[k]), `${k} differs`);
});

check("every file is UTF-8 with no BOM and no carriage return; JSON parses", () => {
  const files = tree(out1);
  assert.ok(Object.keys(files).length >= 12);
  for (const [rel, buf] of Object.entries(files)) {
    assert.ok(!(buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf), `${rel} has a BOM`);
    assert.ok(!buf.includes(0x0d), `${rel} has a CR`);
    if (/\.(json|pbip|pbir|pbism)$/.test(rel)) JSON.parse(buf.toString("utf8"));
  }
});

/** Every line indented by tabs only; every line of a partition expression at four tabs or deeper. */
function assertTabs(rel, text) {
  let inSource = false;
  text.split("\n").forEach((line, i) => {
    if (!line.trim()) { inSource = false; return; }
    const lead = /^[\t ]*/.exec(line)[0];
    assert.ok(/^\t*$/.test(lead), `${rel}:${i + 1} indents with a space`);
    if (/^\t\tsource =$/.test(line)) { inSource = true; return; }
    if (inSource) assert.ok(lead.length >= 4, `${rel}:${i + 1} sits above the expression's base indent`);
  });
}

check("every TMDL file is tab-indented, rows sit at four tabs, and ref lines start at column 0", () => {
  const files = tree(out1);
  const tm = Object.entries(files).filter(([k]) => k.endsWith(".tmdl"));
  assert.ok(tm.length >= 5, `${tm.length} TMDL files`);
  for (const [rel, buf] of tm) {
    const text = buf.toString("utf8");
    assertTabs(rel, text);
    assert.ok(text.endsWith("\n") && !text.endsWith("\n\n"), `${rel} ending`);
  }
  const fact = files["Sales.SemanticModel/definition/tables/sales.tmdl"].toString("utf8");
  const rows = fact.split("\n").filter((l) => /^\t*\s*\{\d{4}, /.test(l));
  assert.strictEqual(rows.length, longRows().length - 1);
  for (const l of rows) assert.ok(/^\t{7}\{/.test(l), `row not at 7 tabs: ${JSON.stringify(l.slice(0, 20))}`);
  assert.ok(/\t\tsource =\n\t\t\t\tADDCOLUMNS \(/.test(fact), "source = is not alone on its line");
  assert.ok(!/\{[^{}\n]*DATE\s*\(/.test(fact), "a row calls DATE");
  const model = files["Sales.SemanticModel/definition/model.tmdl"].toString("utf8");
  for (const l of model.split("\n").filter((x) => /ref table/.test(x))) assert.ok(l.startsWith("ref table "), l);
});

check("every emitted TMDL property and enum value is one tmdl-tree knows", () => {
  let props = 0;
  for (const [rel, buf] of Object.entries(tree(out1)).filter(([k]) => k.endsWith(".tmdl"))) {
    const t = T.parseTree(buf.toString("utf8"));
    assert.deepStrictEqual(t.faults, [], `${rel} parse faults`);
    for (const node of t.nodes) {
      if (node.type === "ref") continue;
      const sch = T.SCHEMA[node.type];
      assert.ok(sch, `${rel}: unknown object type ${node.type}`);
      assert.ok(!node.other, `${rel}: unparsed lines under ${node.type} ${node.name}: ${JSON.stringify(node.other)}`);
      for (const p of node.props) {
        props++;
        const k = p.name.toLowerCase();
        assert.ok(sch.props.has(k) || sch.children.has(k), `${rel}:${p.line} ${p.name} is not a property of ${node.type}`);
        if (T.ENUMS[k] && p.delim === ":") assert.ok(T.ENUMS[k].includes(p.value.toLowerCase()), `${rel}:${p.line} ${p.name}: ${p.value}`);
        if (T.BOOLEANS.has(k)) assert.ok(p.delim === "" || /^(true|false)$/i.test(p.value), `${rel}:${p.line} ${p.name}`);
      }
    }
  }
  assert.ok(props > 50, `only ${props} properties read`);
});

check("every column of every calculated table binds by inferred name, every measure has a format", () => {
  const m = tmdl.readModel(out1);
  for (const src of m.sources.filter((s) => /tables\//.test(s.rel))) {
    for (const node of src.tree.nodes) {
      if (node.type === "column") {
        assert.ok(T.propBool(node, "isNameInferred"), `${node.name} lacks isNameInferred`);
        assert.strictEqual(T.propValue(node, "sourceColumn"), `[${node.name}]`);
        assert.ok(T.propValue(node, "dataType") && T.propValue(node, "summarizeBy"), node.name);
      }
      if (node.type === "measure") assert.ok(T.propValue(node, "formatString"), `${node.name} has no formatString`);
    }
  }
});

check("the calendar is marked, starts on 1 January and ends at the last data date", () => {
  const m = tmdl.readModel(out1);
  const dt = tmdl.dateTable(m);
  assert.strictEqual(dt.marked, true);
  assert.strictEqual(dt.table, "Calendar");
  assert.strictEqual(dt.keyColumn, "Date");
  const cal = m.sources.find((s) => /Calendar\.tmdl$/.test(s.rel)).text;
  assert.ok(cal.includes("CALENDAR ( DATE ( 2024, 1, 1 ), DATE ( 2025, 12, 1 ) )"), cal.match(/CALENDAR \(.*\)/)[0]);
  assert.ok(!/CALENDARAUTO/.test(cal));
  const month = m.tables.find((t) => t.name === "Calendar").columns.find((c) => c.name === "Month");
  assert.strictEqual(month.sortByColumn, "MonthNo");
  assert.ok(!/Fiscal/.test(cal), "fiscal columns without a fiscal start month");
  const rel = m.sources.find((s) => /relationships\.tmdl$/.test(s.rel)).text;
  assert.ok(/fromColumn: sales\.Date\n\ttoColumn: Calendar\.Date/.test(rel), rel);
});

check("measures are template DAX on the fact table, quoted, one per scenario, PY day-aligned", () => {
  const m = tmdl.readModel(out1);
  const t = m.tables.find((x) => x.name === "sales");
  const by = Object.fromEntries(t.measures.map((x) => [x.name, x.expression]));
  assert.strictEqual(by["Amount AC"], "CALCULATE ( SUM ( 'sales'[Amount] ), 'sales'[Scenario] = \"Actual\" )");
  assert.strictEqual(by["Amount PL"], "CALCULATE ( SUM ( 'sales'[Amount] ), 'sales'[Scenario] = \"Budget\" )");
  assert.ok(/TREATAS \( SelectedDates, 'Calendar'\[Next year date\] \)/.test(by["Amount PY"]) && /REMOVEFILTERS \( 'Calendar' \)/.test(by["Amount PY"]), by["Amount PY"]);
  assert.ok(!m.tables.some((x) => /^(measures|kpi|goal|calculations)$/i.test(x.name)), "a measure holder table exists");
  assert.strictEqual(t.columns.find((c) => c.name === "Amount").dataType, "int64");
  const amountNode = m.sources.find((s) => /sales\.tmdl$/.test(s.rel)).tree.nodes.find((x) => x.type === "column" && x.name === "Amount");
  assert.ok(T.propBool(amountNode, "isHidden"), "the raw amount is not hidden");
  // The totals the measures should reproduce, from the CSV.
  const rows = longRows();
  assert.ok(sumWhere(rows, (r) => r[3] === "Actual", 4) > 0 && sumWhere(rows, (r) => r[3] === "Budget", 4) > 0);
});

check("pages: \"propose\" binds only measures and columns the written model has", () => {
  const m = tmdl.readModel(out1);
  const has = (entity, prop, kind) => {
    const t = m.tables.find((x) => x.name === entity);
    return Boolean(t && (kind === "Column" ? t.columns : t.measures).some((x) => x.name === prop));
  };
  const files = tree(out1);
  const visuals = Object.entries(files).filter(([k]) => k.endsWith("/visual.json") && !k.includes("/periodSlicer/"));
  assert.ok(visuals.length >= 1, "no visual proposed");
  let bindings = 0;
  for (const [rel, buf] of visuals) {
    const v = JSON.parse(buf.toString("utf8"));
    for (const [role, q] of Object.entries(v.visual.query.queryState)) {
      for (const p of q.projections) {
        const kind = Object.keys(p.field)[0];
        const ent = p.field[kind].Expression.SourceRef.Entity, prop = p.field[kind].Property;
        assert.ok(has(ent, prop, kind), `${rel} ${role}: ${ent}.${prop} (${kind}) is not in the model`);
        bindings++;
      }
    }
    assert.ok(v.visual.query.queryState.Plan, `${rel}: the comparison was not bound`);
    assert.ok(!JSON.stringify(v.visual.query.queryState.Category).includes("Scenario"), "a page is cut by the scenario column");
  }
  assert.ok(bindings >= 3, `${bindings} bindings`);
});

check("the proposed page opens on one window, and AC, PL and PY under its slicer cover the same months", () => {
  const files = tree(out1);
  const slicer = JSON.parse(files["Sales.Report/definition/pages/p1/visuals/periodSlicer/visual.json"].toString("utf8"));
  assert.strictEqual(slicer.visual.visualType, "slicer");
  const f = slicer.visual.objects.general[0].properties.filter.filter;      // double-nested
  assert.strictEqual(f.From[0].Entity, "Calendar");
  assert.strictEqual(f.Where[0].Condition.In.Expressions[0].Column.Property, "YearMonth");
  const months = f.Where[0].Condition.In.Values.map((v) => v[0].Literal.Value.replace(/'/g, ""));
  assert.deepStrictEqual(months, ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]);
  assert.deepStrictEqual(r1.period.months, months);
  // Evaluate the three measures' definitions under that selection, over the rows as written.
  const text = files["Sales.SemanticModel/definition/tables/sales.tmdl"].toString("utf8");
  const rows = text.split("\n").filter((l) => /^\t+\{\d{4}, /.test(l)).map((l) => l.trim().replace(/,$/, "").slice(1, -1).split(", "));
  const mon = (r) => `${r[0]}-${String(r[1]).padStart(2, "0")}`;
  const back = (m) => `${Number(m.slice(0, 4)) - 1}${m.slice(4)}`;
  const sum = (pred) => rows.filter(pred).reduce((s, r) => s + Number(r[6]), 0);
  const inW = new Set(months);
  const ac = sum((r) => r[5] === "\"Actual\"" && inW.has(mon(r)));
  const pl = sum((r) => r[5] === "\"Budget\"" && inW.has(mon(r)));
  const py = sum((r) => r[5] === "\"Actual\"" && inW.has(`${Number(r[0]) + 1}-${String(r[1]).padStart(2, "0")}`));
  // The same three straight from the fixture.
  const src = longRows().slice(1);
  const exp = (s, inMonths) => src.filter((r) => r[3] === s && inMonths.includes(r[0].slice(0, 7))).reduce((t, r) => t + Number(r[4]), 0);
  assert.strictEqual(ac, exp("Actual", months));
  assert.strictEqual(pl, exp("Budget", months));
  assert.strictEqual(py, exp("Actual", months.map(back)));
  // Like with like: every scenario has rows in every month of the window.
  for (const m of months) {
    assert.ok(rows.some((r) => r[5] === "\"Actual\"" && mon(r) === m), `no AC in ${m}`);
    assert.ok(rows.some((r) => r[5] === "\"Budget\"" && mon(r) === m), `no PL in ${m}`);
    assert.ok(rows.some((r) => r[5] === "\"Actual\"" && mon(r) === back(m)), `no PY for ${m}`);
  }
  assert.ok(ac > 0 && pl > 0 && py > 0 && ac !== py, `ac ${ac} pl ${pl} py ${py}`);
  assert.ok(r1.assumptions.some((a) => /^Period: each proposed page opens on Jan 2025 to Jun 2025 .* PL and PY are read over the same months/.test(a)), r1.assumptions.join(" | "));
  const table = JSON.parse(files["Sales.Report/definition/pages/p1/visuals/p1Table/visual.json"].toString("utf8"));
  assert.ok(/Jan 2025 to Jun 2025'$/.test(table.visual.objects.titleSettings[0].properties.text.expr.Literal.Value), "title");
  assert.ok(!r1.validation.findings.some((x) => x.code === "page-filter-hardcodes-the-period" || x.code === "slicer-filter-double-nested"), JSON.stringify(r1.validation.findings));
});

// --- previous year, case by case ----------------------------------------------------------------

/**
 * Evaluates the written PY measure over the rows written in the model, the way the engine would:
 * it reads the measure and the Calendar's own columns from the TMDL, so a change to either is what
 * the test measures. Selection `S` is a set of calendar dates.
 */
function pyEvaluator(outDir, name, table) {
  const files = tree(outDir);
  const cal = files[`${name}.SemanticModel/definition/tables/Calendar.tmdl`].toString("utf8");
  const fact = files[`${name}.SemanticModel/definition/tables/${table}.tmdl`].toString("utf8");
  const [, y1, m1, d1, y2, m2, d2] = /CALENDAR \( DATE \( (\d+), (\d+), (\d+) \), DATE \( (\d+), (\d+), (\d+) \) \)/.exec(cal).map(Number);
  const iso = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const days = [];
  for (let t = Date.UTC(y1, m1 - 1, d1); t <= Date.UTC(y2, m2 - 1, d2); t += 86400000) days.push(new Date(t).toISOString().slice(0, 10));
  const rows = fact.split("\n").filter((l) => /^\t+\{\d{4}, /.test(l)).map((l) => l.trim().replace(/,$/, "").slice(1, -1).split(", "));
  const byDate = new Map();
  for (const r of rows) { const k = iso(+r[0], +r[1], +r[2]); byDate.set(k, (byDate.get(k) || 0) + Number(r[r.length - 1])); }
  const pyExpr = /measure 'Amount PY' =\n([\s\S]*?)\n\t\tformatString/.exec(fact)[1];
  const parts = (s) => s.split("-").map(Number);
  let priorOf;
  if (/TREATAS \( SelectedDates, 'Calendar'\[Next year date\] \)/.test(pyExpr)
    && /"Next year date", IF \( MONTH \( \[Date\] \) = 2 && DAY \( \[Date\] \) = 29, DATE \( YEAR \( \[Date\] \) \+ 1, 2, 28 \), DATE \( YEAR \( \[Date\] \) \+ 1, MONTH \( \[Date\] \), DAY \( \[Date\] \) \) \)/.test(cal)) {
    const ny = (p) => { const [y, m, d] = parts(p); return m === 2 && d === 29 ? iso(y + 1, 2, 28) : iso(y + 1, m, d); };
    priorOf = (S) => days.filter((p) => S.has(ny(p)));
  } else if (/TREATAS \( PYDates, 'Calendar'\[Date\] \)/.test(pyExpr) && /"PY date", IF \( MONTH \( \[Date\] \) = 2 && DAY \( \[Date\] \) = 29, BLANK \(\)/.test(cal)) {
    const pyd = (s) => { const [y, m, d] = parts(s); return m === 2 && d === 29 ? null : iso(y - 1, m, d); };
    priorOf = (S) => [...S].map(pyd).filter((p) => p && days.includes(p));
  } else {
    throw new Error(`unrecognised PY measure: ${pyExpr}`);
  }
  const sumOn = (dates) => [...new Set(dates)].reduce((s, d) => s + (byDate.get(d) || 0), 0);
  return { days, py: (S) => sumOn(priorOf(S)), ac: (S) => sumOn([...S]) };
}

/** Daily rows, or one row on each month's last day, for 2023 to 2025; 2024 is the leap year. */
function pyFixture(grain) {
  const rows = [["Date", "Region", "Amount"]];
  for (let y = 2023; y <= 2025; y++) for (let m = 1; m <= 12; m++) {
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    for (let d = grain === "daily" ? 1 : last; d <= last; d++) {
      rows.push([`${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`, d % 2 ? "North" : "South", (y - 2022) * 10000 + m * 100 + d]);
    }
  }
  return rows;
}
const monthsOf = (a, b) => { const out = []; for (let m = a; m <= b; m = shiftM(m, 1)) out.push(m); return out; };
function shiftM(m, k) { const [y, mo] = m.split("-").map(Number); const t = y * 12 + mo - 1 + k; return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`; }
const PY_CASES = [
  ["a", "one month, Mar 2025", ["2025-03"]],
  ["b", "several months, Jan, Mar and Nov 2025", ["2025-01", "2025-03", "2025-11"]],
  ["c", "year to date, Jan to Sep 2025", monthsOf("2025-01", "2025-09")],
  ["d", "Feb 2025, the year after the leap year", ["2025-02"]],
  ["e", "Feb 2024, the leap year itself", ["2024-02"]],
  ["f", "fiscal year Jul 2024 to Jun 2025", monthsOf("2024-07", "2025-06")],
  ["g", "the first year in the data, 2023", monthsOf("2023-01", "2023-12")],
  ["h", "grand total, every date", monthsOf("2023-01", "2025-12")],
];
const pyResults = [];
for (const grain of ["daily", "month-end"]) {
  check(`previous year returns the same months a year earlier, every case, ${grain} data`, () => {
    const out = target();
    const rows = pyFixture(grain);
    write({ name: "PYT", outPath: out, source: { path: bed({ "flows.csv": csv(rows) }) }, scenarios: { form: "none", py: "timeIntelligence" },
      date: { column: "Date", fiscalYearStartMonth: 7 } });
    const ev = pyEvaluator(out, "PYT", "flows");
    const src = rows.slice(1);
    const bad = [];
    for (const [id, what, months] of PY_CASES) {
      const S = new Set(ev.days.filter((d) => months.includes(d.slice(0, 7))));
      const want = src.filter((r) => months.includes(shiftM(r[0].slice(0, 7), 12))).reduce((s, r) => s + r[2], 0);
      const got = ev.py(S);
      pyResults.push({ grain, id, what, want, got });
      if (got !== want) bad.push(`(${id}) ${what}: expected ${want}, got ${got}`);
    }
    // Additive: the months of a year add up to the year.
    const months2025 = monthsOf("2025-01", "2025-12");
    const byMonth = months2025.reduce((s, m) => s + ev.py(new Set(ev.days.filter((d) => d.startsWith(m)))), 0);
    if (byMonth !== ev.py(new Set(ev.days.filter((d) => d.startsWith("2025"))))) bad.push("the months of 2025 do not add up to 2025");
    assert.deepStrictEqual(bad, []);
  });
}

check("a plan that does not cover the whole window is stated as a limitation", () => {
  const rows = longRows().filter((r, i) => i === 0 || !(r[3] === "Budget" && r[0] >= "2025-04"));
  const r = write({ name: "Short", outPath: target(), source: { path: bed({ "sales.csv": csv(rows) }) }, pages: "propose" });
  assert.ok(r.assumptions.some((a) => a === "Period: PL has no rows for 2025-04, 2025-05, 2025-06, so those months compare against nothing."), r.assumptions.join(" | "));
});

check("publicCustomVisuals equals the set of custom visual types on the pages", () => {
  const files = tree(out1);
  const report = JSON.parse(files["Sales.Report/definition/report.json"].toString("utf8"));
  const types = new Set(Object.entries(files).filter(([k]) => k.endsWith("/visual.json"))
    .map(([, b]) => JSON.parse(b.toString("utf8")).visual.visualType).filter(project.isCustomVisualType));
  assert.deepStrictEqual(report.publicCustomVisuals, [...types].sort());
  assert.strictEqual(report.themeCollection.baseTheme.type, "SharedResources");
  assert.strictEqual(typeof report.themeCollection.baseTheme.reportVersionAtImport, "object");
  assert.ok(!("layoutOptimization" in report));
});

check("an explicit page spec with three visual types registers exactly the custom ones", () => {
  const out = target();
  const pos = { x: 0, y: 0, width: 400, height: 300 };
  const r = write({ name: "Spec", outPath: out, source: { path: longSrc }, pages: { pages: [{
    name: "p1", displayName: "Overview", visuals: [
      { name: "t1", visualType: "tables+", position: pos, bindings: { Category: ["sales.Region"], Values: ["sales.Amount AC"], Plan: ["sales.Amount PL"] } },
      { name: "c1", visualType: "cards", position: { ...pos, x: 400 }, bindings: { Values: ["sales.Amount AC"] } },
      { name: "tx", visualType: "textbox", position: { ...pos, x: 800 } },
    ] }] } });
  const report = JSON.parse(fs.readFileSync(path.join(out, "Spec.Report", "definition", "report.json"), "utf8"));
  assert.deepStrictEqual(report.publicCustomVisuals, [wr.INSTALLED["tables+"], wr.INSTALLED.cards].sort());
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});

check("a page spec that binds a field the model does not have is refused and writes nothing", () => {
  const out = target();
  refuses({ name: "Bad", outPath: out, source: { path: longSrc }, pages: { pages: [{ name: "p1", displayName: "P",
    visuals: [{ name: "t1", visualType: "tables+", position: { x: 0, y: 0, width: 9, height: 9 },
      bindings: { Category: ["sales.Region"], Values: ["sales.Amount"] } }] }] } }, /does not have.*sales has no measure 'Amount'/);
  assert.ok(!fs.existsSync(out), "a refusal wrote files");
});

check("with pages omitted the report has one empty page and registers no custom visual", () => {
  const out = target();
  const r = write({ name: "Empty", outPath: out, source: { path: longSrc } });
  const report = JSON.parse(fs.readFileSync(path.join(out, "Empty.Report", "definition", "report.json"), "utf8"));
  assert.ok(!("publicCustomVisuals" in report));
  const pages = JSON.parse(fs.readFileSync(path.join(out, "Empty.Report", "definition", "pages", "pages.json"), "utf8"));
  assert.deepStrictEqual(pages.pageOrder, ["p1"]);
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0);
});

// --- negative controls ----------------------------------------------------------------------

/** Copies the written project, applies one edit, validates the copy. */
function broken(edit) {
  const dir = target();
  fs.cpSync(out1, dir, { recursive: true });
  edit(dir);
  return srv.validateReport({ projectPath: dir }).findings.map((f) => f.code);
}
const tbl = (dir) => path.join(dir, "Sales.SemanticModel", "definition", "tables", "sales.tmdl");
const rewrite = (p, f) => fs.writeFileSync(p, f(fs.readFileSync(p, "utf8")));

check("negative control: a related column that binds by source name fires its rule", () => {
  const codes = broken((d) => rewrite(tbl(d), (s) => s.replace("\t\tisNameInferred\n\t\tsourceColumn: [Date]\n", "\t\tsourceColumn: Date\n")));
  assert.ok(codes.includes("calculated-column-relationship-binding"), codes.join(", "));
});
check("negative control: an indented ref line fires its rule", () => {
  const codes = broken((d) => rewrite(path.join(d, "Sales.SemanticModel", "definition", "model.tmdl"), (s) => s.replace("ref table Calendar", "\tref table Calendar")));
  assert.ok(codes.includes("tmdl-ref-line-indented"), codes.join(", "));
});
check("negative control: a DATE call in a row fires its rule", () => {
  const codes = broken((d) => rewrite(tbl(d), (s) => s.replace(/\{2024, 1, 1, /, "{DATE(2024,1,1), 1, 1, ")));
  assert.ok(codes.includes("tmdl-datatable-row-calls-a-function"), codes.join(", "));
});
check("negative control: an old definition.pbism version fires its rule", () => {
  const codes = broken((d) => rewrite(path.join(d, "Sales.SemanticModel", "definition.pbism"), (s) => s.replace("\"4.2\"", "\"1.0\"")));
  assert.ok(codes.includes("pbism-version-too-old-for-tmdl"), codes.join(", "));
});
check("negative control: report.json without its base theme fires its rule", () => {
  const codes = broken((d) => rewrite(path.join(d, "Sales.Report", "definition", "report.json"), (s) => {
    const o = JSON.parse(s); delete o.themeCollection; return JSON.stringify(o);
  }));
  assert.ok(codes.includes("report-has-no-base-theme"), codes.join(", "));
});
check("negative control: source = with the expression on its line fires its rule", () => {
  const codes = broken((d) => rewrite(tbl(d), (s) => s.replace("\t\tsource =\n\t\t\t\tADDCOLUMNS (", "\t\tsource = ADDCOLUMNS (")));
  assert.ok(codes.includes("tmdl-partition-source-on-equals-line"), codes.join(", "));
});
check("negative control: a BOM on a table file fires its rule", () => {
  const codes = broken((d) => rewrite(tbl(d), (s) => `﻿${s}`));
  assert.ok(codes.includes("tmdl-bom"), codes.join(", "));
});
check("negative control: a relationship to a column that is not there fires its rule", () => {
  const codes = broken((d) => rewrite(path.join(d, "Sales.SemanticModel", "definition", "relationships.tmdl"), (s) => s.replace("Calendar.Date", "Calendar.Day")));
  assert.ok(codes.includes("tmdl-relationship-endpoint-unresolved"), codes.join(", "));
});

// --- scenario forms -------------------------------------------------------------------------

const WIDE = csv([["Month", "Region", "Actual", "Budget", "Prior Year"],
  ...["2025-01-01", "2025-02-01", "2025-03-01"].flatMap((d, i) => [[d, "East", 100 + i, 110 + i, 90 + i], [d, "West", 200 + i, 190 + i, 180 + i]])]);

check("wide form is refused without a scenarios block, and the refusal says what to pass", () => {
  refuses({ name: "W", outPath: target(), source: { path: bed({ "w.csv": WIDE }) } },
    /"form": "wide", "columns": \{"AC": "Actual", "PL": "Budget", "PY": "Prior Year"\}/);
});

check("wide form: one measure per mapped column, PY from its own column", () => {
  const out = target();
  const r = write({ name: "W", outPath: out, source: { path: bed({ "w.csv": WIDE }) },
    scenarios: { form: "wide", columns: { AC: "Actual", PL: "Budget", PY: "Prior Year" } },
    metrics: [{ name: "Sales", columns: { AC: "Actual", PL: "Budget", PY: "Prior Year" } }], pages: "propose" });
  const t = tmdl.readModel(out).tables.find((x) => x.name === "w");
  const by = Object.fromEntries(t.measures.map((x) => [x.name, x.expression]));
  assert.deepStrictEqual(by, { "Sales AC": "SUM ( 'w'[Actual] )", "Sales PL": "SUM ( 'w'[Budget] )", "Sales PY": "SUM ( 'w'[Prior Year] )" });
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
  const v = Object.entries(tree(out)).find(([k]) => k.endsWith("/visual.json"));
  assert.ok(v, "no page proposed");
  const qs = JSON.parse(v[1].toString("utf8")).visual.query.queryState;
  assert.strictEqual(qs.Values.projections[0].field.Measure.Property, "Sales AC", "the page is not about the actuals");
});

check("none form: an actual per metric, PY by time intelligence when asked", () => {
  const rows = [["Date", "Category", "Units"]];
  for (let m = 1; m <= 24; m++) rows.push([`20${m > 12 ? 25 : 24}-${String(((m - 1) % 12) + 1).padStart(2, "0")}-15`, m % 2 ? "A" : "B", m * 3]);
  const out = target();
  const r = write({ name: "N", outPath: out, source: { path: bed({ "units.csv": csv(rows) }) },
    scenarios: { form: "none", py: "timeIntelligence" } });
  const t = tmdl.readModel(out).tables.find((x) => x.name === "units");
  assert.deepStrictEqual(t.measures.map((x) => x.name), ["Units AC", "Units PY"]);
  assert.strictEqual(t.measures[0].expression, "SUM ( 'units'[Units] )");
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0);
  const cal = fs.readFileSync(path.join(out, "N.SemanticModel", "definition", "tables", "Calendar.tmdl"), "utf8");
  assert.ok(cal.includes("DATE ( 2025, 12, 15 )") && cal.includes("\"Next year date\""));
});

check("a source with no scenario column is refused with the three forms spelled out", () => {
  refuses({ name: "N", outPath: target(), source: { path: bed({ "u.csv": csv([["Date", "Category", "Units"], ["2025-01-01", "A", 1], ["2025-02-01", "B", 2]]) }) } },
    /"form": "none".*"form": "long".*"form": "wide"/);
});

check("long form with a PY member takes PY from it", () => {
  const rows = [["Date", "Region", "Version", "Value"]];
  for (const v of ["AC", "PY", "PL"]) for (const d of ["2025-01-01", "2025-02-01"]) rows.push([d, "X", v, v.length * 10]);
  const out = target();
  write({ name: "P", outPath: out, source: { path: bed({ "v.csv": csv(rows) }) } });
  const t = tmdl.readModel(out).tables.find((x) => x.name === "v");
  const by = Object.fromEntries(t.measures.map((x) => [x.name, x.expression]));
  assert.strictEqual(by["Value PY"], "CALCULATE ( SUM ( 'v'[Value] ), 'v'[Version] = \"PY\" )");
  assert.ok(!fs.readFileSync(path.join(out, "P.SemanticModel", "definition", "tables", "Calendar.tmdl"), "utf8").includes("Next year date"));
});

check("long form with keyed scenarios is read through the scenario table", () => {
  const fact = [["DateKey", "ScenarioKey", "AccountKey", "Amount", "Date"]];
  for (let i = 1; i <= 6; i++) for (const s of [1, 2]) fact.push([20250100 + i, s, (i % 2) + 1, i * 100 * s, `2025-01-0${i}`]);
  const out = target();
  const r = write({ name: "K", outPath: out, source: { path: bed({
    "FactLedger.csv": csv(fact),
    "DimScenario.csv": csv([["ScenarioKey", "ScenarioName"], [1, "Actual"], [2, "Budget"], [3, "Forecast"]]),
    "DimAccount.csv": csv([["AccountKey", "AccountName"], [1, "Sales"], [2, "Costs"]]),
  }) }, pages: "propose" });
  const t = tmdl.readModel(out).tables.find((x) => x.name === "FactLedger");
  const by = Object.fromEntries(t.measures.map((x) => [x.name, x.expression]));
  assert.strictEqual(by["Amount AC"], "CALCULATE ( SUM ( 'FactLedger'[Amount] ), 'FactLedger'[ScenarioKey] = 1 )");
  assert.strictEqual(by["Amount PL"], "CALCULATE ( SUM ( 'FactLedger'[Amount] ), 'FactLedger'[ScenarioKey] = 2 )");
  assert.ok(!by["Amount FC"], "a measure for a scenario with no rows");
  assert.ok(r.assumptions.some((a) => /Forecast \(3\) is declared in DimScenario\[ScenarioName\] and has no rows/.test(a)), r.assumptions.join(" | "));
  assert.ok(r.model.relationships.some((x) => /FactLedger\[AccountKey\] -> DimAccount\[AccountKey\]/.test(x)));
  // A star: the fact holds only keys, so the page is cut by the related dimension's column, never by
  // the scenario table's.
  const v = Object.entries(tree(out)).filter(([k]) => k.endsWith("/visual.json")).map(([, b]) => JSON.parse(b.toString("utf8"))).filter((x) => x.visual.visualType !== "slicer");
  assert.ok(v.length >= 1, `no page proposed: ${r.assumptions.join(" | ")}`);
  const cats = v.map((x) => x.visual.query.queryState.Category.projections[0].field.Column);
  assert.ok(cats.some((c) => c.Expression.SourceRef.Entity === "DimAccount" && c.Property === "AccountName"), JSON.stringify(cats));
  assert.ok(!cats.some((c) => c.Expression.SourceRef.Entity === "DimScenario"), "a page is cut by the scenario table");
  assert.ok(v.every((x) => x.visual.query.queryState.Plan), "the comparison was not bound");
  assert.ok(r.assumptions.some((a) => /profile_project ran on the model text in memory/.test(a)));
  assert.ok(r.assumptions.some((a) => /narrowed to those on the actuals.*leaving out FactLedger\[ScenarioKey\] and the columns of dimscenario.*a direct propose_pages call does not narrow them/.test(a)), r.assumptions.join(" | "));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});

check("scenario members that do not occur, or map no AC, are refused", () => {
  refuses({ name: "S", outPath: target(), source: { path: longSrc }, scenarios: { form: "long", column: "Scenario", members: { Actual: "AC", Plan: "PL" } } }, /'Plan', which never occurs/);
  refuses({ name: "S", outPath: target(), source: { path: longSrc }, scenarios: { form: "long", column: "Scenario", members: { Budget: "PL" } } }, /no value to AC/);
  refuses({ name: "S", outPath: target(), source: { path: longSrc }, scenarios: { form: "long", column: "Scenario", members: { Actual: "XX" } } }, /use AC, PL, FC or PY/);
});

check("a ratio metric divides the two metrics per scenario", () => {
  const rows = [["Date", "Region", "Scenario", "Revenue", "Cost"]];
  for (const s of ["Actual", "Budget"]) for (const d of ["2025-01-01", "2025-02-01"]) rows.push([d, "N", s, 100, 60]);
  const out = target();
  write({ name: "R", outPath: out, source: { path: bed({ "pl.csv": csv(rows) }) },
    metrics: [{ name: "Revenue", column: "Revenue" }, { name: "Cost", column: "Cost" }, { name: "Cost ratio", numerator: "Cost", denominator: "Revenue" }] });
  const t = tmdl.readModel(out).tables.find((x) => x.name === "pl");
  const m = t.measures.find((x) => x.name === "Cost ratio AC");
  assert.strictEqual(m.expression, "DIVIDE ( [Cost AC], [Revenue AC] )");
  assert.strictEqual(m.formatString, "0.0%");
});

// --- statement order ------------------------------------------------------------------------

const PL_ROWS = [["Date", "Line", "LineNo", "Scenario", "Amount"],
  ...[1, 2, 3].flatMap((m) => [["Revenue", 1, 500], ["Cost of sales", 2, -200], ["Opex", 3, -150], ["Tax", 4, -40]]
    .flatMap(([l, no, a]) => [[`2025-0${m}-01`, l, no, "Actual", a * m], [`2025-0${m}-01`, l, no, "Budget", a * m + 7]]))];

check("an order column on the category's table sorts the proposed statement in that order", () => {
  const out = target();
  const r = write({ name: "PL", outPath: out, source: { path: bed({ "pl.csv": csv(PL_ROWS) }) }, pages: "propose" });
  const files = tree(out);
  const t = T.parseTree(files["PL.SemanticModel/definition/tables/pl.tmdl"].toString("utf8"));
  const line = t.nodes.find((n) => n.type === "column" && n.name === "Line");
  assert.strictEqual(T.propValue(line, "sortByColumn"), "LineNo");
  const vis = Object.entries(files).filter(([k]) => k.endsWith("/visual.json")).map(([, b]) => JSON.parse(b.toString("utf8"))).filter((x) => x.visual.visualType !== "slicer");
  const onLine = vis.find((x) => x.visual.query.queryState.Category.projections[0].field.Column.Property === "Line");
  assert.ok(onLine, "no page by Line");
  const s = onLine.visual.objects.sortSettings[0].properties;
  assert.strictEqual(s.categorySort.expr.Literal.Value, "0D");
  assert.ok(!("columnName" in s), "still sorted by a value column");
  assert.ok(r.assumptions.some((a) => /follow pl\[LineNo\] \(sortByColumn/.test(a)), r.assumptions.join(" | "));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});
check("an order column that gives one line two orders is not used, and says so", () => {
  const rows = PL_ROWS.map((r, i) => (i === 3 ? [r[0], r[1], 9, r[3], r[4]] : r));
  const r = write({ name: "PL", outPath: target(), source: { path: bed({ "pl.csv": csv(rows) }) }, pages: "propose" });
  assert.ok(r.assumptions.some((a) => /pl\[LineNo\] reads like an order column, but some Line values carry two different orders/.test(a)), r.assumptions.join(" | "));
});
check("with no order column the value sort is kept and the assumption says why", () => {
  assert.ok(r1.assumptions.some((a) => /sorted by value, as propose_pages sorts them; sales has no order column/.test(a)), r1.assumptions.join(" | "));
});

// --- order as input -------------------------------------------------------------------------

/** A ledger in miniature: lines in no useful order in the file, each line in one group. */
const LEDGER_LINES = [["Travel", "Overheads", -30], ["Sales", "Income", 900], ["Interest", "Finance", -12],
  ["Staff", "Overheads", -240], ["Cost of goods", "Direct cost", -410]];
const LEDGER_ROWS = [["Date", "Line", "Group", "Scenario", "Amount"],
  ...[1, 2, 3].flatMap((m) => LEDGER_LINES.flatMap(([l, g, a]) => [[`2025-0${m}-01`, l, g, "Actual", a * m], [`2025-0${m}-01`, l, g, "Budget", a * m + 5]]))];
const LINE_ORDER = ["Sales", "Cost of goods", "Staff", "Travel", "Interest"];
const GROUP_ORDER = ["Income", "Direct cost", "Overheads", "Finance"];
const ledgerSrc = (rows = LEDGER_ROWS) => bed({ "ledger.csv": csv(rows) });
/** The embedded rows of a written table, each parsed into its cells. */
function embeddedRows(text) {
  return text.split("\n").filter((l) => /^\t+\{.*\},?$/.test(l)).map((l) => JSON.parse(`[${l.trim().replace(/,$/, "").slice(1, -1)}]`));
}
const tableTree = (files, rel) => T.parseTree(files[rel].toString("utf8"));
const colNode = (t, name) => t.nodes.find((x) => x.type === "column" && x.name === name);

check("order writes a hidden order column into the embedded rows and sets sortByColumn", () => {
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc() }, order: [{ column: "Line", values: LINE_ORDER }] });
  const files = tree(out);
  const rel = "L.SemanticModel/definition/tables/ledger.tmdl";
  const t = tableTree(files, rel);
  assert.strictEqual(T.propValue(colNode(t, "Line"), "sortByColumn"), "'Line Order'");
  const oc = colNode(t, "Line Order");
  assert.ok(oc, "no order column");
  assert.strictEqual(T.propValue(oc, "dataType"), "int64");
  assert.ok(T.propBool(oc, "isHidden"), "the order column is not hidden");
  const rows = embeddedRows(files[rel].toString("utf8"));
  assert.strictEqual(rows.length, LEDGER_ROWS.length - 1);
  for (const row of rows) assert.strictEqual(row[row.length - 1], LINE_ORDER.indexOf(row[3]) + 1, JSON.stringify(row));
  const read = tmdl.readModel(out).tables.find((x) => x.name === "ledger");
  assert.ok(read.columns.some((c) => c.name === "Line Order"));
  assert.ok(r.validation.readBack.every((x) => x.ok), JSON.stringify(r.validation.readBack));
  assert.strictEqual(r.validation.findings.length, 0, JSON.stringify(r.validation.findings));
  assert.ok(r.assumptions.some((a) => /ledger\[Line\] is sorted by ledger\[Line Order\] \(sortByColumn\).*Sales 1, Cost of goods 2, Staff 3, Travel 4, Interest 5\./.test(a)), r.assumptions.join(" | "));
  assert.ok(!r.next.some((x) => /write the project again into a new, empty folder/.test(x)), "the rerun advice is given although order was passed");
  // Negative control: the same file with the sort column written as a DAX reference fires the named rule.
  const bad = path.join(TMP, `neg${++n}`);
  for (const [k, b] of Object.entries(files)) {
    const p = path.join(bad, ...k.split("/"));
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, k === rel ? b.toString("utf8").replace("sortByColumn: 'Line Order'", "sortByColumn: 'ledger'[Line Order]") : b);
  }
  const v = srv.validateReport({ projectPath: bad });
  assert.ok(v.findings.some((f) => f.code === "tmdl-column-name-property-not-a-plain-name"), JSON.stringify(v.findings.map((f) => f.code)));
});
check("members not listed in order go last in source order, and the assumptions name them", () => {
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc() }, order: [{ column: "Line", values: ["Sales", "Cost of goods"] }] });
  const rows = embeddedRows(tree(out)["L.SemanticModel/definition/tables/ledger.tmdl"].toString("utf8"));
  // Source order of the unlisted lines: Travel, Interest, Staff.
  const want = { Sales: 1, "Cost of goods": 2, Travel: 3, Interest: 4, Staff: 5 };
  for (const row of rows) assert.strictEqual(row[row.length - 1], want[row[3]], JSON.stringify(row));
  assert.ok(r.assumptions.some((a) => /3 member\(s\) of ledger\[Line\] are not listed in order and go last, in the order they first appear in the source: Travel, Interest, Staff\./.test(a)), r.assumptions.join(" | "));
  assert.strictEqual(r.validation.findings.length, 0, JSON.stringify(r.validation.findings));
});
check("order refuses a value that never occurs, a value listed twice and one member listed in two spellings", () => {
  const src = ledgerSrc();
  const args = (order) => ({ name: "L", outPath: target(), source: { path: src }, order });
  refuses(args([{ column: "Line", values: ["Sales", "Rebates"] }]), /lists 'Rebates', which never occurs in ledger\[Line\]\. Members: Travel, Sales, Interest, Staff, Cost of goods\./);
  refuses(args([{ column: "Line", values: ["Sales", "Staff", "Sales"] }]), /lists 'Sales' twice\. Power BI sorts ledger\[Line\] by one value per member/);
  refuses(args([{ column: "Line", values: ["Sales", "SALES "] }]), /lists 'Sales' and 'SALES ', which Power BI compares as one member of ledger\[Line\].*cannot take two positions/);
  refuses(args([{ column: "Line", values: ["Sales"] }, { column: "ledger[Line]", values: ["Staff"] }]), /order names ledger\[Line\] in entries 0 and 1/);
  refuses(args([{ column: "Amount", values: ["1"] }]), /ledger\[Amount\] is a number column, which sorts by its own value/);
  refuses(args([{ column: "Account", values: ["Sales"] }]), /order\[0\]\.column 'Account' is not a column of ledger/);
  refuses(args([{ column: "other[Line]", values: ["Sales"] }]), /names a table that is not loaded/);
  refuses(args([{ column: "Line", values: [] }]), /non-empty array of the column's members/);
  refuses(args([{ column: "Line", values: ["Sales"], sort: "asc" }]), /unknown order\[0\] field\(s\): sort/);
  refuses(args({ column: "Line" }), /order must be a non-empty array/);
  const n0 = fs.readdirSync(TMP).length;
  refuses(args([{ column: "Line", values: ["Nope"] }]), /never occurs/);
  assert.strictEqual(fs.readdirSync(TMP).length, n0, "a refusal wrote something");
});
check("a column that exists on two tables needs its table named", () => {
  const src = bed({
    "sales.csv": csv([["Date", "RegionKey", "Line", "Scenario", "Amount"], ["2025-01-01", "1", "Sales", "Actual", "10"], ["2025-01-01", "2", "Staff", "Actual", "-4"], ["2025-01-01", "1", "Sales", "Budget", "11"]]),
    "regions.csv": csv([["RegionKey", "Line"], ["1", "North"], ["2", "South"]]),
  });
  refuses({ name: "L", outPath: target(), source: { path: src }, order: [{ column: "Line", values: ["Sales"] }] }, /'Line' is a column of (sales and regions|regions and sales)\. Name the table/);
  const r = write({ name: "L", outPath: target(), source: { path: src }, order: [{ column: "regions[Line]", values: ["South", "North"] }] });
  assert.ok(r.assumptions.some((a) => /regions\[Line\] is sorted by regions\[Line Order\]/.test(a)), r.assumptions.join(" | "));
});
check("a member spelled two ways in the source takes one position, and the assumption says so", () => {
  // The fourth data row is the first Budget row of Sales; written here in lower case.
  const rows = LEDGER_ROWS.map((r, i) => (i === 4 ? [r[0], "sales", r[2], r[3], r[4]] : r));
  assert.strictEqual(LEDGER_ROWS[4][1], "Sales");
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc(rows) }, order: [{ column: "Line", values: LINE_ORDER }] });
  const cells = embeddedRows(tree(out)["L.SemanticModel/definition/tables/ledger.tmdl"].toString("utf8"));
  assert.ok(cells.filter((c) => c[3].toLowerCase() === "sales").every((c) => c[c.length - 1] === 1));
  assert.ok(r.assumptions.some((a) => /Sales, sales are one member of ledger\[Line\] to Power BI/.test(a)), r.assumptions.join(" | "));
});
check("a cell with a line break is the member it is written as, and takes that member's position", () => {
  // The row writes "Cost of\ngoods" as "Cost of goods", so the two are one member in the model.
  const text = csv(LEDGER_ROWS).replace("2025-01-01,Cost of goods,Direct cost,Actual", "2025-01-01,\"Cost of\ngoods\",Direct cost,Actual");
  assert.ok(text.includes("\"Cost of\ngoods\""));
  const src = bed({ "ledger.csv": text });
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: src }, order: [{ column: "Line", values: LINE_ORDER }] });
  const cells = embeddedRows(tree(out)["L.SemanticModel/definition/tables/ledger.tmdl"].toString("utf8"));
  const cog = cells.filter((c) => c[3] === "Cost of goods");
  assert.strictEqual(cog.length, 6);
  assert.ok(cog.every((c) => c[c.length - 1] === 2), JSON.stringify(cog));
  assert.ok(!r.assumptions.some((a) => /not listed in order/.test(a)), r.assumptions.join(" | "));
  refuses({ name: "L", outPath: target(), source: { path: src }, order: [{ column: "Line", values: ["Cost of goods", "Cost of\ngoods"] }] },
    /which Power BI compares as one member of ledger\[Line\]/);
});
check("a group column takes its own order entry, and without one the assumptions say it sorts alphabetically", () => {
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc() },
    order: [{ column: "Group", values: GROUP_ORDER }, { column: "Line", values: LINE_ORDER }] });
  const files = tree(out);
  const rel = "L.SemanticModel/definition/tables/ledger.tmdl";
  const t = tableTree(files, rel);
  assert.strictEqual(T.propValue(colNode(t, "Group"), "sortByColumn"), "'Group Order'");
  assert.strictEqual(T.propValue(colNode(t, "Line"), "sortByColumn"), "'Line Order'");
  const decl = files[rel].toString("utf8").split("\n").find((l) => l.includes("\"Group Order\", INTEGER"));
  assert.ok(decl && decl.indexOf("\"Group Order\"") < decl.indexOf("\"Line Order\""), "the order columns are not in the order entries' order");
  for (const row of embeddedRows(files[rel].toString("utf8"))) {
    assert.strictEqual(row[row.length - 2], GROUP_ORDER.indexOf(row[4]) + 1, JSON.stringify(row));
    assert.strictEqual(row[row.length - 1], LINE_ORDER.indexOf(row[3]) + 1, JSON.stringify(row));
  }
  assert.ok(!r.assumptions.some((a) => /keeps the model's default order, alphabetical/.test(a)), r.assumptions.join(" | "));
  assert.strictEqual(r.validation.findings.length, 0, JSON.stringify(r.validation.findings));
  const r2 = write({ name: "L", outPath: target(), source: { path: ledgerSrc() }, order: [{ column: "Line", values: LINE_ORDER }] });
  assert.ok(r2.assumptions.some((a) => a.includes("each Line belongs to one Group. A two-level category (Group, then Line) sorts each level by its own sortByColumn, "
    + "so Group keeps the model's default order, alphabetical, unless order has an entry for it too, for example "
    + JSON.stringify({ column: "Group", values: GROUP_ORDER }))), r2.assumptions.join(" | "));
});
check("a two-level statement table (group, then line) over both orders validates clean", () => {
  const out = target();
  const ref = (p) => ({ entity: "ledger", property: p, kind: "Measure" });
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc() },
    order: [{ column: "Group", values: GROUP_ORDER }, { column: "Line", values: LINE_ORDER }],
    pages: { pages: [{ name: "pl", displayName: "P&L", visuals: [{ name: "statement", visualType: "ZebraBITablesPlus3E6085701D7B426980C3859B16327993",
      position: { x: 16, y: 16, z: 1000, width: 1000, height: 600, tabOrder: 1000 },
      bindings: { Category: [{ entity: "ledger", property: "Group", kind: "Column" }, { entity: "ledger", property: "Line", kind: "Column" }],
        Values: [ref("Amount AC")], Plan: [ref("Amount PL")] },
      properties: { "sortSettings.categorySort": 0, "sortSettings.chartSort": 0 } }] }] } });
  assert.strictEqual(r.validation.findings.length, 0, JSON.stringify(r.validation.findings));
  assert.ok(!(r.warnings || []).some((w) => /no sortSettings/.test(w)), JSON.stringify(r.warnings));
});
check("proposed pages sort an ordered column by its order, not by value", () => {
  const out = target();
  const r = write({ name: "L", outPath: out, source: { path: ledgerSrc() }, order: [{ column: "Line", values: LINE_ORDER }], pages: "propose" });
  const vis = Object.entries(tree(out)).filter(([k]) => k.endsWith("/visual.json")).map(([, b]) => JSON.parse(b.toString("utf8"))).filter((x) => x.visual.visualType !== "slicer");
  const onLine = vis.find((x) => x.visual.query.queryState.Category.projections[0].field.Column.Property === "Line");
  assert.ok(onLine, "no page by Line");
  const s = onLine.visual.objects.sortSettings[0].properties;
  assert.strictEqual(s.categorySort.expr.Literal.Value, "0D");
  assert.strictEqual(s.chartSort.expr.Literal.Value, "0D");
  assert.ok(!("columnName" in s), "still sorted by a value column");
  assert.ok(r.assumptions.some((a) => /rows of ledger\[Line\] on page Line follow the order passed in order \(ledger\[Line Order\], sortByColumn/.test(a)), r.assumptions.join(" | "));
  assert.ok(!vis.some((x) => x.visual.query.queryState.Category.projections.some((p) => /Order$/.test(p.field.Column.Property))), "a page is cut by the order column");
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});
check("an explicit order wins over the source's own order column, and the result says so", () => {
  const out = target();
  const reversed = ["Tax", "Opex", "Cost of sales", "Revenue"];
  const r = write({ name: "PL", outPath: out, source: { path: bed({ "pl.csv": csv(PL_ROWS) }) }, order: [{ column: "Line", values: reversed }], pages: "propose" });
  const t = tableTree(tree(out), "PL.SemanticModel/definition/tables/pl.tmdl");
  assert.strictEqual(T.propValue(colNode(t, "Line"), "sortByColumn"), "'Line Order'");
  assert.ok(r.assumptions.some((a) => /pl\[LineNo\] reads like an order column; pl\[Line\] follows the order passed in order instead\./.test(a)), r.assumptions.join(" | "));
  assert.ok(!r.assumptions.some((a) => /follow pl\[LineNo\]/.test(a)), r.assumptions.join(" | "));
});
check("the same arguments with order write byte-identical projects", () => {
  const args = (outPath) => ({ name: "L", outPath, source: { path: ledgerSrc() },
    order: [{ column: "Group", values: GROUP_ORDER }, { column: "Line", values: LINE_ORDER }], pages: "propose" });
  const a = target(), b = target();
  write(args(a));
  write(args(b));
  const ta = tree(a), tb = tree(b);
  assert.deepStrictEqual(Object.keys(ta).sort(), Object.keys(tb).sort());
  for (const k of Object.keys(ta)) assert.ok(ta[k].equals(tb[k]), `${k} differs between two runs`);
});
check("without order the result says to write the project again with order for a statement", () => {
  const r = write({ name: "L", outPath: target(), source: { path: ledgerSrc() } });
  assert.ok(r.next.some((x) => /write the project again into a new, empty folder with order/.test(x) && /circular dependency/.test(x)), r.next.join(" | "));
});
check("the server schema lists order with column and values", () => {
  const o = srv.CALLABLE.write_project.schema.properties.order;
  assert.ok(o && o.type === "array");
  assert.deepStrictEqual(o.items.required, ["column", "values"]);
  assert.ok(/statement/.test(srv.CALLABLE.write_project.description));
});

// --- the fiscal year ------------------------------------------------------------------------

check("a fiscal start month adds Fiscal Year and Fiscal Period to the calendar", () => {
  const out = target();
  const r = write({ name: "F", outPath: out, source: { path: longSrc }, date: { column: "Date", fiscalYearStartMonth: 7 } });
  const cal = fs.readFileSync(path.join(out, "F.SemanticModel", "definition", "tables", "Calendar.tmdl"), "utf8");
  assert.ok(cal.includes("\"Fiscal Year\", YEAR ( EDATE ( [Date], 6 ) )"), cal);
  assert.ok(cal.includes("\"Fiscal Period\", MOD ( MONTH ( [Date] ) - 7, 12 ) + 1"));
  assert.ok(r.assumptions.some((a) => /Fiscal Year is named by the calendar year in which it ends/.test(a)));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0);
  refuses({ name: "F", outPath: target(), source: { path: longSrc }, date: { fiscalYearStartMonth: 13 } }, /1 to 12/);
});

// --- names ----------------------------------------------------------------------------------

check("reserved, dotted, spaced, duplicate and colliding names are made safe and reported", () => {
  const out = target();
  const r = write({ name: "Names", outPath: out, source: { path: bed({
    "KPI.csv": csv([["Date", "Unit.Name", "Unit  Name", "unit name", "Scenario", "Amount", "Amount AC"],
      ["2025-01-01", "a", "b", "c", "Actual", 1, 7], ["2025-02-01", "a", "b", "c", "Budget", 2, 8]]),
    "Measures.csv": csv([["Code", "Label"], ["x1", "One"], ["x2", "Two"]]),
    "sub/KPI Data.csv": csv([["Code", "Label"], ["y1", "One"], ["y2", "Two"]]),
  }) }, metrics: [{ name: "Amount", column: "Amount" }],
    tables: [{ source: "Measures" }, { source: "sub/KPI Data.csv" }] });
  const names = r.model.tables.map((t) => t.name);
  assert.ok(names.includes("KPI Data") && names.includes("KPI Data 2") && names.includes("Measures Data"), names.join(", "));
  assert.ok(!r.model.tables.filter((t) => t.source).some((t) => /^(kpi|measures|calendar)$/i.test(t.name)));
  const m = tmdl.readModel(out);
  const t = m.tables.find((x) => x.name === "KPI Data");
  const cols = t.columns.map((c) => c.name);
  for (const c of ["Unit Name", "Unit Name 2", "unit name 3", "Amount", "Amount AC Amt"]) assert.ok(cols.includes(c), `${c} missing: ${cols.join(", ")}`);
  assert.ok(t.measures.some((x) => x.name === "Amount AC"));
  assert.ok(r.assumptions.some((a) => /KPI is named KPI Data/.test(a)));
  assert.ok(r.assumptions.some((a) => /renamed Amount AC Amt and hidden/.test(a)));
  const text = m.sources.map((s) => s.text).join("\n");
  assert.ok(/table 'KPI Data'/.test(text) && /column 'Unit Name 2'/.test(text));
  assert.ok(!/(^|[^'])KPI Data\[/.test(text), "an unquoted table reference in DAX");
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});

// --- cell encodings -------------------------------------------------------------------------

check("quotes, commas, line breaks and non-ASCII text survive; blanks are written as stated", () => {
  const body = "Date,Name,Code,Scenario,Amount,Due\n"
    + "2025-01-01,\"He said \"\"hi\"\", ok\",007,Actual,10,2025-02-01\n"
    + "2025-01-02,\"two\nlines\",012,Actual,,\n"
    + "2025-01-03,Zürich ✓ 東京,013,Budget,30,2025-03-01\n"
    + "2025-01-04,,014,Budget,40,2025-04-01\n";
  const out = target();
  const r = write({ name: "Cells", outPath: out, source: { path: bed({ "c.csv": body }) } });
  const p = srv.profileProjectTool({ projectPath: out });
  const t = p.tables.find((x) => x.name === "c");
  assert.strictEqual(t.rowCount, 4);
  const col = (name) => t.columns.find((c) => c.name === name);
  assert.deepStrictEqual(col("Name").members.sort(), ["He said \"hi\", ok", "Zürich ✓ 東京", "two lines"].sort());
  assert.strictEqual(col("Name").nulls, 1);
  assert.strictEqual(col("Code").dataType, "string");
  assert.deepStrictEqual(col("Code").members, ["007", "012", "013", "014"]);
  assert.strictEqual(col("Amount").dataType, "int64");
  const text = fs.readFileSync(path.join(out, "Cells.SemanticModel", "definition", "tables", "c.tmdl"), "utf8");
  assert.ok(text.includes("\"He said \"\"hi\"\", ok\""), "a quote was not doubled");
  assert.ok(text.includes("\"Due\", IF ( [Due Year] = 0, BLANK (), DATE ( [Due Year], [Due MonthNo], [Due Day] ) )"), "a blank date is not guarded");
  assert.ok(r.assumptions.some((a) => /Code.*leading zeros.*text/.test(a)));
  assert.ok(r.assumptions.some((a) => /c\[Amount\]: 1 blank cell\(s\) are written as 0/.test(a)));
  assert.ok(r.assumptions.some((a) => /1 text cell\(s\) held a line break/.test(a)));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
  assert.ok(r.validation.readBack.every((x) => x.ok));
});

check("numbers are written culture-invariant with their digits kept", () => {
  assert.strictEqual(project.plainNumber("+1618.50"), "1618.50");
  assert.strictEqual(project.plainNumber(".5"), "0.5");
  assert.strictEqual(project.plainNumber("-.25"), "-0.25");
  assert.strictEqual(project.plainNumber("1.5e3"), "1500");
  assert.strictEqual(project.plainNumber("2.5E-4"), "0.00025");
  assert.strictEqual(project.plainNumber("-0"), "0");
  assert.strictEqual(project.plainNumber("123456789012345678"), "123456789012345678");
});

check("default metrics are read from whole words of the column name", () => {
  const rows = [["Date", "source", "net_generation", "growthRate", "unit_price", "StoreKey"]];
  for (let m = 1; m <= 3; m++) rows.push([`2025-0${m}-01`, m % 2 ? "Wind" : "Gas", m * 10, 0.5, 3, m]);
  const r = write({ name: "G", outPath: target(), source: { path: bed({ "gen.csv": csv(rows) }) }, scenarios: { form: "none" } });
  assert.deepStrictEqual(r.model.measures, ["gen[net_generation AC]"]);
  assert.ok(r.assumptions.some((a) => /Not made a metric: growthRate, unit_price/.test(a)), r.assumptions.join(" | "));
});

check("a JSON array source is read like a CSV", () => {
  const data = [];
  for (let m = 1; m <= 14; m++) data.push({ series: m % 2 ? "Retail" : "Mining", count: m * 7, rate: 1.5, date: `20${m > 12 ? 25 : 24}-${String(((m - 1) % 12) + 1).padStart(2, "0")}-01T08:00:00.000Z` });
  const out = target();
  const r = write({ name: "J", outPath: out, source: { path: bed({ "jobs.json": JSON.stringify(data) }) }, scenarios: { form: "none" }, pages: "propose" });
  assert.deepStrictEqual(r.model.measures, ["jobs[count AC]", "jobs[count PY]"]);
  assert.ok(r.assumptions.some((a) => /Not made a metric: rate/.test(a)));
  assert.ok(r.assumptions.some((a) => /time of day/.test(a)));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});

// --- refusals that protect the numbers --------------------------------------------------------

check("a decimal comma is refused with the column named", () => {
  refuses({ name: "D", outPath: target(), source: { path: bed({ "d.csv": "Date;Region;Amount\n2025-01-01;N;1,5\n2025-02-01;S;2,25\n" }) }, scenarios: { form: "none" } },
    /d\[Amount\] holds numbers written with a comma/);
});
check("a mixed-type column is refused, and loads as text when asked", () => {
  const rows = [["Date", "Region", "Amount"]];
  for (let i = 1; i <= 60; i++) rows.push([`2025-01-${String((i % 28) + 1).padStart(2, "0")}`, "N", i === 30 ? "n/a" : i]);
  const src = bed({ "m.csv": csv(rows) });
  refuses({ name: "M", outPath: target(), source: { path: src }, scenarios: { form: "none" }, metrics: [{ column: "Amount" }] }, /m\[Amount\] is a mixed-type column.*'n\/a'/);
  refuses({ name: "M", outPath: target(), source: { path: src }, scenarios: { form: "none" }, metrics: [{ column: "Amount" }],
    tables: [{ source: "m", types: { Amount: "text" } }] }, /is text, so it cannot be summed/);
});
check("an unparseable date is refused with the column named", () => {
  const rows = [["Date", "Region", "Amount"]];
  for (let i = 1; i <= 60; i++) rows.push([i === 9 ? "2025-13-45" : `2025-02-${String((i % 28) + 1).padStart(2, "0")}`, "N", i]);
  refuses({ name: "U", outPath: target(), source: { path: bed({ "u.csv": csv(rows) }) }, scenarios: { form: "none" } }, /u\[Date\] is a date column, but '2025-13-45'/);
});
check("a one side with keys that differ only in case is refused", () => {
  refuses({ name: "K", outPath: target(), source: { path: bed({
    "Fact.csv": csv([["Date", "RegionKey", "Amount"], ["2025-01-01", "north", 1], ["2025-01-02", "North", 2], ["2025-01-03", "south", 3]]),
    "Region.csv": csv([["RegionKey", "Label"], ["north", "N1"], ["North", "N2"], ["south", "S"]]),
  }) }, scenarios: { form: "none" } }, /Region\[RegionKey\] would be the one side.*holds 'north' 2 times.*without case/);
});
check("an exact duplicate key on a dimension is refused with the true reason, named or not", () => {
  const files = {
    "sales.csv": csv([["Date", "ProductKey", "Amount"], ["2025-01-01", 1, 5], ["2025-01-02", 2, 6], ["2025-01-03", 2, 7]]),
    "product.csv": csv([["ProductKey", "Product"], [1, "A"], [2, "B"], [2, "B2"]]),
  };
  refuses({ name: "D", outPath: target(), source: { path: bed(files) }, scenarios: { form: "none" } },
    /product\.csv: ProductKey is the key sales\.csv relates to, and it holds '2' 2 times/);
  refuses({ name: "D", outPath: target(), source: { path: bed(files) }, scenarios: { form: "none" }, tables: [{ source: "product" }] },
    /holds '2' 2 times/);
});
check("a small fact whose keys happen to be unique is never the one side of a relationship", () => {
  const r = write({ name: "B", outPath: target(), source: { path: bed({
    "sales.csv": csv([["Date", "ProductKey", "Amount"], ["2025-01-01", 1, 5], ["2025-01-02", 2, 6]]),
    "product.csv": csv([["ProductKey", "Product"], [1, "A"], [2, "B"]]),
  }) }, scenarios: { form: "none" } });
  assert.ok(r.model.relationships.some((x) => x === "sales[ProductKey] -> product[ProductKey]"), r.model.relationships.join("; "));
  assert.ok(!r.model.relationships.some((x) => /^product\[/.test(x)), r.model.relationships.join("; "));
});
check("scenario values that differ only in case are one member, counted and said as the measure counts them", () => {
  const rows = [["Date", "Region", "Scenario", "Amount"],
    ["2025-01-01", "N", "Actual", 1], ["2025-01-02", "N", "actual", 10], ["2025-01-03", "N", "Budget", 100], ["2025-01-04", "N", "BUDGET", 1000]];
  const r = write({ name: "S", outPath: target(), source: { path: bed({ "f.csv": csv(rows) }) } });
  assert.ok(r.assumptions.some((a) => a === "Scenarios: Actual, actual are one value to Power BI, which compares text without case, so the AC measure counts all 2 of their rows."), r.assumptions.join(" | "));
  assert.ok(r.assumptions.some((a) => /Budget, BUDGET are one value .* the PL measure counts all 2/.test(a)), r.assumptions.join(" | "));
  assert.ok(!r.assumptions.some((a) => /are in no measure/.test(a)), "rows the measure counts are said to be in no measure");
  refuses({ name: "S", outPath: target(), source: { path: bed({ "f.csv": csv(rows) }) }, scenarios: { form: "long", column: "Scenario", members: { Actual: "AC", actual: "PL" } } },
    /'Actual' and 'actual' to different codes, but they differ only in case/);
});
check("an impossible date is refused with its row, in a slash form and in ISO", () => {
  const slash = [["Date", "Region", "Amount"], ...Array.from({ length: 60 }, (_, i) => [i === 7 ? "31/02/2024" : `${String((i % 28) + 1).padStart(2, "0")}/03/2024`, "N", i])];
  refuses({ name: "I", outPath: target(), source: { path: bed({ "d.csv": csv(slash) }) }, scenarios: { form: "none" } },
    /d\[Date\] is a date column, but '31\/02\/2024' \(data row 8\)/);
  const iso = [["Date", "Region", "Amount"], ...Array.from({ length: 60 }, (_, i) => [i === 40 ? "2023-02-29" : `2023-01-${String((i % 28) + 1).padStart(2, "0")}`, "N", i])];
  refuses({ name: "I", outPath: target(), source: { path: bed({ "d.csv": csv(iso) }) }, scenarios: { form: "none" } }, /'2023-02-29' \(data row 41\)/);
});
check("two source files with the same name are refused, and a path in tables tells them apart", () => {
  const files = { "sales.csv": LONG, "archive/sales.csv": LONG };
  refuses({ name: "Two", outPath: target(), source: { path: bed(files) } }, /archive\/sales\.csv and sales\.csv have the same name|sales\.csv and archive\/sales\.csv have the same name/);
  refuses({ name: "Two", outPath: target(), source: { path: bed(files) }, tables: [{ source: "sales.csv", role: "skip" }] }, /'sales\.csv' matches 2 files/);
  const r = write({ name: "Two", outPath: target(), source: { path: bed(files) }, tables: [{ source: "archive/sales.csv", role: "skip" }] });
  assert.deepStrictEqual(r.skipped, [{ file: "archive/sales.csv", reason: "skipped as asked (role: skip)" }]);
  assert.ok(r.model.tables.some((t) => t.name === "sales" && t.source === "sales.csv"));
  refuses({ name: "Two", outPath: target(), source: { path: bed({ "sales.csv": LONG, "sales.json": "[{\"a\":1}]" }) } }, /have the same name/);
});
check("a JSON integer beyond 2^53 is refused, not rounded", () => {
  const body = `[{"Date":"2025-01-01","Amount":9007199254740993},{"Date":"2025-01-02","Amount":5}]`;
  refuses({ name: "J", outPath: target(), source: { path: path.join(bed({ "big.json": body }), "big.json") }, scenarios: { form: "none" } },
    /big\.json holds the integer 9007199254740993, beyond 2\^53/);
  const ok = write({ name: "J", outPath: target(), source: { path: path.join(bed({ "ok.json": `[{"Date":"2025-01-01","Amount":9007199254740991,"Id":"12345678901234567890"}]` }), "ok.json") }, scenarios: { form: "none" } });
  assert.strictEqual(ok.ok, true);
});
check("a second path between two tables is written inactive", () => {
  const out = target();
  const r = write({ name: "Amb", outPath: out, source: { path: bed({
    "A.csv": csv([["AKey", "BKey", "AName"], [1, 10, "a1"], [2, 10, "a2"], [3, 20, "a3"]]),
    "B.csv": csv([["BKey", "BName"], [10, "b10"], [20, "b20"]]),
    "fact.csv": csv([["Date", "AKey", "BKey", "Amount"], ["2025-01-01", 1, 10, 5], ["2025-01-02", 2, 10, 6], ["2025-01-03", 3, 20, 7], ["2025-01-04", 1, 10, 8]]),
  }) }, scenarios: { form: "none" } });
  assert.ok(r.model.relationships.some((x) => /fact\[BKey\] -> B\[BKey\] \(inactive\)/.test(x)), r.model.relationships.join("; "));
  assert.ok(/isActive: false/.test(fs.readFileSync(path.join(out, "Amb.SemanticModel", "definition", "relationships.tmdl"), "utf8")));
  assert.strictEqual(r.validation.wontOpen + r.validation.blank, 0, JSON.stringify(r.validation.findings));
});
check("orphan keys are reported in the assumptions", () => {
  const r = write({ name: "O", outPath: target(), source: { path: bed({
    "Fact.csv": csv([["Date", "RegionKey", "Amount"], ["2025-01-01", 1, 1], ["2025-01-02", 2, 2], ["2025-01-03", 9, 3]]),
    "Region.csv": csv([["RegionKey", "Label"], [1, "N"], [2, "S"]]),
  }) }, scenarios: { form: "none" } });
  assert.ok(r.assumptions.some((a) => /1 row\(s\) of Fact carry a RegionKey that Region does not have \(for example 9\)/.test(a)), r.assumptions.join(" | "));
});

// --- refusals that protect the disk ---------------------------------------------------------

check("a folder that is not empty is refused and left untouched", () => {
  const out = target();
  fs.mkdirSync(out);
  fs.writeFileSync(path.join(out, "keep.txt"), "mine");
  refuses({ name: "X", outPath: out, source: { path: longSrc } }, /is not empty.*never overwrites or deletes/);
  assert.deepStrictEqual(fs.readdirSync(out), ["keep.txt"]);
  assert.strictEqual(fs.readFileSync(path.join(out, "keep.txt"), "utf8"), "mine");
});
check("pages or visuals whose names differ only in case are refused before anything is written", () => {
  const out = target();
  const pos = { x: 0, y: 0, width: 100, height: 100 };
  refuses({ name: "Case", outPath: out, source: { path: longSrc }, pages: { pages: [
    { name: "p1", displayName: "One" }, { name: "P1", displayName: "Two" }] } }, /pages\/p1\/page\.json and .*pages\/P1\/page\.json differ only in case/);
  refuses({ name: "Case", outPath: out, source: { path: longSrc }, pages: { pages: [{ name: "p1", displayName: "One", visuals: [
    { name: "t1", visualType: "textbox", position: pos }, { name: "T1", visualType: "textbox", position: pos }] }] } }, /differ only in case/);
  refuses({ name: "CON", outPath: out, source: { path: longSrc } }, /'CON\.pbip' is a name Windows reserves/);
  assert.ok(!fs.existsSync(out), "a refusal wrote files");
});
check("a write that fails partway returns every file already written, says the folder is incomplete, deletes nothing", () => {
  const out = target();
  let theirs = null;
  const r = project.writeProject({ name: "Half", outPath: out, source: { path: longSrc } }, {
    validateReport: srv.validateReport, resolveProject: srv.resolveProject,
    // Another process creates the 4th file just before this call writes it.
    beforeWrite: (rel, abs, i) => { if (i === 3) { theirs = abs; fs.writeFileSync(abs, "another process"); } },
  });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.incomplete, true);
  assert.strictEqual(r.written.length, 3);
  assert.ok(/INCOMPLETE/.test(r.error) && /Nothing was deleted/.test(r.error) && /EEXIST/.test(r.error), r.error);
  for (const f of r.written) assert.strictEqual(fs.statSync(path.join(out, f.path)).size, f.bytes, f.path);
  assert.ok(r.notWritten.length > 0 && r.notWritten.every((p) => !fs.existsSync(path.join(out, p)) || path.join(out, p) === theirs));
  // The no-overwrite flag: the other process's file is exactly as it left it.
  assert.strictEqual(fs.readFileSync(theirs, "utf8"), "another process");
});
check("a file that appears in the folder before the first write stops the write; only that file is there", () => {
  const out = target();
  let err = null;
  try {
    project.writeProject({ name: "Race", outPath: out, source: { path: longSrc } }, {
      validateReport: srv.validateReport, resolveProject: srv.resolveProject,
      beforeFinalCheck: (dir) => { fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, "theirs.txt"), "x"); },
    });
  } catch (e) { err = e; }
  assert.ok(err && /is not empty/.test(err.message), `not refused: ${err && err.message}`);
  assert.deepStrictEqual(fs.readdirSync(out), ["theirs.txt"]);
});
check("an empty existing folder is accepted", () => {
  const out = target();
  fs.mkdirSync(out);
  assert.strictEqual(write({ name: "E", outPath: out, source: { path: longSrc } }).ok, true);
});
check("a second write into the same folder is refused", () => {
  refuses({ name: "Sales", outPath: out1, source: { path: longSrc } }, /is not empty/);
});
check("a relative outPath and a bad name are refused", () => {
  refuses({ name: "X", outPath: "relative/dir", source: { path: longSrc } }, /must be absolute/);
  refuses({ name: "bad name!", outPath: target(), source: { path: longSrc } }, /letters, digits, underscores or hyphens/);
  refuses({ name: "X", outPath: target(), source: { path: longSrc }, colour: "red" }, /unknown write_project field\(s\): colour/);
});
check("a path longer than 240 characters is refused before anything is written", () => {
  const out = path.join(TMP, "p".repeat(Math.max(10, 225 - TMP.length)));
  refuses({ name: "LongName", outPath: out, source: { path: longSrc }, pages: "propose" }, /characters long.*240-character limit/);
  assert.ok(!fs.existsSync(out));
});
check("over the cell ceiling is refused with the way out named", () => {
  const head = "Date,A,B,C,D,E,F,G,H,Amount\n";
  const line = "2025-01-01,a,b,c,d,e,f,g,h,1\n";
  const out = target();
  refuses({ name: "Big", outPath: out, source: { path: bed({ "big.csv": head + line.repeat(26000) }) }, scenarios: { form: "none" } },
    /above the 300000-cell ceiling.*keep-list/);
  assert.ok(!fs.existsSync(out));
});
check("a date table that is set aside does not count towards the ceiling", () => {
  const dates = ["DateKey,Date,Year,Month,A,B,C,D,E,F,G,H"];
  for (let i = 0; i < 26000; i++) {
    const d = new Date(Date.UTC(2000, 0, 1 + i)).toISOString().slice(0, 10);
    dates.push(`${d.replace(/-/g, "")},${d},${d.slice(0, 4)},${+d.slice(5, 7)},a,b,c,d,e,f,g,h`);
  }
  const r = write({ name: "DT", outPath: target(), source: { path: bed({ "DimDate.csv": `${dates.join("\n")}\n`,
    "sales.csv": csv([["Date", "Region", "Amount"], ["2025-01-01", "N", 1], ["2025-02-01", "S", 2]]) }) }, scenarios: { form: "none" } });
  assert.ok(r.assumptions.some((a) => /DimDate\.csv reads as a date table and is left out/.test(a)), r.assumptions.join(" | "));
});
check("over the 2 MB ceiling is refused even under the cell ceiling", () => {
  const long = "x".repeat(1100);
  const body = `Date,Note,Amount\n${Array.from({ length: 2000 }, (_, i) => `2025-01-01,${long}${i},${i}`).join("\n")}\n`;
  refuses({ name: "Wide", outPath: target(), source: { path: bed({ "notes.csv": body }) }, scenarios: { form: "none" } }, /bytes of TMDL, above the 2097152-byte ceiling/);
});
check("a keep-list brings a table under the ceiling, and skip leaves a file out", () => {
  const head = "Date,A,B,C,D,E,F,G,H,Amount\n";
  const line = "2025-01-01,a,b,c,d,e,f,g,h,1\n";
  const r = write({ name: "Kept", outPath: target(), source: { path: bed({ "big.csv": head + line.repeat(26000), "other.csv": "X,Y\n1,2\n" }) },
    scenarios: { form: "none" }, tables: [{ source: "big", columns: ["Date", "A", "Amount"] }, { source: "other.csv", role: "skip" }] });
  assert.deepStrictEqual(r.model.tables.map((t) => [t.name, t.rows]), [["big", 26000], ["Calendar", null]]);
  assert.deepStrictEqual(r.skipped, [{ file: "other.csv", reason: "skipped as asked (role: skip)" }]);
});

// --- a working folder with more than data in it ----------------------------------------------

const WORKDIR = {
  "sales.csv": csv([["Date", "RegionKey", "Scenario", "Amount"], ["2025-01-01", 1, "Actual", 10], ["2025-01-01", 2, "Budget", 12], ["2025-02-01", 2, "Actual", 20]]),
  "regions.csv": csv([["RegionKey", "Region Name"], [1, "North"], [2, "South"]]),
  "start.txt": "1795344658\n",
  "README.txt": "These files are the October export.\nAsk the finance team before changing them, please, because the totals are reconciled.\nNotes, drafts and logs live here too.\n",
  "settings.json": JSON.stringify({ owner: "someone", refreshed: true }),
  "notes.csv": "Item,Comment\nfirst,checked\nsecond,pending\n",
};
check("from a folder, only files that read as data become tables; the rest are listed with a reason", () => {
  const r = write({ name: "Work", outPath: target(), source: { path: bed(WORKDIR) } });
  assert.deepStrictEqual(r.model.tables.map((t) => t.name).sort(), ["Calendar", "regions", "sales"]);
  const why = Object.fromEntries(r.skipped.map((s) => [s.file, s.reason]));
  assert.deepStrictEqual(Object.keys(why).sort(), ["README.txt", "notes.csv", "settings.json", "start.txt"]);
  assert.ok(/one column and no header row/.test(why["start.txt"]), why["start.txt"]);
  assert.ok(/not readable as a table/.test(why["README.txt"]), why["README.txt"]);
  assert.ok(/joins no other file, and has no date or amount column; name it in tables to load it/.test(why["notes.csv"]), why["notes.csv"]);
  assert.ok(/not a table/.test(why["settings.json"]), why["settings.json"]);
  assert.ok(r.assumptions[0] === "Loaded as tables: regions.csv, sales.csv.", r.assumptions[0]);
  assert.ok(r.assumptions.some((a) => /^Not loaded: README\.txt, settings\.json, notes\.csv, start\.txt \(reasons under skipped\)\.$/.test(a)), r.assumptions.join(" | "));
  assert.ok(r.model.relationships.some((x) => /sales\[RegionKey\] -> regions\[RegionKey\]/.test(x)), "the joined file did not load or relate");
});
check("a file named in tables is always loaded, and a single file as source.path always loads", () => {
  const r = write({ name: "Work", outPath: target(), source: { path: bed(WORKDIR) }, tables: [{ source: "start.txt" }] });
  assert.ok(r.model.tables.some((t) => t.name === "start"), "a named file was not loaded");
  assert.ok(!r.skipped.some((s) => s.file === "start.txt"));
  const dir = bed({ "one.csv": csv([["Code", "Label"], ["a", "A"], ["b", "B"]]) });
  refuses({ name: "One", outPath: target(), source: { path: path.join(dir, "one.csv") } }, /No table reads as a fact table/);
  refuses({ name: "Work", outPath: target(), source: { path: bed(WORKDIR) }, tables: [{ source: "settings.json" }] }, /settings\.json was not read/);
});

// --- the server -----------------------------------------------------------------------------

check("the server lists write_project with explicit write annotations", () => {
  const e = srv.CALLABLE.write_project;
  assert.ok(e && typeof e.fn === "function");
  assert.deepStrictEqual(e.schema.required, ["name", "outPath", "source"]);
  assert.ok(!srv.ENCODERS.write_project && !srv.WRITERS.write_project);
});

for (const [s, name, d] of results) console.log(`  ${s}  ${name}${d ? `  <- ${d}` : ""}`);
if (process.env.ZBI_PY_TABLE) console.log(JSON.stringify(pyResults));
const failed = results.filter(([s]) => s === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} passed`);
fs.rmSync(TMP, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
