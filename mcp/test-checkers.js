#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * Per-checker tests. Each rule gets a project that should fire it and a clean project that
 * should not, because a checker only proven to fire is half tested and the half that matters
 * commercially is the other one: a rule that fires on a good report is worse than no rule.
 *
 *   node test-checkers.js
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const srv = require("./server.js");
const { lit } = require("./checkers.js");

const GUID = {
  tables: "ZebraBITablesBAE31B370F254F808553548EFB35BFA5",
  cards: "zebraBiCards2C860CFAA9944091B75F0DBD117F20FA",
  charts: "waterfall6C9ED82ABD1F44C4A0D590CE01EB5EE7",
  // Tables+ and Charts+. Tables+ extends the Tables prefix, so it was already covered; Charts+
  // does not extend the `waterfall` one, so every rule skipped it and the report passed
  // unchecked. These two fixtures are what make that coverage provable rather than assumed.
  tablesPlus: "ZebraBITablesPlus3E6085701D7B426980C3859B16327993",
  chartsPlus: "ZebraBIChartsPlusC3F2FD9F79054F76BD1B722E7666AC33",
};
const W = (s) => ({ expr: { Literal: { Value: `'${s}'` } } });
const N = (n) => ({ expr: { Literal: { Value: `${n}D` } } });
const SCHEMA =
  "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json";
const PBISM_SCHEMA =
  "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json";
const REPORT_SCHEMA = (v) =>
  `https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/${v}/schema.json`;
// report.json exactly as a project that Desktop opened has it: schema 3.0.0,
// reportVersionAtImport an object, type a word, no layoutOptimization. The negative control for the
// report.json shape rules; each test below changes one thing in it.
const OPENS_REPORT_JSON = () => ({
  $schema: REPORT_SCHEMA("3.0.0"),
  themeCollection: { baseTheme: { name: "CY24SU08",
    reportVersionAtImport: { visual: "2.11.0", page: "2.1.0", report: "3.0.0" }, type: "SharedResources" } },
  publicCustomVisuals: ["ZebraBITablesPlus3E6085701D7B426980C3859B16327993"],
  settings: { useStylableVisualContainerHeader: true, useEnhancedTooltips: true },
});

/**
 * Build a project. `mutate` receives the writable pieces so a test can introduce exactly one
 * fault; everything it does not touch stays valid, which is what makes a silent-run meaningful.
 */
// Every fixture directory is removed when the process exits. Without this the suite left one
// `zbi-c-*` directory per fixture per run: 5,394 of them (5.2 MB) had accumulated on one machine
// by 2026-08-15, and a CI runner accumulates the same way. Removal happens at exit rather than per
// fixture because a failing assertion is easier to inspect while its project is still on disk.
const FIXTURE_ROOTS = [];
process.on("exit", () => {
  if (process.env.ZBI_KEEP_FIXTURES) return;
  for (const r of FIXTURE_ROOTS) { try { fs.rmSync(r, { recursive: true, force: true }); } catch {} }
});

