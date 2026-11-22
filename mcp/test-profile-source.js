#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";

/**
 * Tests for profile_source.
 *
 * Every signal here is a positive fixture AND a control. A profiler that reports "no fiscal year,
 * no drift, no stub, no empty scenario" on a clean source is right; one that reports the same on
 * a source that has all four is worthless and looks identical. So each check asserts the signal
 * fires on the fixture built to carry it and stays silent on the one built without it.
 *
 * The fixtures are synthetic on purpose: the shapes come from a real finance sample (July fiscal
 * year whose own column switches naming convention part-way, postings drifting around month end,
 * a 33-row year-end stub, a declared-but-empty Forecast, a budget reaching 6 of 9 entities), but
 * the test must run on CI with nothing but this directory.
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ps = require("./profile-source.js");
const srv = require("./server.js");

const results = [];
function check(name, fn) {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (e) { results.push(["FAIL", name, String(e.message).slice(0, 110)]); }
}

const pad = (n) => String(n).padStart(2, "0");
const iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;

/** A date table from Jan 2010 to Dec 2013 with a July fiscal year. `naming` = 'start' | 'end' | 'switch'. */
function dimDate(naming) {
  const lines = ["DateKey|FullDateAlternateKey|MonthNumberOfYear|CalendarYear|FiscalQuarter|FiscalYear"];
  for (let y = 2010; y <= 2013; y++) for (let m = 1; m <= 12; m++) {
    const days = new Date(y, m, 0).getDate();
    for (let d = 1; d <= days; d++) {
      const startYear = m >= 7 ? y : y - 1;           // FY starting in July
      const byStart = startYear, byEnd = startYear + 1;
      const fy = naming === "start" ? byStart : naming === "end" ? byEnd : (y <= 2010 ? byEnd : byStart);
      const fq = Math.floor(((m + 5) % 12) / 3) + 1;
      lines.push(`${y}${pad(m)}${pad(d)}|${iso(y, m, d)}|${m}|${y}|${fq}|${fy}`);
    }
  }
  return lines.join("\n") + "\n";
}

function calendarDimDate() {
  const lines = ["DateKey|FullDateAlternateKey|MonthNumberOfYear|CalendarYear|FiscalYear"];
  for (let y = 2010; y <= 2013; y++) for (let m = 1; m <= 12; m++) {
    const days = new Date(y, m, 0).getDate();
    for (let d = 1; d <= days; d++) lines.push(`${y}${pad(m)}${pad(d)}|${iso(y, m, d)}|${m}|${y}|${y}`);
  }
  return lines.join("\n") + "\n";
}

/**
 * A fact. `drift` puts postings on the 29th/1st; otherwise on the 15th. `stub` adds a 3-row
 * December 2013. Scenarios: 1 = Actual (all orgs, all months), 2 = Budget (orgs 1..6, 2011 only).
 * Forecast (3) is declared in DimScenario and never posted.
 */
function factFinance({ drift = false, stub = false, orphan = false, duplicate = false, signed = false } = {}) {
  const lines = ["FinanceKey|DateKey|OrganizationKey|ScenarioKey|AccountKey|Amount|Date"];
  let k = 1;
  const post = (y, m, d, org, sc, acct, amt) => {
    lines.push(`${k++}|${y}${pad(m)}${pad(d)}|${org}|${sc}|${acct}|${amt}|${iso(y, m, d)} 00:00:00.000`);
  };
  for (let y = 2011; y <= 2013; y++) for (let m = 1; m <= 12; m++) {
    if (y === 2013 && m === 12) continue;
    // Drift: alternate between the 29th of the previous month and the 1st of this one.
    let py = y, pm = m, pd = 15;
    if (drift) { if (m % 2) { pd = 1; } else { pm = m - 1; pd = 29; if (pm === 0) { pm = 12; py = y - 1; } } }
    for (let org = 1; org <= 9; org++) for (let acct = 1; acct <= 4; acct++) {
      const cost = acct >= 3;
      const amt = (1000 + org * 10 + acct) * (signed && cost ? -1 : 1);
      post(py, pm, pd, org, 1, acct, amt);
      if (y === 2011 && org <= 6) post(py, pm, pd, org, 2, acct, amt * 1.1);
    }
  }
  // The orphan posts on a regular posting date and the duplicate copies a mid-file row, so neither
  // adds a posting day or a period of its own -- the stub and drift assertions must see only the
  // shapes built for them.
  if (orphan) post(2012, 3, drift ? 1 : 15, 99, 1, 1, 500);
  if (duplicate) lines.push(lines[5]);
  if (stub) for (let i = 0; i < 3; i++) post(2013, 12, 28, 1, 1, 5, -12);
  return lines.join("\n") + "\n";
}