function project(mutate = () => {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-c-"));
  FIXTURE_ROOTS.push(root);
  const report = path.join(root, "Demo.Report");
  const def = path.join(report, "definition");
  const pageDir = path.join(def, "pages", "P1");
  fs.mkdirSync(pageDir, { recursive: true });

  const state = {
    pbip: { $schema: SCHEMA, version: "1.0" },
    // definition.pbism: a project Desktop opens carries 4.2 beside a TMDL definition/ folder.
    pbism: { $schema: PBISM_SCHEMA, version: "4.2", settings: {} },
    // definition.pbir as a project that opens has it: the PBIR $schema and version 4.0.
    pbir: { $schema: "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            version: "4.0", datasetReference: { byPath: { path: "../Demo.SemanticModel" } } },
    // A report that opens registers every Zebra BI visual type its pages use.
    reportJson: { $schema: "https://x", themeCollection: { baseTheme: { name: "CY24SU10" } },
                  publicCustomVisuals: Object.values(GUID) },
    versionJson: { $schema: "https://x/versionMetadata/1.0.0/schema.json", version: "2.0.0" },
    pagesJson: { pageOrder: ["P1"], activePageName: "P1" },
    page: { name: "P1", visualInteractions: [] },
    visuals: {
      V1: { name: "V1", position: { x: 0, y: 0, width: 600, height: 400 },
            visual: { visualType: GUID.tables, objects: {}, query: { queryState: {} } } },
    },
    bom: false,
  };
  mutate(state, GUID);

  // A realistic clean project has a model: scenario measures and a marked calendar. Without
  // one, "clean" would mean "we could not read the model", which is a different thing.
  const md = path.join(root, "Demo.SemanticModel", "definition");
  fs.mkdirSync(md, { recursive: true });
  fs.writeFileSync(path.join(md, "model.tmdl"), [
    "table Calendar", "\tdataCategory: Time", "", "\tcolumn Date", "\t\tisKey", "",
    "table Financials",
    "\tmeasure 'Value AC' = 1", "\t\tformatString: #,0",
    "\tmeasure 'Value PL' = 1", "\t\tformatString: #,0", "",
  ].join("\n"));
  const w = (p, o) => fs.writeFileSync(p, (state.bom ? "﻿" : "") + JSON.stringify(o, null, 2));
  fs.writeFileSync(path.join(root, "Demo.pbip"), JSON.stringify(state.pbip));
  if (state.pbism !== null) fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition.pbism"), JSON.stringify(state.pbism));
  w(path.join(def, "definition.pbir"), state.pbir);
  w(path.join(def, "report.json"), state.reportJson);
  // definition/version.json is REQUIRED by Desktop's packaging layer -- a project without it will
  // not open at all. The fixture lacked it, so "a clean project" described one that cannot be
  // opened; added 2026-08-15 with the rule that catches its absence.
  if (state.versionJson !== null) w(path.join(def, "version.json"), state.versionJson);
  w(path.join(def, "pages", "pages.json"), state.pagesJson);
  w(path.join(pageDir, "page.json"), state.page);
  for (const [name, v] of Object.entries(state.visuals)) {
    const vd = path.join(pageDir, "visuals", name);
    fs.mkdirSync(vd, { recursive: true });
    w(path.join(vd, "visual.json"), v);
  }
  return root;
}

const results = [];
const codes = (root) => new Set(srv.validateReport({ projectPath: root }).findings.map((f) => f.code));

/**
 * A free-form assertion, for the few things that are not "this rule fires on that mutation":
 * the shape of an assembled finding, and the literal decoder every rule reads through. Same
 * results table and same report line as `rule` below, so a failure here fails the suite.
 */
function check(name, fn) {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (e) { results.push(["FAIL", name, String(e.message).split("\n")[0].slice(0, 92)]); }
}

/** Assert the rule fires on `broken` and does NOT fire on an otherwise-identical clean project. */
function rule(code, name, mutate) {
  try {
    assert.ok(codes(project(mutate)).has(code), `did not fire: ${code}`);
    assert.ok(!codes(project()).has(code), `FIRES ON A CLEAN REPORT: ${code}`);
    results.push(["PASS", name, ""]);
  } catch (e) {
    results.push(["FAIL", name, String(e.message).slice(0, 80)]);
  }
}

// --- will not open -------------------------------------------------------------------------

rule("pbip-schema-wrong", "a guessed .pbip $schema", (s) => {
  s.pbip.$schema = "https://developer.microsoft.com/json-schemas/fabric/pbip/schema.json";
});

// --- project-file shape -----------------------------------------------------------------------------
// Each rule below fires on a project shape Power BI Desktop refuses. The controls are the shapes of
// projects Desktop opens.

// definition.pbism: "1.0" beside a TMDL definition/ folder is "Missing required artifact 'model.bim'".
rule("pbism-version-too-old-for-tmdl", "definition.pbism at 1.0 beside a TMDL folder", (s) => { s.pbism.version = "1.0"; });

check("pbism 4.0 and 4.2 are NOT flagged; neither is a missing or version-less pbism", () => {
  for (const v of ["4.0", "4.2", "5.0"]) {
    const root = project((s) => { s.pbism.version = v; });
    assert.ok(!codes(root).has("pbism-version-too-old-for-tmdl"), `fired on ${v}`);
  }
  assert.ok(!codes(project((s) => { s.pbism = null; })).has("pbism-version-too-old-for-tmdl"), "fired on no pbism");
  assert.ok(!codes(project((s) => { delete s.pbism.version; })).has("pbism-version-too-old-for-tmdl"),
    "fired on a pbism with no version");
});

check("a pbism at 1.0 with NO TMDL folder (a model.bim model) is NOT flagged", () => {
  const root = project((s) => { s.pbism.version = "1.0"; });
  fs.rmSync(path.join(root, "Demo.SemanticModel", "definition"), { recursive: true, force: true });
  assert.ok(!codes(root).has("pbism-version-too-old-for-tmdl"), "fired on a model.bim project");
});

// .pbip artifacts: only { report } is allowed.
rule("pbip-artifact-not-a-report", "a model entry in .pbip artifacts", (s) => {
  s.pbip.artifacts = [{ report: { path: "Demo.Report" } }, { model: { path: "Demo.SemanticModel" } }];
});

check("a .pbip with only a report artifact (or none) is NOT flagged; semanticModel is", () => {
  assert.ok(!codes(project((s) => { s.pbip.artifacts = [{ report: { path: "Demo.Report" } }]; }))
    .has("pbip-artifact-not-a-report"), "fired on the legal form");
  assert.ok(!codes(project()).has("pbip-artifact-not-a-report"), "fired with no artifacts key");
  assert.ok(codes(project((s) => { s.pbip.artifacts = [{ semanticModel: { path: "Demo.SemanticModel" } }]; }))
    .has("pbip-artifact-not-a-report"), "missed a semanticModel entry");
});

// report.json shapes, judged by the schema the file declares.
const reportCodes = (mutate) => codes(project((s) => { s.reportJson = OPENS_REPORT_JSON(); mutate(s.reportJson); }));

check("the report.json of a project that opens produces none of the report.json shape findings", () => {
  const c = reportCodes(() => {});
  for (const id of ["report-theme-version-has-wrong-type", "report-type-is-a-number", "report-layout-optimization-invalid"]) {
    assert.ok(!c.has(id), `FIRES ON A REPORT THAT OPENS: ${id}`);
  }
});

check("reportVersionAtImport as a string under report schema 3.x is caught; an object under 2.0.0 is too", () => {
  assert.ok(reportCodes((r) => { r.themeCollection.baseTheme.reportVersionAtImport = "5.59"; })
    .has("report-theme-version-has-wrong-type"), "missed the string under 3.0.0");
  assert.ok(reportCodes((r) => { r.$schema = REPORT_SCHEMA("3.2.0"); r.themeCollection.baseTheme.reportVersionAtImport = "5.59"; })
    .has("report-theme-version-has-wrong-type"), "missed the string under 3.2.0");
  assert.ok(reportCodes((r) => { delete r.themeCollection.baseTheme.reportVersionAtImport; })
    .has("report-theme-version-has-wrong-type"), "missed an absent required version");
  assert.ok(reportCodes((r) => { delete r.themeCollection.baseTheme.reportVersionAtImport.page; })
    .has("report-theme-version-has-wrong-type"), "missed an object without page");
  assert.ok(reportCodes((r) => { r.$schema = REPORT_SCHEMA("2.0.0"); })
    .has("report-theme-version-has-wrong-type"), "missed an object where 2.0.0 wants a string");
});

check("a string version under report schema 1.0.0 or 2.0.0, or under no readable schema, is NOT flagged", () => {
  for (const sch of ["1.0.0", "2.0.0"]) {
    assert.ok(!reportCodes((r) => { r.$schema = REPORT_SCHEMA(sch); r.themeCollection.baseTheme.reportVersionAtImport = "5.58"; })
      .has("report-theme-version-has-wrong-type"), `fired on a string under ${sch}`);
  }
  assert.ok(!reportCodes((r) => { delete r.$schema; r.themeCollection.baseTheme.reportVersionAtImport = "5.59"; })
    .has("report-theme-version-has-wrong-type"), "judged a report that declares no schema");
});

check("a numeric theme or resource type is caught; words and absent types are NOT", () => {
  assert.ok(reportCodes((r) => { r.themeCollection.baseTheme.type = 2; }).has("report-type-is-a-number"), "missed type 2");
  assert.ok(reportCodes((r) => { r.themeCollection.customTheme = { name: "T", reportVersionAtImport:
    { visual: "2.0.0", page: "2.0.0", report: "3.0.0" }, type: 1 }; }).has("report-type-is-a-number"), "missed customTheme type 1");
  assert.ok(reportCodes((r) => { r.resourcePackages = [{ name: "RegisteredResources", type: 1, items: [] }]; })
    .has("report-type-is-a-number"), "missed a numeric package type");
  assert.ok(reportCodes((r) => { r.resourcePackages = [{ name: "SharedResources", type: "SharedResources",
    items: [{ name: "a.json", path: "BaseThemes/a.json", type: 202 }] }]; }).has("report-type-is-a-number"), "missed a numeric item type");
  assert.ok(!reportCodes((r) => {
    r.resourcePackages = [{ name: "SharedResources", type: "SharedResources",
      items: [{ name: "a.json", path: "BaseThemes/a.json", type: "BaseTheme" }] }];
    r.themeCollection.customTheme = { name: "T", reportVersionAtImport: { visual: "2.0.0", page: "2.0.0", report: "3.0.0" },
      type: "RegisteredResources" };
  }).has("report-type-is-a-number"), "fired on word types");
  assert.ok(!reportCodes((r) => { delete r.themeCollection.baseTheme.type; }).has("report-type-is-a-number"),
    "fired on an absent type");
});

check("an absent type on a resource package or item is NOT flagged", () => {
  assert.ok(!reportCodes((r) => { r.resourcePackages = [{ name: "SharedResources", items: [] }]; })
    .has("report-type-is-a-number"), "fired on a package with no type");
  assert.ok(!reportCodes((r) => { r.resourcePackages = [{ name: "SharedResources", type: "SharedResources",
    items: [{ name: "a.json", path: "BaseThemes/a.json" }] }]; }).has("report-type-is-a-number"), "fired on an item with no type");
});

check("layoutOptimization: a number is caught in every schema; the 1.0.0 string and an absent key are NOT", () => {
  assert.ok(reportCodes((r) => { r.layoutOptimization = 0; }).has("report-layout-optimization-invalid"), "missed 0 under 3.0.0");
  assert.ok(reportCodes((r) => { r.$schema = REPORT_SCHEMA("1.0.0"); r.layoutOptimization = 0; })
    .has("report-layout-optimization-invalid"), "missed 0 under 1.0.0");
  assert.ok(reportCodes((r) => { delete r.$schema; r.layoutOptimization = 0; })
    .has("report-layout-optimization-invalid"), "missed 0 with no schema");
  assert.ok(reportCodes((r) => { r.layoutOptimization = "None"; }).has("report-layout-optimization-invalid"),
    "missed a property that report schema 3.0.0 does not declare");
  for (const v of ["None", "PhonePortrait"]) {
    assert.ok(!reportCodes((r) => { r.$schema = REPORT_SCHEMA("1.0.0"); r.layoutOptimization = v; })
      .has("report-layout-optimization-invalid"), `fired on the legal ${v} under 1.0.0`);
  }
  assert.ok(!reportCodes((r) => { r.$schema = REPORT_SCHEMA("1.0.0"); }).has("report-layout-optimization-invalid"),
    "fired on an absent layoutOptimization under 1.0.0");
});

// The shape rules know report schema majors 1, 2 and 3. A later major may change either shape, so a
// report that declares one is left alone by both rules.
check("a report.json that declares an unknown schema major (4.0.0) is NOT judged by the shape rules", () => {
  const v4 = (mutate) => reportCodes((r) => { r.$schema = REPORT_SCHEMA("4.0.0"); mutate(r); });
  assert.ok(!v4((r) => { r.themeCollection.baseTheme.reportVersionAtImport = "5.59"; })
    .has("report-theme-version-has-wrong-type"), "judged a string version under 4.0.0");
  assert.ok(!v4(() => {}).has("report-theme-version-has-wrong-type"), "judged the object version under 4.0.0");
  for (const v of [0, "None"]) {
    assert.ok(!v4((r) => { r.layoutOptimization = v; }).has("report-layout-optimization-invalid"),
      `judged layoutOptimization ${JSON.stringify(v)} under 4.0.0`);
  }
});

rule("file-starts-with-bom", "a BOM in a report file", (s) => { s.bom = true; });

rule("interaction-target-missing", "an interaction naming a visual not on disk", (s) => {
  s.page.visualInteractions = [{ source: "V1", target: "GhostVisual", type: "NoFilter" }];
});

rule("interaction-target-missing", "a declared page with no page.json", (s) => {
  s.pagesJson.pageOrder = ["P1", "P2"];
});

rule("visual-object-not-an-array", "a bare object under visual.objects", (s) => {
  s.visuals.V1.visual.objects.titleSettings = { properties: { text: W("Hi") } };
});

// --- renders blank -------------------------------------------------------------------------

rule("dataset-path-does-not-resolve", "a dataset path pointing nowhere", (s) => {
  s.pbir.datasetReference.byPath.path = "../Renamed.SemanticModel";
});

rule("report-has-no-base-theme", "no base theme", (s) => { delete s.reportJson.themeCollection; });

// Desktop writes only `customTheme` on Save As PBIP, and that report opens and renders (field
// report 2026-10-23, two cold opens). A custom theme without a base theme must stay silent; an
// empty themeCollection must still fire.
rule("report-has-no-base-theme", "an empty themeCollection", (s) => { s.reportJson.themeCollection = {}; });

check("a customTheme with no baseTheme is NOT flagged", () => {
  const root = project((s) => {
    s.reportJson.themeCollection = { customTheme: { name: "Custom", type: "RegisteredResources" } };
  });
  assert.ok(!codes(root).has("report-has-no-base-theme"), "fired on a report with only a customTheme");
});

check("a customTheme with no name is flagged, at the customTheme", () => {
  for (const custom of [{}, { type: "RegisteredResources" }, { name: "" }, true]) {
    const root = project((s) => { s.reportJson.themeCollection = { customTheme: custom }; });
    const f = srv.validateReport({ projectPath: root }).findings
      .find((x) => x.code === "report-has-no-base-theme");
    assert.ok(f, `did not fire on customTheme ${JSON.stringify(custom)}`);
    assert.ok(/themeCollection\.customTheme\.name/.test(f.location), `location: ${f.location}`);
  }
});

// --- looks fine, misleads -------------------------------------------------------------------

// The clean project is a Zebra Table, so the negative control is a Zebra page with no native
// visual; the positive swaps the type for a native pie. Both directions in one call, as always.
rule("native-anti-pattern-visual", "a native pie chart where a bar table belongs", (s) => {
  s.visuals.V1.visual.visualType = "pieChart";
});

// View-mode comments. The storage link on a CERTIFIED visual is declared and ignored; the clean
// project is a certified Table with no link, and the Tables+ and Charts+ silence fixtures at the end prove the
// same link on Tables+ stays quiet (the rule reads the GUID, not the property).
const STORAGE_LINK = W(JSON.stringify({ storageBackend: "sharePointExcel", storageOptions: {
  siteId: "11111111-1111-1111-1111-111111111111", folderPath: "/drive/root:", fullFileName: "c.xlsx" } }));
rule("view-mode-storage-on-certified-visual", "a storage link on certified Tables", (s) => {
  s.visuals.V1.visual.objects.annotationLayerSettings =
    [{ properties: { annotationsStorageConfig: STORAGE_LINK } }];
});
rule("view-mode-storage-with-comment-box-off", "a storage link with the panel switched off", (s, G) => {
  s.visuals.V1.visual.visualType = G.tablesPlus;
  s.reportJson.publicCustomVisuals = [G.tablesPlus];
  s.visuals.V1.visual.objects.annotationLayerSettings =
    [{ properties: { annotationsStorageConfig: STORAGE_LINK } }];
  s.visuals.V1.visual.objects.commentBoxSettings =
    [{ properties: { show: { expr: { Literal: { Value: "false" } } } } }];
});
// The silence half of the second rule, constructed rather than assumed: a link with NO
// commentBoxSettings at all is the corpus norm for comments and must not fire.
try {
  const out = srv.validateReport({ projectPath: project((s, G) => {
    s.visuals.V1.visual.visualType = G.tablesPlus;
    s.reportJson.publicCustomVisuals = [G.tablesPlus];
    s.visuals.V1.visual.objects.annotationLayerSettings =
      [{ properties: { annotationsStorageConfig: STORAGE_LINK } }];
  }) });
  const hit = out.findings.filter((f) => /view-mode-storage/.test(f.code)).map((f) => f.code);
  assert.deepStrictEqual(hit, [], `a Tables+ storage link with no comment box fired: ${hit}`);
  results.push(["PASS", "a Tables+ storage link with commentBoxSettings absent stays silent", ""]);
} catch (e) {
  results.push(["FAIL", "a Tables+ storage link with commentBoxSettings absent stays silent",
    String(e.message).slice(0, 80)]);
}

// A POSITIVE fixture, not just the clean-project negative control. A rule that never fires scores
// zero false positives too, so the absence has to be constructed deliberately. Reproduced against
// Desktop 2.156.951.0: deleting this file from a project that had just opened cleanly makes the
// next open fail with "Cannot find file 'version.json'".
rule("report-missing-version-json", "definition/version.json deleted",
     (s) => { s.versionJson = null; });

// `multiline-measure-dax-on-equals-line` is a TMDL rule and its fixtures live in test-tmdl.js,
// which is the suite whose project() helper can write a fact table.

// TWO measures on Cards, not four: the declared cap is one, and the rule this replaced let a
// two-measure Cards through while the visual refused to render it.
rule("data-role-exceeds-declared-maximum", "two measures in a Cards Values well", (s, G) => {
  s.visuals.V1.visual.visualType = G.cards;
  s.visuals.V1.visual.query.queryState.Values = {
    projections: [1, 2].map((i) => ({ field: {}, queryRef: `m${i}` })),
  };
});

rule("data-role-exceeds-declared-maximum", "three Charts Values projections", (s, G) => {
  s.visuals.V1.visual.visualType = G.charts;
  s.visuals.V1.visual.query.queryState.Values = {
    projections: [1, 2, 3].map((i) => ({ field: {}, queryRef: `m${i}` })),
  };
});

// `detail` is per-hit and used to be dropped when the finding was assembled, so the customer got
// the generic message with none of the numbers the checker had already worked out. Both setters
// lose their point without it: this one says WHICH cap and by how much.
check("a finding carries the per-hit detail the checker computed", () => {
  const out = srv.validateReport({ projectPath: project((s2, G) => {
    s2.visuals.V1.visual.visualType = G.cards;
    s2.visuals.V1.visual.query.queryState.Values = {
      projections: [1, 2].map((i) => ({ field: {}, queryRef: `m${i}` })),
    };
  }) });
  const hit = out.findings.find((f) => f.code === "data-role-exceeds-declared-maximum");
  assert.ok(hit, "the role-maximum rule did not fire, so this test proves nothing");
  assert.ok(hit.detail, "finding carries no detail, so the numbers never reach the reader");
  assert.match(hit.detail, /of max/, `detail was "${hit.detail}"`);
});

// `lit()` finished with an unconditional `.replace(/[DLM]$/, "")`. That suffix is PBIR's NUMERIC
// literal type marker, so on a string it silently ate the last character: 'TrendM' -> "Trend",
// '#1A1A2D' -> "#1A1A2". Every rule comparing a name or a colour was reading corrupted input.
check("lit() keeps a trailing D, L or M that belongs to a string", () => {
  const of = (v) => lit({ expr: { Literal: { Value: v } } });
  assert.strictEqual(of("'TrendM'"), "TrendM");
  assert.strictEqual(of("'#1A1A2D'"), "#1A1A2D");
  assert.strictEqual(of("'EMEA-L'"), "EMEA-L");
});

check("lit() still strips the type suffix from a NUMERIC literal", () => {
  const of = (v) => lit({ expr: { Literal: { Value: v } } });
  assert.strictEqual(of("1234D"), "1234");
  assert.strictEqual(of("-12.5D"), "-12.5");
  assert.strictEqual(of("42L"), "42");
});

// Tables+ and Charts+ must be checked like their certified twins. Before Charts+ got its own
// prefix this fixture passed silently -- not because the report was good, but because the
// checker did not recognise the visual as Zebra at all.
rule("data-role-exceeds-declared-maximum", "three Charts+ Values projections", (s, G) => {
  s.visuals.V1.visual.visualType = G.chartsPlus;
  s.visuals.V1.visual.query.queryState.Values = {
    projections: [1, 2, 3].map((i) => ({ field: {}, queryRef: `m${i}` })),
  };
});

rule("data-role-exceeds-declared-maximum", "five Tables+ Plan projections", (s, G) => {
  s.visuals.V1.visual.visualType = G.tablesPlus;
  s.visuals.V1.visual.query.queryState.Plan = {
    projections: [1, 2, 3, 4, 5].map((i) => ({ field: {}, queryRef: `m${i}` })),
  };
});

rule("comment-filtercontexts-key", "a comment with no filterContexts key", (s) => {
  s.visuals.V1.visual.objects.annotationLayerSettings = [{ properties: {
    annotationComments: W(JSON.stringify([{ uuid: "a", title: "x - null" }])),
  } }];
});

// --- numbers wrong -------------------------------------------------------------------------

rule("computed-row-in-grand-total", "a computed row not excluded from the total", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: { showGrandTotal: W("true") } }];
  s.visuals.V1.visual.objects.categoriesMetadata = [{ properties: {
    addedFormulas: W(JSON.stringify([{ identity: "Gross margin %", expression: "[GP]/[Rev]" }])),
  } }];
});

// --- looks fine and is wrong ---------------------------------------------------------------

rule("colour-gate", "colours set without style Custom", (s) => {
  s.visuals.V1.visual.objects.designSettings = [{ properties: {
    positiveColor: { solid: { color: W("#00A")} }, negativeColor: { solid: { color: W("#A00")} },
  } }];
});

// RESTORED 2026-08-05 after being wrongly re-based to 20px the same day. This fixture is not
// arbitrary: the held catalogue records that a 22pt title in a 40px box makes Power BI draw its own native
// scrollbar inside the box and clip the text, that it shipped, and that a human found it in a
// screenshot. 40px is the render-proven case. Do not lower it.
rule("textbox-too-short-for-font", "a 22pt title in a 40px box", (s) => {
  s.visuals.T1 = {
    name: "T1", position: { x: 0, y: 0, width: 400, height: 40 },
    visual: { visualType: "textbox",
      objects: { general: [{ properties: { fontSize: "22" } }] } },
  };
});

// Measured 2026-09-06: the floor is ceil(pt * 1.45 + 12), so 12pt needs 30. 28 clipped on screen
// and 30 did not, at 2px granularity, which is why this fixture sits on the pair either side.
rule("textbox-too-short-for-font", "a 12pt caption in a 28px box (30 is the measured floor)", (s) => {
  s.visuals.T2 = {
    name: "T2", position: { x: 0, y: 0, width: 400, height: 28 },
    visual: { visualType: "textbox",
      objects: { general: [{ properties: { fontSize: "12" } }] } },
  };
});

// A slicer's header shows unless it is explicitly turned off, and it costs ~22px. 46 renders with
// the dropdown cut off at the bottom; 52 is the rendered floor. The fixture leaves `header` absent
// on purpose -- that is the state a slicer is in unless someone thought about it.
rule("slicer-height-clips-the-dropdown", "a 46px dropdown slicer with the header left on", (s) => {
  s.visuals.S1 = {
    name: "S1", position: { x: 0, y: 0, width: 190, height: 46 },
    visual: { visualType: "slicer", objects: { data: [{ properties: { mode: W("Dropdown") } }] } },
  };
});

rule("table-row-height-below-legible", "a Fixed row height of 12px", (s) => {
  s.visuals.V1.visual.objects.categorySettings = [{ properties: { rowHeight: N(5), height: N(12) } }];
});

rule("title-token-broken-after-save", "a measure token plus Desktop's save bookkeeping", (s) => {
  s.visuals.V1.visual.objects.titleSettings = [{ properties: { text: W("[Selected Year]") } }];
  s.visuals.V1.visual.objects.version = [{ properties: {} }];
  s.visuals.V1.visual.objects.previewSettings = [{ properties: {} }];
});

rule("table-ignores-sort-definition", "a Table given the standard sort definition", (s) => {
  s.visuals.V1.visual.query.sortDefinition = { sort: [{ field: {}, direction: "Ascending" }] };
});

// The three silent-run tests below are the vendor-template shapes from the 5 August corpus run.
// Each one fired on a shipped reference template, which makes it a product defect and not a tuning
// question. They are written as bare silent-runs rather than `rule(...)` because what is being
// asserted is the absence of a finding on a shape that is correct.
const silent = (code, name, mutate) => {
  try {
    assert.ok(!codes(project(mutate)).has(code), `FIRES ON A SHIPPED TEMPLATE SHAPE: ${code}`);
    results.push(["PASS", name, ""]);
  } catch (e) {
    results.push(["FAIL", name, String(e.message).slice(0, 80)]);
  }
};

// The other half of the textbox pair, and the reason the floor was re-measured rather than
// re-derived: a 12pt caption at exactly the measured floor renders clean, so a rule that fires here
// is over-demanding. The previous derived floor asked for 36.8px at 12pt and did exactly that.
silent("textbox-too-short-for-font", "a 12pt caption at exactly 30px, the measured floor", (s) => {
  s.visuals.T3 = {
    name: "T3", position: { x: 0, y: 0, width: 400, height: 30 },
    visual: { visualType: "textbox",
      objects: { general: [{ properties: { fontSize: "12" } }] } },
  };
});

// Header off is the idiomatic short slicer: 270 of the vendor's 408 are written this way and 44-48
// is correct for them. A rule that cannot tell the two apart flags three quarters of the corpus,
// which is why an unconditional slicer-height rule was rejected twice before this one.
silent("slicer-height-clips-the-dropdown", "a 46px dropdown slicer with the header OFF", (s) => {
  s.visuals.S2 = {
    name: "S2", position: { x: 0, y: 0, width: 190, height: 46 },
    visual: { visualType: "slicer",
      objects: { data: [{ properties: { mode: W("Dropdown") } }],
                 header: [{ properties: { show: { expr: { Literal: { Value: "false" } } } } }] } },
  };
});

// `categorySettings.height` is read by rowHeight 5 (Fixed) alone. Written beside any other value it
// is inert, not illegible -- and one shipped vendor template does exactly that.
silent("table-row-height-below-legible", "a 12px height beside rowHeight 0, where it is inert", (s) => {
  s.visuals.V1.visual.objects.categorySettings = [{ properties: { rowHeight: N(0), height: N(12) } }];
});

// An actionButton's label is state-scoped. The positive is the exact authoring mistake: the label
// written into the selector-less entry, which Desktop drops in silence. The three silent runs are
// the shapes shipped templates actually use -- label under `default`, label under `hover` ONLY
// (which real templates do), and an icon button carrying no label at all.
const BTN = (textEntries) => (s) => {
  s.visuals.V1.visual.visualType = "actionButton";
  s.visuals.V1.visual.objects = { text: textEntries };
};