const DIM_SCENARIO = "ScenarioKey|ScenarioName\n1|Actual\n2|Budget\n3|Forecast\n";
const DIM_ORG = ["OrganizationKey|ParentOrganizationKey|PercentageOfOwnership|OrganizationName|CurrencyKey",
  "1||1|Group|100", "2|1|1|North|100", "3|1|1|South|100", "4|1|1|East|100", "5|1|1|West|100",
  "6|1|0.75|Canada|19", "7|1|0.5|France|36", "8|1|0.25|Germany|36", "9|1|1|Central|100"].join("\n") + "\n";
const DIM_ACCOUNT = ["AccountKey|ParentAccountKey|AccountDescription|Operator",
  "1||Net Income|~", "2|1|Revenue|+", "3|1|Cost of sales|-", "4|3|Materials|+", "5|3|Labour|+"].join("\n") + "\n";

function bed(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-src-"));
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(root, name), text);
  return root;
}

const FULL = bed({
  "DimDate.csv": dimDate("switch"),
  "DimScenario.csv": DIM_SCENARIO,
  "DimOrganization.csv": DIM_ORG,
  "DimAccount.csv": DIM_ACCOUNT,
  "FactFinance.csv": factFinance({ drift: true, stub: true, orphan: true, duplicate: true }),
  "notes.xlsx": "not really a workbook",
});
const CLEAN = bed({
  "DimDate.csv": calendarDimDate(),
  "DimScenario.csv": "ScenarioKey|ScenarioName\n1|Actual\n2|Budget\n",
  "DimOrganization.csv": DIM_ORG.replace(/0\.75|0\.5|0\.25/g, "1").replace(/\|19|\|36/g, "|100"),
  "DimAccount.csv": DIM_ACCOUNT,
  "FactFinance.csv": factFinance({ signed: true }),
});

const full = ps.profileSource({ path: FULL });
const clean = ps.profileSource({ path: CLEAN });

// --- reading ---------------------------------------------------------------------------------

check("both beds profile, and every file is classified", () => {
  assert.strictEqual(full.ok, true, full.error);
  assert.strictEqual(clean.ok, true, clean.error);
  assert.strictEqual(full.files.length, 5, "five text files, the workbook excluded");
  const roles = Object.fromEntries(full.files.map((f) => [f.table, f.role]));
  assert.strictEqual(roles.FactFinance, "fact");
  assert.strictEqual(roles.DimDate, "date");
  assert.strictEqual(roles.DimScenario, "scenario");
  assert.strictEqual(roles.DimOrganization, "dimension");
  assert.strictEqual(roles.DimAccount, "dimension");
});

check("the pipe delimiter is sniffed, not assumed", () => {
  for (const f of full.files) assert.strictEqual(f.delimiter, "|", `${f.file} read as ${f.delimiter}`);
  const comma = bed({ "sales.csv": "Region,Amount,Date\nNorth,10,2024-01-01\nSouth,20,2024-02-01\n" });
  const r = ps.profileSource({ path: comma });
  assert.strictEqual(r.files[0].delimiter, ",");
  const tab = bed({ "sales.tsv": "Region\tAmount\tDate\nNorth\t10\t2024-01-01\nSouth\t20\t2024-02-01\n" });
  assert.strictEqual(ps.profileSource({ path: tab }).files[0].delimiter, "tab");
});