rule("action-button-label-needs-state-selector", "a button label in a selector-less entry",
  BTN([{ properties: { show: W("true"), text: W("Drill through to detail") } }]));

silent("action-button-label-needs-state-selector", "a button label under selector default",
  BTN([{ properties: { show: W("true") } },
       { properties: { text: W("Drill through to detail") }, selector: { id: "default" } }]));

silent("action-button-label-needs-state-selector", "a button whose label lives ONLY under hover",
  BTN([{ properties: { show: W("true") } },
       { properties: { text: W("Explore trends") }, selector: { id: "hover" } }]));

silent("action-button-label-needs-state-selector", "an icon button with no label at all",
  BTN([{ properties: { show: W("true") } }]));


// Power BI writes the sort it chose itself and marks it. Nobody asked for an order, so the rule's
// "rather than the order you asked for" describes no fault. 82 of 82 vendor tables carry this.
silent("table-ignores-sort-definition", "a Table whose sort is Power BI's own default", (s) => {
  s.visuals.V1.visual.query.sortDefinition = {
    sort: [{ field: { Measure: { Expression: { SourceRef: { Entity: "Key Measures" } },
                                 Property: "AC" } }, direction: "Descending" }],
    isDefaultSort: true,
  };
});

// A Tables rule must not read a Cards visual. This one carried severity `blank`, so it told the
// customer a table renders empty -- on a Cards visual, in a reference template.
silent("table-sort-field-not-shown", "a CARDS visual sorted by an unprojected measure", (s, G) => {
  s.visuals.V1.visual.visualType = G.cards;
  s.visuals.V1.visual.query.queryState.Values = { projections: [
    { field: {}, queryRef: "Main Measures.Before AC", nativeQueryRef: "Before AC" },
  ] };
  s.visuals.V1.visual.query.sortDefinition = { sort: [
    { field: { Measure: { Expression: { SourceRef: { Entity: "Main Measures" } },
                          Property: "Before due %" } }, direction: "Descending" } ] };
});

// `skipCalculationCategories` excludes a category from the visual's CALCULATIONS, not from the
// render. With no total the visual computes nothing for an unskipped row to corrupt. This is the
// income-statement shape from three pages of `Financial statements`, whose vendor render shows no
// total row: two ratio rows unskipped, one skipped, no showGrandTotal.
silent("computed-row-in-grand-total", "computed rows in a table with no total", (s) => {
  s.visuals.V1.visual.objects.categoriesMetadata = [{ properties: {
    addedFormulas: W(JSON.stringify([
      { identity: "Gross margin %", expression: "[Gross profit] / [Revenue]", percent: true },
      { identity: "Operating income %", expression: "[Operating income] / [Revenue]", percent: true },
      { identity: "Net income %", expression: "[Net income] / [Revenue]", percent: true },
    ])),
    skipCalculationCategories: W(JSON.stringify([
      { hierarchyIdentity: "Accounts.Account group", category: "Gross margin %", level: 0 },
    ])),
  } }];
});

// `showTotals` is not a substitute for `showGrandTotal`. Zebra BI - Working capital carries
// showTotals: 1 and renders no total row and no total column.
silent("computed-row-in-grand-total", "showTotals alone does not mean a total is computed", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: { showTotals: N(1) } }];
  s.visuals.V1.visual.objects.categoriesMetadata = [{ properties: {
    addedFormulas: W(JSON.stringify([{ identity: "Net Change", expression: "[CF ops] + [CF inv]" }])),
  } }];
});

// `chartLayout: Waterfall` is the ONE value of nine that renders differently, per correction 3,
// which rendered and hashed all nine. So it must not be reported as inert.
silent("property-declared-but-inert", "chartLayout set to Waterfall, the one value that works", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: { chartLayout: W("Waterfall") } }];
});

// The date-hierarchy silent test that sat here is gone with the rule it guarded --
// `rename-needs-displayname` was retired, not narrowed a third time. Its replacement is the
// tombstone further down, which drives all three vendor shapes at once.

// This fixture was moved twice on 2026-08-05 and both moves were wrong, which is worth more than
// the fixture. It went off `chartLayout` on a census, then off `useCustomScenarioColors` on a caveat.
// Both properties have a RENDER behind them -- correction 3 hashed all nine chartLayout values, and
// `gate-experiment-colors.charts.json` runs useCustomScenarioColors as an a/b gate returning
// `identical` -- so neither should ever have moved. Restored to the original property.
//
// The lesson is about fixtures, not about colours: when a fixture has to move so a narrowing can
// land, the narrowing is the thing under suspicion, not the fixture.
rule("property-declared-but-inert", "an inert property set", (s) => {
  s.visuals.V1.visual.objects.designSettings = [{ properties: { useCustomScenarioColors: W("true") } }];
});

// Kept from the original fixture: a class-1 chartLayout value renders identically to setting nothing.
rule("property-declared-but-inert", "a chartLayout value that renders as the default", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: { chartLayout: W("Responsive") } }];
});

rule("in-cell-chart-switch-is-a-number", "showAsTable written as a boolean", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: {
    columnSettings: W(JSON.stringify({ actual: { tableView: { showAsTable: true } } })),
  } }];
});

// A tombstone, not a coverage test. `rename-needs-displayname` was removed on 2026-08-05 because
// its fault shape and the ordinary shape are byte-identical -- see the note in checkers.js where
// the checker used to be. This asserts it stays removed, on the three real shapes that produced
// its 141 false positives: a qualified queryRef with a bare nativeQueryRef, a date hierarchy
// level, and Desktop disambiguating two same-named fields. None of them may produce a finding
// from any rule.
try {
  const shapes = codes(project((s) => {
    s.visuals.V1.visual.query.queryState.Values = { projections: [
      { field: {}, queryRef: "Measures.AC", nativeQueryRef: "AC" },
      { field: {}, queryRef: "MasterData.AC", nativeQueryRef: "AC2" },
      { field: {}, queryRef: "KPI measures.AC (KPI)", nativeQueryRef: "AC_KPI" } ] };
    s.visuals.V1.visual.query.queryState.Category = { projections: [
      { field: {}, queryRef: "Calendar.YQM.Year", nativeQueryRef: "YQM Year" },
      { field: {}, queryRef: "Sales.Product", nativeQueryRef: "Product" } ] };
  }));
  assert.ok(!shapes.has("rename-needs-displayname"), "the removed rule is firing again");
  assert.ok(!srv.checkers["projection-rename-requires-displayname"],
    "the checker is back; it cannot be made correct, see the note in checkers.js");
  assert.ok(!srv.loadRules().some((r) => r.id === "rename-needs-displayname"),
    "the rule is back in rules.json; unmark it in the rule source and rebuild");
  results.push(["PASS", "the rename rule stays removed", ""]);
} catch (err) {
  results.push(["FAIL", "the rename rule stays removed", String(err.message).slice(0, 80)]);
}

rule("table-sort-field-not-shown", "a sort naming a field the table does not show", (s) => {
  s.visuals.V1.visual.query.queryState.Values = { projections: [
    { field: {}, queryRef: "Financials.Value AC", nativeQueryRef: "Value AC" },
  ] };
  s.visuals.V1.visual.query.sortDefinition = { sort: [
    { field: { Column: { Property: "KPI_ID" } }, direction: "Ascending" },
  ] };
});

rule("filterconfig-entry-needs-name", "a filterConfig entry with no name", (s) => {
  s.visuals.V1.filterConfig = { filters: [{ queryRef: "Accounts.Account" }] };
});

rule("slicer-filter-double-nested", "a slicer selection written one level too shallow", (s) => {
  s.visuals.V1.visual.objects.general = [{ properties: {
    filter: { Version: 2, From: [{ Name: "a" }], Where: [{}] },
  } }];
});

rule("nested-settings-need-full-key-set", "a chartView missing keys", (s) => {
  s.visuals.V1.visual.objects.chartSettings = [{ properties: {
    columnSettings: W(JSON.stringify({ actual: { chartView: { textColor: "#000" } } })),
  } }];
});

rule("cards-size-written-into-data", "a card size with no cards to apply it to", (s, G) => {
  s.visuals.V1.visual.visualType = G.cards;
  s.visuals.V1.visual.objects.customData = [{ properties: {
    uniformData: W(JSON.stringify({ instance: 0, globalCardSize: { w: 256, h: 104 }, cards: {} })),
  } }];
});

// The fault that cost a launch on 2026-08-14: a `visual` member written at the container root.
// Desktop refuses to open the report and reports it in a window a title check cannot see, so this
// has to be caught from disk. One arm per denylisted name, because each is a separate typo.
for (const k of ["visualContainerObjects", "visualType", "query", "objects",
  "drillFilterOtherVisuals"]) {
  rule("visual-property-at-the-json-root", `"${k}" written at the visual.json root`, (s) => {
    s.visuals.V1[k] = k === "drillFilterOtherVisuals" ? true : {};
  });
}

// A tooltip page Desktop accepts and then never opens on hover. Three arms, because the property
// is UNREACHABLE on the old schema and merely absent on the current one, and because a visual
// pointing at an unmarked page is the same defect approached from the consumer end.
const PAGE_SCHEMA = (v) =>
  `https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/${v}/schema.json`;

rule("tooltip-page-schema-too-old-for-type", "a tooltip page with no top-level type", (s) => {
  s.page.pageBinding = { name: "Pod1", type: "Tooltip" };
});

rule("tooltip-page-schema-too-old-for-type", "a tooltip page on page schema 1.0.0", (s) => {
  s.page.$schema = PAGE_SCHEMA("1.0.0");
  s.page.pageBinding = { name: "Pod1", type: "Tooltip" };
});

rule("tooltip-page-schema-too-old-for-type", "a visual pointing at an unmarked tooltip page", (s) => {
  s.visuals.V1.visual.visualContainerObjects = {
    visualTooltip: [{ properties: { show: { expr: { Literal: { Value: "true" } } },
      type: W("Canvas"), section: W("P1") } }],
  };
});

// The direction that matters commercially: a tooltip page done RIGHT must stay silent, or the rule
// fires on every report that already gets this correct. The clean project has no tooltip page at
// all, so it cannot prove this -- the page has to be present and correct.
try {
  const root = project((s) => {
    s.page.$schema = PAGE_SCHEMA("2.0.0");
    s.page.pageBinding = { name: "Pod1", type: "Tooltip" };
    s.page.type = "Tooltip";
    s.page.visibility = "HiddenInViewMode";
    s.visuals.V1.visual.visualContainerObjects = {
      visualTooltip: [{ properties: { type: W("Canvas"), section: W("P1") } }],
    };
  });
  assert.ok(!codes(root).has("tooltip-page-schema-too-old-for-type"),
    "fired on a correctly marked tooltip page");
  results.push(["PASS", "a correctly marked tooltip page is silent", ""]);
} catch (e) {
  results.push(["FAIL", "a correctly marked tooltip page is silent", String(e.message).slice(0, 80)]);
}