check("a headerless file is named col1..n and flagged, never silently mis-headed", () => {
  const r = ps.profileSource({ path: bed({ "raw.csv": "1|2011-01-01|500\n2|2011-02-01|600\n3|2011-03-01|700\n" }) });
  assert.strictEqual(r.files[0].headerless, true);
  assert.deepStrictEqual(r.files[0].columns.map((c) => c.name), ["col1", "col2", "col3"]);
  assert.ok(r.notProfiled.some((n) => /no header row/.test(n)), "the header gap is not reported");
});

check("a binary workbook is reported in notProfiled, not skipped in silence", () => {
  assert.ok(full.notProfiled.some((n) => /notes\.xlsx/.test(n) && /binary/.test(n)), full.notProfiled.join(" | "));
});

check("a JSON array of records is read like a table", () => {
  const recs = [];
  for (let y = 2012; y <= 2013; y++) for (let m = 1; m <= 12; m++) recs.push({ Series: "Retail", Date: iso(y, m, 1), Amount: 100 + m }, { Series: "Wholesale", Date: iso(y, m, 1), Amount: 50 + m });
  const r = ps.profileSource({ path: bed({ "unemployment.json": JSON.stringify(recs) }) });
  assert.strictEqual(r.ok, true, r.error);
  assert.strictEqual(r.files[0].rows, 48);
  assert.strictEqual(r.files[0].columns.find((c) => c.name === "Date").type, "date");
  assert.strictEqual(r.files[0].columns.find((c) => c.name === "Series").distinct, 2);
});

check("column types: date, dateKey, integer, number, text", () => {
  const f = full.files.find((x) => x.table === "FactFinance");
  const t = Object.fromEntries(f.columns.map((c) => [c.name, c.type]));
  assert.strictEqual(t.Date, "date");
  assert.strictEqual(t.DateKey, "dateKey");
  assert.strictEqual(t.OrganizationKey, "integer");
  assert.strictEqual(t.Amount, "number");
  const d = full.files.find((x) => x.table === "DimOrganization");
  assert.strictEqual(d.columns.find((c) => c.name === "OrganizationName").type, "text");
});

// --- joins and orphans -----------------------------------------------------------------------

check("joins are found on <Dim>Key names and a bare Date column does NOT join DimDate", () => {
  const to = full.joins.map((j) => `${j.from}>${j.to}`);
  assert.ok(to.includes("FactFinance[DateKey]>DimDate[DateKey]"), to.join("\n"));
  assert.ok(to.includes("FactFinance[OrganizationKey]>DimOrganization[OrganizationKey]"));
  assert.ok(to.includes("FactFinance[ScenarioKey]>DimScenario[ScenarioKey]"));
  assert.ok(!to.some((x) => x.startsWith("FactFinance[Date]>")), "the Date column was joined to the date table's integer key");
});

check("orphan keys fire on the fixture and stay silent on the control", () => {
  const j = full.joins.find((x) => x.from === "FactFinance[OrganizationKey]");
  assert.strictEqual(j.orphanRows, 1);
  assert.deepStrictEqual(j.orphanExamples, ["99"]);
  assert.ok(full.gaps.some((g) => /ORPHAN KEYS/.test(g) && /OrganizationKey/.test(g)));
  for (const c of clean.joins) assert.strictEqual(c.orphanRows, 0, `${c.from} reported orphans on a clean bed`);
  assert.ok(!clean.gaps.some((g) => /ORPHAN/.test(g)));
});

// --- the fiscal year, the finding that motivated the tool -----------------------------------

check("a July fiscal year named by its START year is read from the date table", () => {
  const r = ps.profileSource({ path: bed({ "DimDate.csv": dimDate("start"), "FactFinance.csv": factFinance() }) });
  assert.ok(r.fiscal, "no fiscal signal on a table with a FiscalYear column");
  assert.strictEqual(r.fiscal.startMonth, 7);
  assert.strictEqual(r.fiscal.namedBy, "start");
  assert.ok(/July/.test(r.fiscal.note) && /START/.test(r.fiscal.note), r.fiscal.note);
  assert.ok(r.fiscal.companions.includes("FiscalQuarter"), "companion fiscal columns are not listed for carrying across");
  assert.ok(r.analystBrief.some((b) => /^Fiscal year: starts July, named by its start year/.test(b)), r.analystBrief.join("\n"));
});