// --- registration, textbox paragraphs and chart orientation (22 Nov 2026) -------------------------------
// Fixtures are invented. Each rule has a firing case and the otherwise identical case that stays silent.

// A Zebra BI visual type used on a page must be registered in report.json.
rule("zebra-visual-type-not-registered", "a Tables+ visual whose GUID is not in publicCustomVisuals", (s) => {
  s.reportJson.publicCustomVisuals = ["ZebraBITablesPlus7E7C97F00CD14F299A17F402F1D219B5"];
  s.visuals.V1.visual.visualType = GUID.tablesPlus;
});

check("a visual registered by GUID, by an embedded resource package or by the organization's store is NOT flagged", () => {
  const id = "zebraBiCards2C860CFAA9944091B75F0DBD117F20FA";
  const listed = project((s) => { s.reportJson.publicCustomVisuals = [id.toUpperCase()]; s.visuals.V1.visual.visualType = id; });
  assert.ok(!codes(listed).has("zebra-visual-type-not-registered"), "fired on a GUID listed in another case");
  const embedded = project((s) => {
    s.reportJson.publicCustomVisuals = [];
    s.reportJson.resourcePackages = [{ name: "RegisteredResources", type: "RegisteredResources",
      items: [{ name: `${id}.pbiviz.json`, path: `${id}.pbiviz.json`, type: "CustomVisual" }] }];
    s.visuals.V1.visual.visualType = id;
  });
  assert.ok(!codes(embedded).has("zebra-visual-type-not-registered"), "fired on a visual embedded in the report");
  const store = project((s) => {
    s.reportJson.publicCustomVisuals = [];
    s.reportJson.resourcePackages = [{ name: `${id}_OrgStore`, type: "OrganizationalStoreCustomVisual", items: [] }];
    s.visuals.V1.visual.visualType = `${id}_OrgStore`;
  });
  assert.ok(!codes(store).has("zebra-visual-type-not-registered"), "fired on an organizational-store visual");
});

check("native visuals and an unreadable report.json are NOT flagged; one finding names every unregistered type", () => {
  assert.ok(!codes(project((s) => { s.reportJson.publicCustomVisuals = []; s.visuals.V1.visual.visualType = "tableEx"; }))
    .has("zebra-visual-type-not-registered"), "fired on a native visual");
  const bad = project();
  fs.writeFileSync(path.join(bad, "Demo.Report", "definition", "report.json"), "{ not json");
  assert.ok(!codes(bad).has("zebra-visual-type-not-registered"), "judged an unreadable report.json");
  const two = project((s) => {
    s.reportJson.publicCustomVisuals = [];
    s.visuals.V2 = { name: "V2", position: { x: 0, y: 0, width: 600, height: 400 },
      visual: { visualType: GUID.tables, objects: {}, query: { queryState: {} } } };
  });
  const hits = srv.validateReport({ projectPath: two }).findings.filter((f) => f.code === "zebra-visual-type-not-registered");
  assert.strictEqual(hits.length, 1, `one finding for the registration list, got ${hits.length}`);
  assert.ok(hits[0].detail.includes(`'${GUID.tables}' (2 visuals)`), hits[0].detail);
  // two different unregistered types still make one finding, and it names both
  const mixed = project((s) => {
    s.reportJson.publicCustomVisuals = [];
    s.visuals.V2 = { name: "V2", position: { x: 0, y: 0, width: 600, height: 400 },
      visual: { visualType: GUID.charts, objects: {}, query: { queryState: {} } } };
  });
  const both = srv.validateReport({ projectPath: mixed }).findings.filter((f) => f.code === "zebra-visual-type-not-registered");
  assert.strictEqual(both.length, 1);
  assert.ok(both[0].detail.includes(GUID.tables) && both[0].detail.includes(GUID.charts), both[0].detail);
});

// A textbox's paragraphs live in objects.general; under visualContainerObjects.general Desktop refuses them.
const PARAGRAPHS = [{ paragraphs: [{ textRuns: [{ value: "Revenue by month" }] }] }];
rule("textbox-paragraphs-in-container-objects", "a textbox with paragraphs under visualContainerObjects.general", (s) => {
  s.visuals.T1 = { name: "T1", position: { x: 0, y: 0, width: 400, height: 80 },
    visual: { visualType: "textbox",
      visualContainerObjects: { general: [{ properties: { paragraphs: PARAGRAPHS } }] } } };
});

check("a textbox with paragraphs in objects.general (and title and background in the container) is NOT flagged", () => {
  const root = project((s) => {
    s.visuals.T1 = { name: "T1", position: { x: 0, y: 0, width: 400, height: 80 },
      visual: { visualType: "textbox",
        objects: { general: [{ properties: { paragraphs: PARAGRAPHS } }] },
        visualContainerObjects: { general: [{ properties: { keepLayerOrder: { expr: { Literal: { Value: "true" } } } } }],
          background: [{ properties: { show: { expr: { Literal: { Value: "false" } } } } }] } } };
    // a non-textbox may carry any container property; the rule reads textboxes only
    s.visuals.V1.visual.visualContainerObjects = { general: [{ properties: { paragraphs: PARAGRAPHS } }] };
  });
  assert.ok(!codes(root).has("textbox-paragraphs-in-container-objects"), "fired on the shape Desktop writes");
});

check("the paragraph rule and the font-floor rule read the same place: a textbox with the paragraphs in the container is also sized from them", () => {
  const root = project((s) => {
    s.visuals.T1 = { name: "T1", position: { x: 0, y: 0, width: 400, height: 40 },
      visual: { visualType: "textbox", visualContainerObjects: { general: [{ properties: {
        paragraphs: [{ textRuns: [{ textStyle: { fontSize: "22pt" } }] }] } }] } } };
  });
  const c = codes(root);
  assert.ok(c.has("textbox-paragraphs-in-container-objects") && c.has("textbox-too-short-for-font"),
    "the two rules disagree on where a textbox's paragraphs are");
});

// Charts and Charts+ on their side with a time Category.
const writeModel = (root, tmdl) => fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "model.tmdl"), tmdl);
const CAL_MODEL = [
  "table Calendar", "\tdataCategory: Time", "", "\tcolumn Date", "\t\tdataType: dateTime", "\t\tisKey", "",
  "\tcolumn Month", "\t\tdataType: string", "",
  "table Accounts", "", "\tcolumn Account", "\t\tdataType: string", "",
  "table Orders", "", "\tcolumn 'Ship date'", "\t\tdataType: dateTime", "", "\tcolumn Region", "\t\tdataType: string", "",
  "table Financials", "\tmeasure 'Value AC' = 1", "\t\tformatString: #,0", "\tmeasure 'Value PL' = 1", "\t\tformatString: #,0", "",
].join("\n");
const TRUE_ = { expr: { Literal: { Value: "true" } } };
const col = (entity, prop) => ({ Column: { Expression: { SourceRef: { Entity: entity } }, Property: prop } });
const chartOn = (guid, field, vertical = TRUE_) => (s) => {
  s.visuals.C1 = { name: "C1", position: { x: 0, y: 0, width: 600, height: 400 },
    visual: { visualType: guid, query: { queryState: { Category: { projections: [{ field, queryRef: "x" }] } } },
      objects: { chartSettings: [{ properties: { showVerticalCharts: vertical } }] } } };
};
const verticalCodes = (mutate, model = CAL_MODEL) => {
  const root = project(mutate); writeModel(root, model); return codes(root);
};
const V = "charts-vertical-orientation-on-time";

check("showVerticalCharts: true on a date-table level or a dateTime column is caught, on Charts and Charts+", () => {
  for (const g of [GUID.charts, GUID.chartsPlus]) {
    assert.ok(verticalCodes(chartOn(g, col("Calendar", "Month"))).has(V), `missed a date-table level on ${g}`);
    assert.ok(verticalCodes(chartOn(g, col("Calendar", "Date"))).has(V), `missed the date column on ${g}`);
    assert.ok(verticalCodes(chartOn(g, col("Orders", "Ship date"))).has(V), `missed a dateTime column outside the date table on ${g}`);
  }
  const level = { HierarchyLevel: { Level: "Month", Expression: { Hierarchy: { Hierarchy: "Date Hierarchy",
    Expression: { PropertyVariationSource: { Name: "Variation", Property: "Date", Expression: { SourceRef: { Entity: "Orders" } } } } } } } };
  assert.ok(verticalCodes(chartOn(GUID.chartsPlus, level)).has(V), "missed a level of the Date Hierarchy");
});

check("showVerticalCharts on an account category, or off, or on a time category with another visual, is NOT flagged", () => {
  for (const g of [GUID.charts, GUID.chartsPlus]) {
    assert.ok(!verticalCodes(chartOn(g, col("Accounts", "Account"))).has(V), `fired on an account axis on ${g}`);
    assert.ok(!verticalCodes(chartOn(g, col("Orders", "Region"))).has(V), `fired on a region axis on ${g}`);
    assert.ok(!verticalCodes(chartOn(g, col("Calendar", "Month"), { expr: { Literal: { Value: "false" } } })).has(V),
      `fired with showVerticalCharts false on ${g}`);
  }
  assert.ok(!verticalCodes(chartOn(GUID.tables, col("Calendar", "Month"))).has(V), "fired on a Tables visual");
  assert.ok(!verticalCodes(chartOn(GUID.charts, col("Nowhere", "Month"))).has(V), "judged a table the model does not declare");
});

check("showVerticalCharts: true with a time category is not judged when the project has no model", () => {
  const root = project(chartOn(GUID.charts, col("Calendar", "Month")));
  fs.rmSync(path.join(root, "Demo.SemanticModel"), { recursive: true, force: true });
  assert.ok(!codes(root).has(V), "judged a report with no model");
});

// definition.pbir beside a PBIR report folder: version 1.0 with no $schema opened an empty Page 1 (22 Nov 2026).
rule("pbir-version-and-schema-do-not-match-pbir-folder", "definition.pbir at version 1.0 with no $schema", (s) => {
  s.pbir = { version: "1.0", datasetReference: { byPath: { path: "../Demo.SemanticModel" } } };
});