check("...and one named by its END year is told apart", () => {
  const r = ps.profileSource({ path: bed({ "DimDate.csv": dimDate("end"), "FactFinance.csv": factFinance() }) });
  assert.strictEqual(r.fiscal.startMonth, 7);
  assert.strictEqual(r.fiscal.namedBy, "end");
  assert.ok(/FiscalYear 2011 = July 2010 to June 2011/.test(r.fiscal.example), r.fiscal.example);
});

check("a fiscal column that switches convention part-way is reported as INCONSISTENT, not averaged", () => {
  assert.strictEqual(full.fiscal.namedBy, "inconsistent", JSON.stringify(full.fiscal));
  assert.strictEqual(full.fiscal.consistent, false);
  assert.ok(full.fiscal.yearsNamedByEnd.length >= 1 && full.fiscal.yearsNamedByStart.length >= 1);
  assert.ok(/does NOT name it consistently/.test(full.fiscal.note), full.fiscal.note);
  assert.ok(full.gaps.some((g) => /FISCAL YEAR COLUMN INCONSISTENT/.test(g)));
  assert.ok(full.analystBrief.some((b) => /two different ways/.test(b)));
});

check("a FiscalYear column equal to the calendar year is reported as calendar (the control)", () => {
  assert.ok(clean.fiscal, "the control has a FiscalYear column and must still report");
  assert.strictEqual(clean.fiscal.startMonth, 1);
  assert.strictEqual(clean.fiscal.namedBy, "calendar");
  assert.ok(clean.analystBrief.some((b) => /equals the calendar year/.test(b)));
  assert.ok(!clean.gaps.some((g) => /FISCAL/.test(g)));
});

check("no fiscal column at all -> the brief says to assume calendar and to say so", () => {
  const r = ps.profileSource({ path: bed({ "sales.csv": factFinance() }) });
  assert.strictEqual(r.fiscal, null);
  assert.ok(r.analystBrief.some((b) => /no fiscal column found — assume calendar/.test(b)), r.analystBrief.join("\n"));
});

// --- periods: coverage, stub, drift, future --------------------------------------------------

check("the trailing stub is found and the last complete period named", () => {
  const p = full.periods.find((x) => x.table === "FactFinance");
  assert.ok(p, "no period signal on the fact");
  assert.strictEqual(p.trailingStubs.length, 1, JSON.stringify(p.trailingStubs));
  assert.strictEqual(p.trailingStubs[0].rows, 3);
  assert.ok(p.trailingStubs[0].period.startsWith("2013-12"));
  assert.ok(p.lastCompletePeriod && p.lastCompletePeriod.startsWith("2013-1"), p.lastCompletePeriod);
  assert.ok(full.analystBrief.some((b) => /^Period:/.test(b) && /stub/.test(b)));
});

check("no stub on the control, and the last complete period is the last period", () => {
  const p = clean.periods.find((x) => x.table === "FactFinance");
  assert.strictEqual(p.trailingStubs.length, 0);
  assert.strictEqual(p.lastCompletePeriod, "2013-11-15");
  assert.strictEqual(p.drift, null, "drift reported on 15th-of-month postings");
  assert.strictEqual(p.futureDated, null);
});

check("posting-date drift fires on 29th/1st postings, names the empty and doubled months, and refuses to guess a shift", () => {
  const p = full.periods.find((x) => x.table === "FactFinance");
  assert.ok(p.drift, "no drift on boundary postings");
  assert.deepStrictEqual(p.drift.postingDaysOfMonth, [1, 28, 29]);
  assert.ok(p.drift.calendarMonthsWithNoPosting.length >= 1, "no empty month reported under calendar bucketing");
  assert.ok(p.drift.calendarMonthsWithTwoPostings.length >= 1, "no doubled month reported under calendar bucketing");
  assert.ok(/Do not shift the dates by a guessed number of days/.test(p.drift.note));
  assert.ok(full.gaps.some((g) => /POSTING DATES DRIFT/.test(g)));
  assert.strictEqual(p.grain, "one posting per period");
});