check("a pbir with version 4.0 and a $schema, or 4.0 with no $schema, or a $schema at any version, is NOT flagged", () => {
  const id = "pbir-version-and-schema-do-not-match-pbir-folder";
  const ref = { byPath: { path: "../Demo.SemanticModel" } };
  assert.ok(!codes(project()).has(id), "fired on the form that opens");
  assert.ok(!codes(project((s) => { s.pbir = { version: "4.0", datasetReference: ref }; })).has(id), "fired on 4.0 with no $schema");
  assert.ok(!codes(project((s) => { s.pbir = { $schema: s.pbir.$schema, version: "1.0", datasetReference: ref }; })).has(id),
    "fired on a pbir that carries a $schema");
  assert.ok(!codes(project((s) => { s.pbir = { datasetReference: ref }; })).has(id), "fired on a pbir with no version");
  const noFolder = project((s) => { s.pbir = { version: "1.0", datasetReference: ref }; });
  fs.rmSync(path.join(noFolder, "Demo.Report", "definition", "report.json"));
  assert.ok(!codes(noFolder).has(id), "judged a report folder that is not in the PBIR format");
});

// chartSettings.calculations on Tables / Tables+ is one comparison pair per variance slot.
const CALC = "tables-calculations-repeat-a-comparison";
const proj = (n) => Array.from({ length: n }, (_, i) => ({ field: {}, queryRef: "m" + i }));
const tableWith = (guid, roles, calcs) => (s) => {
  const queryState = {};
  for (const [role, n] of Object.entries(roles)) queryState[role] = { projections: proj(n) };
  s.visuals.V1.visual = { visualType: guid, query: { queryState },
    objects: calcs === undefined ? {} : { chartSettings: [{ properties: { calculations: W(JSON.stringify(calcs)) } }] } };
};
const calcCodes = (guid, roles, calcs) => codes(project(tableWith(guid, roles, calcs)));
const ACPYPL = { Values: 1, PreviousYear: 1, Plan: 1 };

check("a four-key calculations value on a three-scenario table draws the plan variance twice and is caught", () => {
  const four = ["actual-plan", "actual-plan-percent", "actual-previousYear", "actual-previousYear-percent"];
  for (const g of [GUID.tables, GUID.tablesPlus]) {
    const root = project(tableWith(g, ACPYPL, four));
    const hit = srv.validateReport({ projectPath: root }).findings.find((f) => f.code === CALC);
    assert.ok(hit, "missed the four-key value on " + g);
    assert.strictEqual(hit.severity, "wrong-numbers");
    assert.ok(/actual-plan, actual-plan/.test(hit.detail) && /actual-previousYear is not drawn/.test(hit.detail), hit.detail);
  }
  assert.ok(calcCodes(GUID.tablesPlus, ACPYPL, ["actual-previousYear", "actual-previousYear-percent", "actual-plan", "actual-plan-percent"]).has(CALC),
    "missed the mirror value");
  assert.ok(calcCodes(GUID.tables, { Values: 1, Forecast: 1, Plan: 1 }, ["actual-plan"]).has(CALC),
    "missed one entry that repeats the pair another slot shows by default");
});

check("pairs only, the key absent, a harmless percent tail past the drawn slots and other shapes are NOT flagged", () => {
  const silent = (what, guid, roles, calcs) => assert.ok(!calcCodes(guid, roles, calcs).has(CALC), "fired on " + what);
  silent("distinct pairs", GUID.tablesPlus, ACPYPL, ["actual-previousYear", "actual-plan"]);
  silent("PL first", GUID.tablesPlus, ACPYPL, ["actual-plan", "actual-previousYear"]);
  silent("the key absent", GUID.tablesPlus, ACPYPL, undefined);
  silent("an empty list", GUID.tablesPlus, ACPYPL, []);
  silent("a null placeholder", GUID.tablesPlus, ACPYPL, [null, "actual-plan"]);
  // two scenarios: one variance slot is drawn, so a percent tail in slot 2 is never read
  silent("a two-scenario table with a percent tail", GUID.tablesPlus, { Values: 1, PreviousYear: 1 }, ["actual-previousYear", "actual-previousYear-percent"]);
  silent("AC/FC/PL with the forecast pair first", GUID.tables, { Values: 1, Forecast: 1, Plan: 1 }, ["forecast-plan", "actual-plan"]);
  // a Plan role with two projections adds plan2; the vendor shape [null, plan-actual, plan2-actual]
  silent("a table with two plan projections", GUID.tables, { Values: 1, Plan: 2 }, [null, "plan-actual", "plan2-actual"]);
  // an entry naming a scenario that is not bound ends the loop; later entries are not read
  silent("an entry that names an unbound scenario", GUID.tablesPlus, ACPYPL, ["actual-forecast", "actual-plan", "actual-plan"]);
  // not a table, not an array, no Values
  assert.ok(!calcCodes(GUID.charts, ACPYPL, ["actual-plan", "actual-plan-percent"]).has(CALC), "fired on a Charts visual");
  assert.ok(!codes(project(tableWith(GUID.tablesPlus, ACPYPL, "x"))).has(CALC), "judged a calculations value that is a string");
  silent("no Values bound", GUID.tablesPlus, { PreviousYear: 1, Plan: 1 }, ["actual-plan", "actual-plan-percent"]);
});

// A table with computed rows (addedFormulas) must not be sorted by a value column.
const SORTED = "computed-rows-sorted-by-value";
const ADDED = [{ identity: "Gross profit", expression: "[Revenue] + [Cost of sales]", hierarchyIdentity: "Fin.Account",
  position: "Cost of sales", bold: true }];
// A Tables or Tables+ visual with a Category, computed rows and the given sort keys.
const sortedTable = (guid, sort, extra = {}, added = ADDED) => (s) => {
  const sortProps = {};
  for (const [k, v] of Object.entries(sort)) sortProps[k] = typeof v === "number" ? N(v) : W(v);
  s.visuals.V1.visual = { visualType: guid,
    query: { queryState: { Category: { projections: [{ field: { Column: { Property: "Account" } },
      queryRef: "Fin.Account", nativeQueryRef: "Account", displayName: "P&L line" }] },
      Values: { projections: [{ field: {}, queryRef: "Fin.AC" }] } } },
    objects: { categoriesMetadata: [{ properties: { addedFormulas: W(JSON.stringify(added)) } }],
      ...(Object.keys(sort).length ? { sortSettings: [{ properties: sortProps }] } : {}), ...extra } };
};
const sortedCodes = (guid, sort, extra, added) => codes(project(sortedTable(guid, sort, extra, added)));

check("computed rows sorted by a value column are caught, on Tables and Tables+, in every form of that sort", () => {
  for (const g of [GUID.tables, GUID.tablesPlus]) {
    assert.ok(sortedCodes(g, { columnName: "actual", categorySort: 1 }).has(SORTED), "missed descending by actual on " + g);
    assert.ok(sortedCodes(g, { columnName: "actual", categorySort: 2, chartSort: 2 }).has(SORTED), "missed ascending by actual on " + g);
    assert.ok(sortedCodes(g, { categorySort: 1 }).has(SORTED), "missed a descending sort with no columnName (the first Values column)");
    // the shape of the agent-built page that drew the subtotals among the lines
    assert.ok(sortedCodes(g, { columnName: "actual", categorySort: 0 }).has(SORTED), "missed categorySort 0 on a value column with no chartSort");
    assert.ok(sortedCodes(g, { columnName: "plan-actual", categorySort: 1 }).has(SORTED), "missed a variance column");
    assert.ok(sortedCodes(g, { columnName: "Amount AC", categorySort: 1 }).has(SORTED), "missed a measure's display name");
  }
  const hit = srv.validateReport({ projectPath: project(sortedTable(GUID.tablesPlus, { columnName: "actual", categorySort: 0 })) })
    .findings.find((f) => f.code === SORTED);
  assert.strictEqual(hit.severity, "looks-fine");
});

check("the statement-order sorts, the hidden order column and everything that is not a value sort are NOT flagged", () => {
  const silentSort = (what, sort, extra, added, guid = GUID.tablesPlus) =>
    assert.ok(!sortedCodes(guid, sort, extra, added).has(SORTED), "fired on " + what);
  for (const g of [GUID.tables, GUID.tablesPlus]) {
    silentSort("categorySort 0 and chartSort 0 on 'actual' (28 shipped statement tables)", { columnName: "actual", categorySort: 0, chartSort: 0 }, undefined, ADDED, g);
    silentSort("categorySort 0 and chartSort 0 with no columnName", { categorySort: 0, chartSort: 0 }, undefined, ADDED, g);
  }
  silentSort("columnName 'category'", { columnName: "category", categorySort: 0 });
  silentSort("columnName 'category' descending", { columnName: "category", categorySort: 1 });
  silentSort("columnName naming the category column", { columnName: "Account", categorySort: 2 });
  silentSort("columnName naming the category's display name", { columnName: "p&l line", categorySort: 2 });
  silentSort("a columnName with no categorySort (the shipped Financial statements shape)", { columnName: "actual" });
  silentSort("no sortSettings at all", {});
  // the remedy the message names: an order column, hidden, sorted on
  const hidden = { chartSettings: [{ properties: { columnSettings: W(JSON.stringify({ "Row order": { order: 20, hidden: true } })) } }] };
  silentSort("a hidden order column", { columnName: "Row order", categorySort: 2, chartSort: 2 }, hidden);
  assert.ok(sortedCodes(GUID.tablesPlus, { columnName: "Row order", categorySort: 2, chartSort: 2 },
    { chartSettings: [{ properties: { columnSettings: W(JSON.stringify({ "Row order": { order: 20, hidden: false } })) } }] }).has(SORTED),
    "an order column left visible is still a sort by a value column");
  silentSort("a table with no computed rows sorted by actual", { columnName: "actual", categorySort: 1 }, undefined, []);
  silentSort("a table with no addedFormulas value", { columnName: "actual", categorySort: 1 }, undefined, null);
  // not a table
  assert.ok(!sortedCodes(GUID.charts, { columnName: "actual", categorySort: 1 }).has(SORTED), "fired on a Charts visual");
});

// --- review fixes: a checker reports, never throws; one slot simulation; fences and block comments -----

// Every rule over a project whose arrays hold null members, a visualType that is not a string, and the
// shapes the nine rules of 22 Nov read. A rule that throws loses every finding on the project.
const nullify = (x) => {
  if (Array.isArray(x)) return [null, ...x.map(nullify), null];
  if (x && typeof x === "object") return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, nullify(v)]));
  return x;
};
const nullifyJsonFiles = (dir) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const f = path.join(dir, e.name);
    if (e.isDirectory()) nullifyJsonFiles(f);
    else if (/\.(json|pbir|pbip)$/.test(e.name)) {
      const raw = fs.readFileSync(f, "utf8").replace(/^﻿/, "");
      let j; try { j = JSON.parse(raw); } catch { continue; }
      fs.writeFileSync(f, JSON.stringify(nullify(j)));
    }
  }
};
const dateField = { Column: { Expression: { SourceRef: { Entity: "Calendar" } }, Property: "Date" } };
const richProject = (s) => {
  s.reportJson.resourcePackages = [{ name: "R", type: "RegisteredResources", items: [{ name: "a", path: "a", type: "CustomVisual" }] }];
  s.pbip.artifacts = [{ report: { path: "Demo.Report" } }];
  const roles = (n) => ({ projections: Array.from({ length: n }, (_, i) => ({ field: dateField, queryRef: "m" + i })) });
  s.visuals.V1 = { name: "V1", position: { x: 0, y: 0, width: 600, height: 400 },
    visual: { visualType: GUID.tablesPlus,
      query: { queryState: { Category: roles(2), Values: roles(1), PreviousYear: roles(1), Plan: roles(1), Forecast: roles(1) },
        sortDefinition: { sort: [{ field: dateField, direction: "Ascending" }] } },
      objects: { chartSettings: [{ properties: { calculations: W(JSON.stringify(["actual-plan", "actual-plan-percent"])),
        showVerticalCharts: { expr: { Literal: { Value: "true" } } } } }],
        sortSettings: [{ properties: {} }] },
      visualContainerObjects: { general: [{ properties: {} }] } } };
  s.visuals.C1 = { name: "C1", position: { x: 0, y: 0, width: 600, height: 400 },
    visual: { visualType: GUID.chartsPlus, query: { queryState: { Category: roles(2) } },
      objects: { chartSettings: [{ properties: { showVerticalCharts: { expr: { Literal: { Value: "true" } } } } }] } } };
  s.visuals.T1 = { name: "T1", position: { x: 0, y: 0, width: 400, height: 40 },
    visual: { visualType: "textbox",
      objects: { general: [{ properties: { paragraphs: [{ textRuns: [{ value: "x" }] }] } }] },
      visualContainerObjects: { general: [{ properties: { paragraphs: [{ textRuns: [{ value: "x" }] }] } }] } } };
  s.visuals.N1 = { name: "N1", position: { x: 0, y: 0, width: 100, height: 100 }, visual: { visualType: 42, objects: {} } };
};

check("no rule throws on a project whose arrays hold null members", () => {
  const root = project(richProject);
  fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "model.tmdl"), [
    "table Calendar", "\tdataCategory: Time", "", "\tcolumn Date", "\t\tdataType: dateTime", "\t\tisKey", ""].join("\n"));
  nullifyJsonFiles(root);
  try { srv.validateReport({ projectPath: root }); }
  catch (e) { assert.fail("validate_report threw: " + e.stack.split("\n").slice(0, 3).join(" | ")); }
  const p = srv.resolveProject(root);
  const thrown = [];
  for (const rule of srv.loadRules()) {
    try { srv.checkers[rule.check](p); } catch (e) { thrown.push(`${rule.id}: ${e.message}`); }
  }
  assert.deepStrictEqual(thrown, [], thrown.join(" | "));
});

check("the vertical-orientation rule reports, and does not throw, on a null Category projection", () => {
  const root = project((s) => {
    s.visuals.C1 = { name: "C1", position: { x: 0, y: 0, width: 600, height: 400 },
      visual: { visualType: GUID.charts, query: { queryState: { Category: { projections: [null, { field: dateField, queryRef: "x" }] } } },
        objects: { chartSettings: [{ properties: { showVerticalCharts: { expr: { Literal: { Value: "true" } } } } }] } } };
  });
  fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "model.tmdl"), [
    "table Calendar", "\tdataCategory: Time", "", "\tcolumn Date", "\t\tdataType: dateTime", "\t\tisKey", ""].join("\n"));
  const out = srv.validateReport({ projectPath: root });
  assert.ok(out.ok, out.error);
});

check("a drill hierarchy is judged on its first level: Account then Month is a ranking, Month then Account is not", () => {
  const model = "table Accounts\n\n\tcolumn Account\n\t\tdataType: string\n\ntable Calendar\n\tdataCategory: Time\n\n\tcolumn Month\n\t\tdataType: string\n";
  const acct = { Column: { Expression: { SourceRef: { Entity: "Accounts" } }, Property: "Account" } };
  const month = { Column: { Expression: { SourceRef: { Entity: "Calendar" } }, Property: "Month" } };
  const run = (fields) => {
    const root = project((s) => {
      s.visuals.C1 = { name: "C1", position: { x: 0, y: 0, width: 600, height: 400 },
        visual: { visualType: GUID.charts, query: { queryState: { Category: { projections: fields.map((field, i) => ({ field, queryRef: "f" + i })) } } },
          objects: { chartSettings: [{ properties: { showVerticalCharts: { expr: { Literal: { Value: "true" } } } } }] } } };
    });
    fs.writeFileSync(path.join(root, "Demo.SemanticModel", "definition", "model.tmdl"), model);
    return codes(root);
  };
  assert.ok(!run([acct, month]).has("charts-vertical-orientation-on-time"), "fired on a ranking with a time drill");
  assert.ok(run([month, acct]).has("charts-vertical-orientation-on-time"), "missed a time first level");
});

// One implementation of the visual's slot logic: the checker, the encoder and the writers agree.
check("the calculations rule and tableComparisonSlots agree on 3,000 role and list combinations", () => {
  const enc = require("./encoders.js");
  const names = ["actual", "previousYear", "plan", "plan2", "plan3", "forecast", "forecast2", "forecast3", "plan4"];
  let seed = 12345; const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
  const rule = srv.checkers["tables-calculations-must-name-distinct-drawn-pairs"];
  let fired = 0;
  for (let i = 0; i < 3000; i++) {
    const roles = { Values: rnd(3), PreviousYear: rnd(2), Plan: rnd(5), Forecast: rnd(4) };
    const list = Array.from({ length: rnd(5) }, () => {
      const k = rnd(10); if (k === 0) return null;
      const a = k < 7 ? "actual" : names[rnd(names.length)], b = names[rnd(names.length)];
      return k === 1 || k === 8 ? `${a}-${b}-percent` : `${a}-${b}`;
    });
    const parsed = { visual: { visualType: GUID.tablesPlus, query: { queryState: Object.fromEntries(Object.entries(roles)
      .filter(([, n]) => n > 0).map(([r, n]) => [r, { projections: Array.from({ length: n }, () => ({})) }])) },
      objects: { chartSettings: [{ properties: { calculations: W(JSON.stringify(list)) } }] } } };
    const hits = rule({ visuals: [{ visualType: GUID.tablesPlus, rel: "v.json", parsed }] });
    const want = enc.tableComparisonSlots(roles, list).repeats.length > 0;
    assert.strictEqual(hits.length > 0, want, `roles ${JSON.stringify(roles)} list ${JSON.stringify(list)}`);
    if (want) fired++;
  }
  assert.ok(fired > 100, `only ${fired} cases exercised a repeat`);
});

check("a table with no Values is judged as the visual's role assignment does, and a role counts three measures at most", () => {
  const enc = require("./encoders.js");
  // no Values: Previous year becomes the value scenario, Plan its reference; ["previousYear-plan2"] then repeats nothing
  const noValues = { PreviousYear: 1, Plan: 2 };
  assert.deepStrictEqual(enc.tableReferences(noValues), { value: "previousYear", references: ["plan", "plan2"] });
  assert.ok(calcCodes(GUID.tablesPlus, noValues, ["previousYear-plan2"]).has(CALC), "the checker skipped a table with no Values");
  // a fourth Plan measure is not a fourth reference: the visual has no name for it
  assert.deepStrictEqual(enc.tableReferences({ Values: 1, Plan: 4 }).references, ["plan", "plan2", "plan3"]);
  assert.ok(!calcCodes(GUID.tablesPlus, { Values: 1, Plan: 4 }, ["actual-plan", "actual-plan2", "actual-plan3"]).has(CALC),
    "judged a reference the visual cannot name");
});

// --- a slicer selection the column cannot hold (22 Nov 2026) ------------------------------------
// A month column defined with FORMAT ( [Date], "mmm" ) holds Sep; a slicer that selects 'September' matches
// no row, so the page is filtered to nothing while the slicer still shows September as selected. Judged only
// where the files fix the column's values: English culture, a calendar made by ADDCOLUMNS over CALENDAR.
const SELV = "slicer-selection-is-not-a-value-of-its-column";
const calSource = (fmt = '"mmm"', col = "Month", last = "") => "ADDCOLUMNS (\n\t\t\t\t\tCALENDAR ( DATE ( 2024, 1, 1 ), DATE ( 2024, 12, 31 ) ),\n"
  + `\t\t\t\t\t"MonthNo", MONTH ( [Date] ),\n\t\t\t\t\t"${col}", FORMAT ( [Date], ${fmt}${last} )\n\t\t\t\t)`;
const calTmdl = (source, col = "Month", sourceColumn = null) => "table Calendar\n\tdataCategory: Time\n\n\tcolumn Date\n\t\tdataType: dateTime\n\t\tisKey\n\t\tsummarizeBy: none\n"
  + "\t\tsourceColumn: [Date]\n\n\tcolumn MonthNo\n\t\tdataType: int64\n\t\tisHidden\n\t\tsummarizeBy: none\n\t\tsourceColumn: [MonthNo]\n\n"
  + `\tcolumn ${col}\n\t\tdataType: string\n\t\tsummarizeBy: none\n\t\tsourceColumn: [${sourceColumn || col}]\n\t\tsortByColumn: MonthNo\n\n`
  + "\tpartition Calendar = calculated\n\t\tmode: import\n\t\tsource =\n\t\t\t\t" + source + "\n";
const MPART = "table Calendar\n\n\tcolumn Month\n\t\tdataType: string\n\t\tsourceColumn: Month\n\n\tpartition Calendar = m\n\t\tmode: import\n\t\tsource =\n"
  + "\t\t\t\tlet\n\t\t\t\t    S = 1\n\t\t\t\tin\n\t\t\t\t    S\n";