check("future-dated rows are a gap with a count", () => {
  const y = new Date().getFullYear() + 2;
  const text = factFinance() + `99999|${y}0115|1|1|1|123|${y}-01-15 00:00:00.000\n`;
  const r = ps.profileSource({ path: bed({ "FactFinance.csv": text }) });
  const p = r.periods[0];
  assert.ok(p.futureDated && p.futureDated.count === 1, JSON.stringify(p.futureDated));
  assert.ok(r.gaps.some((g) => /FUTURE-DATED ROWS/.test(g)));
  assert.ok(!clean.gaps.some((g) => /FUTURE-DATED/.test(g)));
});

// --- scenarios -------------------------------------------------------------------------------

check("a declared-but-empty scenario is named, and the budget's reach is counted", () => {
  const s = full.scenarios;
  assert.ok(s, "no scenario signal");
  assert.deepStrictEqual(s.declared, ["Actual", "Budget", "Forecast"]);
  assert.deepStrictEqual(s.empty, ["Forecast"]);
  const budget = s.withRows.find((x) => x.name === "Budget");
  assert.ok(budget, "Budget not resolved through DimScenario");
  const org = budget.coverage.find((c) => c.column === "OrganizationKey");
  assert.ok(org && org.members === 6 && org.of === 10, JSON.stringify(budget.coverage)); // 9 orgs + the orphan 99
  assert.ok(budget.first.startsWith("2010-12") || budget.first.startsWith("2011-01"), budget.first);
  assert.ok(budget.last.startsWith("2011-1"), budget.last);
  assert.ok(full.gaps.some((g) => /Forecast is declared and has ZERO rows/.test(g)));
  assert.ok(full.gaps.some((g) => /Budget covers 6 of 10 OrganizationKey/.test(g)));
  assert.ok(full.gaps.some((g) => /A variance exists only where both have rows/.test(g)));
});

check("the control declares two scenarios, both posted: no empty scenario, no coverage note on entities", () => {
  assert.deepStrictEqual(clean.scenarios.empty, []);
  const budget = clean.scenarios.withRows.find((x) => x.name === "Budget");
  assert.strictEqual(budget.coverage.find((c) => c.column === "OrganizationKey").members, 6);
  assert.ok(!clean.gaps.some((g) => /ZERO rows/.test(g)));
});

check("two scenarios that never overlap in time are called out", () => {
  const lines = ["Period|Scenario|Amount"];
  for (let m = 1; m <= 12; m++) lines.push(`2011-${pad(m)}-01|Budget|${100 + m}`);
  for (let m = 1; m <= 12; m++) lines.push(`2013-${pad(m)}-01|Actual|${200 + m}`);
  const r = ps.profileSource({ path: bed({ "ledger.csv": lines.join("\n") + "\n" }) });
  assert.ok(r.scenarios, "a text scenario column on the fact was not recognised");
  assert.ok(r.scenarios.notes.some((n) => /never overlap in time/.test(n)), r.scenarios.notes.join("\n"));
});

// --- sign, hierarchy, stock, currency, perimeter, duplicates ---------------------------------

check("sign convention: unsigned on the fixture, signed on the control", () => {
  const u = full.signs.find((s) => s.column === "Amount");
  assert.ok(u && /^unsigned/.test(u.convention), JSON.stringify(u));
  const s = clean.signs.find((x) => x.column === "Amount");
  assert.ok(s && /^signed/.test(s.convention), JSON.stringify(s));
});

check("a parent-child dimension is found with its depth, roots and roll-up operator", () => {
  const pc = full.parentChild.find((x) => x.table === "DimAccount");
  assert.ok(pc, "DimAccount parent-child not found");
  assert.strictEqual(pc.depth, 3);
  assert.strictEqual(pc.roots, 1);
  assert.ok(pc.operatorColumn && pc.operatorColumn.name === "Operator");
  assert.ok(/PATH\(AccountKey, ParentAccountKey\)/.test(pc.note), pc.note);
  assert.ok(/subtracts from its parent/.test(pc.note));
  const org = full.parentChild.find((x) => x.table === "DimOrganization");
  assert.ok(org && org.operatorColumn === null, "an operator was invented for DimOrganization");
});

check("a stock-like column is flagged as as-at, and an amount is not", () => {
  const r = ps.profileSource({ path: bed({ "inventory.csv": "Date,Warehouse,UnitsBalance\n2024-01-31,A,100\n2024-02-29,A,120\n2024-03-31,A,90\n" }) });
  assert.ok(r.stocks.some((s) => s.column === "UnitsBalance"), JSON.stringify(r.stocks));
  assert.strictEqual(full.stocks.length, 0, JSON.stringify(full.stocks));
});

check("mixed currency with no rate table is a brief line; one currency is silence", () => {
  assert.ok(full.currency && full.currency.distinct === 3 && full.currency.rateTable === null, JSON.stringify(full.currency));
  assert.ok(full.analystBrief.some((b) => /^Currency:.*NO rate table/.test(b)));
  assert.strictEqual(clean.currency, null);
});

check("a rate table is recognised when present", () => {
  const r = ps.profileSource({ path: bed({
    "DimOrganization.csv": DIM_ORG, "FactFinance.csv": factFinance(),
    "FxRates.csv": "CurrencyKey|Month|AverageRate\n19|2011-01|0.74\n36|2011-01|1.08\n",
  }) });
  assert.ok(r.currency && r.currency.rateTable === "FxRates", JSON.stringify(r.currency));
});

check("ownership below 100% is a perimeter caveat; the control at 100% is silent", () => {
  assert.ok(full.perimeter && full.perimeter.partiallyOwned.length === 3, JSON.stringify(full.perimeter));
  assert.ok(full.analystBrief.some((b) => /^Perimeter:/.test(b)));
  assert.strictEqual(clean.perimeter, null);
});

check("an exact duplicate row is counted; the control has none", () => {
  assert.ok(full.duplicates.some((d) => d.exactDuplicateRows === 1), JSON.stringify(full.duplicates));
  assert.ok(full.gaps.some((g) => /DUPLICATES/.test(g)));
  assert.strictEqual(clean.duplicates.length, 0);
});

// --- the brief and the honesty lines --------------------------------------------------------

check("the brief names period, fiscal year and comparison, in that order, on both beds", () => {
  for (const r of [full, clean]) {
    const heads = r.analystBrief.map((b) => b.split(":")[0]);
    assert.deepStrictEqual(heads.slice(0, 3), ["Period", "Fiscal year", "Comparison"], heads.join(","));
  }
  assert.ok(full.analystBrief.some((b) => /^Comparison:.*Lead with plan where it reaches/.test(b)));
});

check("notProfiled always says what was not evaluated", () => {
  for (const r of [full, clean]) {
    assert.ok(r.notProfiled.some((n) => /Whether any number is RIGHT/.test(n)));
    assert.ok(r.notProfiled.some((n) => /Which findings matter/.test(n)));
  }
});

check("a missing path is an error, not an empty profile", () => {
  const r = ps.profileSource({ path: path.join(os.tmpdir(), "zbi-src-does-not-exist") });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(ps.profileSource({}).ok, false);
});

check("the server lists profile_source and routes it", () => {
  const tool = srv.TOOLS.find((t) => t.name === "profile_source");
  assert.ok(tool, "profile_source is not in TOOLS");
  assert.deepStrictEqual(tool.inputSchema.required, ["path"]);
  assert.ok(/FISCAL YEAR/.test(tool.description) && /LAST COMPLETE PERIOD/.test(tool.description));
  assert.strictEqual(typeof srv.profileSourceTool, "function");
  assert.strictEqual(srv.profileSourceTool({ path: CLEAN }).ok, true);
});