const selectionOf = (values, { entity = "Calendar", prop = "Month", flat = false, not = false } = {}) => {
  const inn = { In: { Expressions: [{ Column: { Expression: { SourceRef: { Source: "c" } }, Property: prop } }],
    Values: values.map((v) => [{ Literal: { Value: v } }]) } };
  const f = { Version: 2, From: [{ Name: "c", Entity: entity, Type: 0 }], Where: [{ Condition: not ? { Not: { Expression: inn } } : inn }] };
  return flat ? f : { filter: f };
};
function slicerProject({ culture = "en-US", tmdl = calTmdl(calSource()), values = ["'March'"], sel = {}, byPath = null, type = "slicer" } = {}) {
  const root = project((s) => {
    s.visuals.MonthPick = { name: "MonthPick", position: { x: 0, y: 0, width: 200, height: 60 },
      visual: { visualType: type, objects: { general: [{ properties: { filter: selectionOf(values, sel) } }] }, query: { queryState: {} } } };
    if (byPath) s.pbir.datasetReference.byPath.path = byPath;
  });
  const md = path.join(root, "Demo.SemanticModel", "definition");
  fs.writeFileSync(path.join(md, "model.tmdl"), "model Model\n" + (culture === null ? "" : `\tculture: ${culture}\n`));
  fs.mkdirSync(path.join(md, "tables"), { recursive: true });
  fs.writeFileSync(path.join(md, "tables", "Calendar.tmdl"), tmdl);
  return root;
}
const selHits = (opts) => srv.validateReport({ projectPath: slicerProject(opts) }).findings.filter((f) => f.code === SELV);

check("a slicer selecting a month in full, on a column defined with FORMAT mmm, is caught once, at the slicer, as blank", () => {
  const hits = selHits();
  assert.strictEqual(hits.length, 1);
  assert.strictEqual(hits[0].severity, "blank");
  assert.ok(/MonthPick\/visual\.json:objects\.general\.filter \(Calendar\.Month = 'March'\)$/.test(hits[0].location), hits[0].location);
  // one finding per value the column does not hold, none for the ones it does
  const two = selHits({ values: ["'Mar'", "'September'", "'Feb'", "'Sept'"] });
  assert.deepStrictEqual(two.map((h) => /= '(.*)'\)$/.exec(h.location)[1]), ["September", "Sept"]);
});

check("the selection written as the model produces it, in any case, or on the spelled-out format, is NOT flagged", () => {
  assert.strictEqual(selHits({ values: ["'Mar'"] }).length, 0, "fired on Mar");
  assert.strictEqual(selHits({ values: ["'mar'", "'SEP'", "'Dec'"] }).length, 0, "compared with case");
  assert.strictEqual(selHits({ values: ["' Mar '"] }).length, 0, "fired on a value that matches once trimmed");
  assert.strictEqual(selHits({ tmdl: calTmdl(calSource('"mmmm"')), values: ["'March'", "'september'"] }).length, 0, "fired on the spelled-out format");
  assert.strictEqual(selHits({ tmdl: calTmdl(calSource('"MMM"')), values: ["'Mar'"] }).length, 0, "fired on MMM");
  // the same fixture with the other direction: a short name on the spelled-out format is wrong
  assert.strictEqual(selHits({ tmdl: calTmdl(calSource('"mmmm"')), values: ["'Mar'"] }).length, 1, "missed a short name on mmmm");
});

check("weekday formats are judged the same way: ddd holds Mon, dddd holds Monday", () => {
  const day = (fmt, v) => selHits({ tmdl: calTmdl(calSource(fmt, "Day"), "Day"), values: [v], sel: { prop: "Day" } }).length;
  assert.strictEqual(day('"ddd"', "'Monday'"), 1, "missed Monday on ddd");
  assert.strictEqual(day('"ddd"', "'Mon'"), 0, "fired on Mon on ddd");
  assert.strictEqual(day('"dddd"', "'Mon'"), 1, "missed Mon on dddd");
  assert.strictEqual(day('"dddd"', "'Monday'"), 0, "fired on Monday on dddd");
});

check("a model culture that is not English, absent, or an English one that spells the short names differently is NOT judged for them", () => {
  for (const culture of ["de-DE", "sl-SI", "fr-FR", null]) assert.strictEqual(selHits({ culture }).length, 0, "judged culture " + culture);
  for (const culture of ["en-GB", "en-AU", "en-IN"]) {
    assert.strictEqual(selHits({ culture }).length, 0, "judged mmm for " + culture);
    // the spelled-out names are the same in every English culture
    assert.strictEqual(selHits({ culture, tmdl: calTmdl(calSource('"mmmm"')), values: ["'Mar'"] }).length, 1, "missed Mar on mmmm for " + culture);
  }
  assert.strictEqual(selHits({ culture: "en" }).length, 1, "missed the neutral English culture");
});

check("a column whose values the files do not fix is NOT judged: other formats, a wrapped call, an M table, another source, a renamed column", () => {
  const miss = (what, opts) => assert.strictEqual(selHits(opts).length, 0, "judged " + what);
  miss("mmm yyyy", { tmdl: calTmdl(calSource('"mmm yyyy"')), values: ["'March 2024'"] });
  miss("a third argument (a locale)", { tmdl: calTmdl(calSource('"mmm"', "Month", ', "de-DE"')) });
  miss("a call wrapped in a concatenation", { tmdl: calTmdl(calSource().replace('FORMAT ( [Date], "mmm" )', '"M" & FORMAT ( [Date], "mmm" )')) });
  miss("a call inside IF", { tmdl: calTmdl(calSource().replace('FORMAT ( [Date], "mmm" )', 'IF ( TRUE (), FORMAT ( [Date], "mmm" ), "-" )')) });
  miss("FORMAT of another column", { tmdl: calTmdl(calSource().replace("FORMAT ( [Date]", "FORMAT ( [Posted]")) });
  miss("FORMAT of a column written with its table", { tmdl: calTmdl(calSource().replace("FORMAT ( [Date]", "FORMAT ( 'Fact'[Date]")) });
  miss("an M partition", { tmdl: MPART });
  miss("a source that is not ADDCOLUMNS", { tmdl: calTmdl("UNION ( " + calSource() + ", ROW ( \"Month\", \"x\" ) )") });
  miss("ADDCOLUMNS over a table that is not CALENDAR", { tmdl: calTmdl(calSource().replace(/CALENDAR \(.*\),\n/, "'Fact',\n")) });
  miss("SELECTCOLUMNS", { tmdl: calTmdl(calSource().replace("ADDCOLUMNS", "SELECTCOLUMNS")) });
  miss("a column with its own expression", { tmdl: calTmdl(calSource()).replace("sourceColumn: [Month]", "= \"x\"") });
  miss("a column bound to another name", { tmdl: calTmdl(calSource(), "Month", "Mon") });
  miss("a column that is not text", { tmdl: calTmdl(calSource()).replace("dataType: string", "dataType: int64") });
  miss("a name defined twice", { tmdl: calTmdl(calSource().replace('"MonthNo", MONTH ( [Date] ),', '"Month", FORMAT ( [Date], "mmmm" ),')) });
});

check("a selection that is not an In of text on that column, or sits where the rule does not read, is NOT flagged", () => {
  const miss = (what, opts) => assert.strictEqual(selHits(opts).length, 0, "judged " + what);
  miss("Not In", { sel: { not: true } });
  miss("a flat-nested selection", { sel: { flat: true } });
  miss("a number", { values: ["3L", "null"] });
  miss("another column", { sel: { prop: "MonthNo" } });
  miss("another table", { sel: { entity: "Fact" } });
  miss("a visual that is not a slicer", { type: GUID.tables });
  miss("a model the report does not point at", { byPath: "../Other.SemanticModel" });
  assert.strictEqual(selHits({ sel: { entity: "calendar", prop: "month" } }).length, 1, "names are compared without case");
});

check("a model the report points at by a path with backslashes is the one judged", () => {
  assert.strictEqual(selHits({ byPath: "..\\Demo.SemanticModel" }).length, 1);
});

// --- the silence bar -----------------------------------------------------------------------
// The acceptance test the held catalogue paid for: a rule that fires on a good report is worse than
// no rule, because it trains the reader to ignore the tool.

try {
  const out = srv.validateReport({ projectPath: project() });
  assert.strictEqual(out.findings.length, 0,
    `a clean project produced ${out.findings.length} finding(s): ` +
    out.findings.map((f) => f.code).join(", "));
  results.push(["PASS", "a clean project produces ZERO findings", ""]);
} catch (e) {
  results.push(["FAIL", "a clean project produces ZERO findings", String(e.message).slice(0, 110)]);
}

// The silence bar again, for Tables+ and Charts+ specifically. Giving Charts+ its own prefix turned
// every isZebra()-guarded rule ON for a visual they had all been skipping, so the fixture above --
// which uses the certified Tables GUID -- proves nothing about them. A rule that starts firing on a
// correct Tables+ or Charts+ page is a false positive we introduced, and 19 of those shipped once already.
for (const [label, guid] of [["Tables+", GUID.tablesPlus], ["Charts+", GUID.chartsPlus]]) {
  try {
    const out = srv.validateReport({ projectPath: project((s, G) => {
      s.visuals.V1.visual.visualType = guid;
      s.reportJson.publicCustomVisuals = [guid];
    }) });
    assert.strictEqual(out.findings.length, 0,
      `a clean ${label} project produced ${out.findings.length} finding(s): ` +
      out.findings.map((f) => f.code).join(", "));
    results.push(["PASS", `a clean ${label} project produces ZERO findings`, ""]);
  } catch (e) {
    results.push(["FAIL", `a clean ${label} project produces ZERO findings`, String(e.message).slice(0, 110)]);
  }
}

try {
  const out = srv.validateReport({ projectPath: project() });
  const rules = srv.loadRules();
  const implemented = rules.filter((r) => srv.checkers[r.check]).length;
  results.push(["PASS", `${implemented}/${rules.length} rules have a checker`, ""]);
  // With every rule implemented there is no count to declare, and declaring "0 rule(s)" would
  // be noise. The assertion is that the two states are consistent, not that the line exists.
  const unrun = rules.length - implemented;
  const declared = out.notChecked.some((s) => s.includes(`${unrun} rule(s)`));
  assert.ok(unrun === 0 ? !declared : declared,
    `unrun=${unrun} but notChecked ${declared ? "declares" : "does not declare"} it`);
  results.push(["PASS", "unimplemented rules are declared only when there are some", ""]);
} catch (e) {
  results.push(["FAIL", "the unimplemented rules are declared, not hidden", String(e.message).slice(0, 80)]);
}

for (const [s, n, d] of results) console.log(`  ${s}  ${n}${d ? "  <- " + d : ""}`);
const failed = results.filter((r) => r[0] === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