// One file named directly used to skip the extension filter the folder walk applies, so a
// secrets file came back with its first line as a column name. The CSV is the control.
check("a single file that is not a data file is refused, a CSV beside it is read", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-ps-single-"));
  const secret = "FAKE_KEY=not-a-real-secret";
  const files = { ".env": secret, "id_test": secret, "notes.md": secret, "history": secret };
  for (const [n, body] of Object.entries(files)) fs.writeFileSync(path.join(dir, n), body + "\n");
  fs.writeFileSync(path.join(dir, "sales.csv"), "Month,Actual\n2026-01,120\n");
  for (const n of Object.keys(files)) {
    const out = ps.profileSource({ path: path.join(dir, n) });
    assert.strictEqual(out.ok, false, `${n} was read`);
    assert.ok(!JSON.stringify(out).includes(secret), `${n} leaked its content`);
  }
  assert.strictEqual(ps.profileSource({ path: path.join(dir, "sales.csv") }).ok, true);
});

// --- wide scenarios: actual and comparison figures as separate numeric columns ---------------------
// Invented data. The reported shape was `CostCentre, Sort, Actual, Plan`, which the profiler read as
// a dimension with "no scenario column", so the brief said a variance needs a benchmark.

const wideOf = (files) => ps.profileSource({ path: bed(files) });
const WIDE_TWO = "CostCentre,Sort,Actual,Plan\nStaff,1,620,600\nTravel,2,830,510\nMarketing,3,450,490\nIT,4,500,480\n";

check("wide: Actual and Plan as two numeric columns are a comparison, not a gap", () => {
  const r = wideOf({ "costs.csv": WIDE_TWO });
  assert.strictEqual(r.ok, true, r.error);
  assert.ok(r.wideScenarios, "no wide-scenario signal");
  const roles = Object.fromEntries(r.wideScenarios.columns.map((c) => [c.column, c.scenario]));
  assert.deepStrictEqual(roles, { Actual: "actual", Plan: "plan" });
  assert.deepStrictEqual(r.wideScenarios.pairs.map((p) => [p.actual, p.against.map((a) => a.column)]), [["Actual", ["Plan"]]]);
  const cmp = r.analystBrief.find((b) => /^Comparison:/.test(b));
  assert.ok(/wide form/.test(cmp) && /Actual is AC/.test(cmp) && /Plan is PL/.test(cmp), cmp);
  assert.ok(!/no scenario column and under two years|a variance needs a benchmark/.test(cmp), cmp);
  assert.ok(!r.gaps.some((g) => /NO COMPARATIVE/.test(g)), JSON.stringify(r.gaps));
  assert.strictEqual(r.scenarios, null, "the long-form signal was reshaped");
  // Still no date column, and with no previous-year column the line keeps saying so.
  assert.ok(r.gaps.some((g) => /NO DATE COLUMN.*PY and YTD are unavailable/.test(g)), JSON.stringify(r.gaps));
});

check("wide: columns that share a measure stem pair up (Sales AC with Sales PL, Sales PY with Sales AC)", () => {
  const r = wideOf({ "p.csv": "Region,Sales AC,Sales PL,Sales PY,Units AC,Units PL\nN,100,90,80,10,12\nS,120,130,110,14,13\nE,90,95,85,9,9\n" });
  const w = r.wideScenarios;
  assert.ok(w, "no wide-scenario signal");
  const byStem = Object.fromEntries(w.pairs.map((p) => [p.measure, p]));
  assert.deepStrictEqual(Object.keys(byStem).sort(), ["sales", "units"]);
  assert.strictEqual(byStem.sales.actual, "Sales AC");
  assert.deepStrictEqual(byStem.sales.against.map((a) => [a.scenario, a.column]), [["plan", "Sales PL"], ["previousYear", "Sales PY"]]);
  assert.deepStrictEqual(byStem.units.against.map((a) => a.column), ["Units PL"], "Units was paired with Sales columns");
  const cmp = r.analystBrief.find((b) => /^Comparison:/.test(b));
  assert.ok(/Sales AC vs Sales PL and Sales PY/.test(cmp), cmp);
});

check("wide: the plain measure beside its scenario columns is the actual; a bare Forecast stays unpaired", () => {
  const r = wideOf({ "p.csv": "Product,Revenue,Revenue Budget,Forecast\nA,100,90,95\nB,50,60,55\nC,70,70,72\n" });
  const w = r.wideScenarios;
  assert.ok(w, "no wide-scenario signal");
  const rev = w.columns.find((c) => c.column === "Revenue");
  assert.ok(rev && rev.scenario === "actual" && rev.implied === true, JSON.stringify(w.columns));
  assert.ok(w.columns.some((c) => c.column === "Forecast" && c.scenario === "forecast"));
  assert.deepStrictEqual(w.pairs.map((p) => [p.actual, p.against.map((a) => a.column)]), [["Revenue", ["Revenue Budget"]]]);
});

check("wide: a previous-year column changes the no-date gap; the long form is untouched", () => {
  const r = wideOf({ "p.csv": "Region,Sales AC,Sales PY\nN,100,80\nS,120,110\n" });
  assert.ok(r.wideScenarios);
  assert.ok(r.gaps.some((g) => /NO DATE COLUMN.*YTD is unavailable and PY comes only from the previous-year column/.test(g)), JSON.stringify(r.gaps));
});

check("wide, false friends: Planet, Actuality, Budget Holder, Plan Variance and a lone Actual match nothing", () => {
  assert.strictEqual(wideOf({ "a.csv": "Name,Planet,Actuality,Count\nx,1,2,3\ny,4,5,6\n" }).wideScenarios, null);
  assert.strictEqual(wideOf({ "b.csv": "Name,Actual,Planet\nx,1,2\ny,4,5\n" }).wideScenarios, null, "Actual alone is not a comparison");
  assert.strictEqual(wideOf({ "c.csv": "Name,Actual,Plan Variance\nx,1,2\ny,4,5\n" }).wideScenarios, null, "a variance column is not the plan");
  assert.strictEqual(wideOf({ "d.csv": "Name,Actual,Budget Holder\nx,1,2\ny,4,5\n" }).wideScenarios, null);
  assert.strictEqual(wideOf({ "e.csv": "Name,Plan,Budget\nx,1,2\ny,4,5\n" }).wideScenarios, null, "two plans and no actual is not a comparison");
  const r = wideOf({ "a.csv": "Name,Planet,Actuality,Count\nx,1,2,3\ny,4,5,6\n" });
  assert.ok(r.analystBrief.some((b) => /no scenario column and under two years/.test(b)), "the no-comparison brief was lost");
});

check("wide: the long form is unchanged, and a file with both is read as the long form", () => {
  assert.strictEqual(full.wideScenarios, null);
  assert.strictEqual(clean.wideScenarios, null);
  assert.ok(full.scenarios && full.scenarios.withRows.length >= 2, "the long form regressed");
  const cmp = full.analystBrief.find((b) => /^Comparison:/.test(b));
  assert.ok(/scenarios with rows are/.test(cmp) && !/wide form/.test(cmp), cmp);
  // Long form plus numeric columns named for scenarios: the long-form brief still leads.
  const rows = [];
  for (let m = 1; m <= 12; m++) for (const sc of ["Actual", "Budget"]) rows.push(`2026-${pad(m)}-01,${sc},N,${m},${m + 1}`);
  const both = wideOf({ "f.csv": "Date,Scenario,Region,Sales,Sales Budget"+"\n"+rows.join("\n")+"\n" });
  assert.ok(both.scenarios, "long form not detected");
  assert.ok(!both.analystBrief.some((b) => /wide form/.test(b)), JSON.stringify(both.analystBrief));
});

// --- report ---------------------------------------------------------------------------------

let failed = 0;
for (const [state, name, detail] of results) {
  if (state === "FAIL") { failed++; console.log(`  FAIL  ${name}  <- ${detail}`); }
  else console.log(`  PASS  ${name}`);
}
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
