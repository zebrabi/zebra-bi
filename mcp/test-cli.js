#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * Tests for the script front end (cli.js): the way to call the Zebra BI tools on a host that loads
 * the skills but does not register the local tool server.
 *
 * Every call here runs as a child process from a working directory that is NOT the plugin's, which
 * is how a host runs it: `node <plugin>/mcp/cli.js <tool> @args.json`.
 *
 *   node test-cli.js
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const srv = require("./server.js");

const CLI = path.join(__dirname, "cli.js");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-cli-"));
const CWD = path.join(TMP, "elsewhere");
fs.mkdirSync(CWD);

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(["PASS", name, ""]); }
  catch (err) { results.push(["FAIL", name, String(err.message).split("\n")[0].slice(0, 110)]); }
};
const run = (...args) => {
  const r = spawnSync(process.execPath, [CLI, ...args], { cwd: CWD, encoding: "utf8" });
  return { code: r.status, out: r.stdout, err: r.stderr };
};
const argsFile = (name, value) => {
  const f = path.join(TMP, name);
  fs.writeFileSync(f, typeof value === "string" ? value : JSON.stringify(value));
  return "@" + f;
};

const names = srv.listTools().map((t) => t.name);

check("list names every tool the server lists, one per line, and exits 0", () => {
  const r = run("list");
  assert.strictEqual(r.code, 0, r.err);
  const listed = r.out.trim().split("\n").map((l) => l.split(" - ")[0]);
  assert.deepStrictEqual(listed, names);
  // the same set tools/list answers over stdio, and the ones the skill names
  const all = [...srv.TOOLS.map((t) => t.name), ...Object.keys(srv.CALLABLE)].sort();
  assert.deepStrictEqual([...listed].sort(), all);
  for (const n of ["validate_report", "propose_pages", "profile_project", "profile_source", "write_visual",
    "write_page", "write_report", "write_project", "write_measures", "encode_added_formulas"]) {
    assert.ok(listed.includes(n), `${n} is not listed`);
  }
  assert.strictEqual(new Set(listed).size, listed.length);
});

// A project to work on, written by the tool and read back by the others.
const src = path.join(TMP, "src");
fs.mkdirSync(src);
fs.writeFileSync(path.join(src, "sales.csv"),
  "Date,Region,Scenario,Amount\n2025-01-01,North,Actual,10\n2025-01-01,North,Budget,12\n2025-02-01,South,Actual,20\n2025-02-01,South,Budget,18\n");
const out = path.join(TMP, "out");
const projectArgs = { name: "Demo", outPath: out, source: { path: src } };

check("a writing tool (write_project) runs from another directory, prints its JSON result and writes the files", () => {
  const r = run("write_project", argsFile("wp.json", projectArgs));
  assert.strictEqual(r.code, 0, r.err + r.out.slice(0, 200));
  const res = JSON.parse(r.out);
  assert.ok(res.files.some((f) => f.path === "Demo.pbip"), "no Demo.pbip in the result");
  assert.ok(fs.existsSync(path.join(out, "Demo.pbip")), "the project was not written");
  assert.deepStrictEqual(fs.readdirSync(CWD), [], "the working directory was written to");
});

check("a tool's refusal exits 1 and prints the tool's own JSON error", () => {
  const r = run("write_project", argsFile("wp2.json", projectArgs));
  assert.strictEqual(r.code, 1);
  assert.strictEqual(JSON.parse(r.out).ok, false);
  assert.ok(/not empty/.test(JSON.parse(r.out).error), r.out.slice(0, 200));
});

check("a read tool (validate_report) prints exactly the JSON the tool returns", () => {
  const r = run("validate_report", argsFile("v.json", { projectPath: out }));
  assert.strictEqual(r.code, 0, r.err);
  const direct = srv.callTool("validate_report", { projectPath: out });
  assert.strictEqual(r.out, direct.text + "\n");
  assert.strictEqual(JSON.parse(r.out).ok, true);
  // arguments inline work too, and no arguments at all reach the tool as {}
  assert.strictEqual(run("validate_report", JSON.stringify({ projectPath: out })).out, r.out);
  assert.strictEqual(run("validate_report").code, 1);
});

check("the other writing tool (write_measures) edits the project through the script", () => {
  const r = run("write_measures", argsFile("m.json", { projectPath: out, table: "sales",
    measures: [{ name: "Rows", expression: "COUNTROWS ( sales )", formatString: "0" }] }));
  assert.strictEqual(r.code, 0, r.err + r.out.slice(0, 300));
  assert.ok(/Rows/.test(r.out));
  const tmdl = fs.readFileSync(path.join(out, "Demo.SemanticModel", "definition", "tables", "sales.tmdl"), "utf8");
  assert.ok(/measure Rows/.test(tmdl), "the measure is not in the table file");
  assert.strictEqual(JSON.parse(run("validate_report", argsFile("v2.json", { projectPath: out })).out).ok, true);
});

check("a file-reading tool (profile_source) and an encoder run through the script", () => {
  const p = run("profile_source", argsFile("ps.json", { path: src }));
  assert.strictEqual(p.code, 0, p.err);
  assert.ok(JSON.parse(p.out).ok !== false);
  const e = run("encode_table_calculations", argsFile("e.json", { comparisons: ["actual-plan"] }));
  assert.strictEqual(e.code, 0, e.err + e.out.slice(0, 200));
  assert.ok(e.out.includes("actual-plan"));
});

check("an unknown tool fails with code 2, names the tools and prints nothing on stdout", () => {
  const r = run("write_everything", argsFile("u.json", {}));
  assert.strictEqual(r.code, 2);
  assert.strictEqual(r.out, "");
  assert.ok(/unknown tool 'write_everything'/.test(r.err) && r.err.includes("validate_report"), r.err);
});

check("a missing or malformed arguments file fails with code 2 and says which and why", () => {
  const missing = run("validate_report", "@" + path.join(TMP, "nope.json"));
  assert.strictEqual(missing.code, 2);
  assert.ok(/cannot read the arguments file/.test(missing.err) && /nope\.json/.test(missing.err), missing.err);
  const broken = run("validate_report", argsFile("bad.json", "{ \"projectPath\": "));
  assert.strictEqual(broken.code, 2);
  assert.ok(/arguments file .*bad\.json are not valid JSON/.test(broken.err), broken.err);
  assert.strictEqual(broken.out, "");
  const inline = run("validate_report", "{nope");
  assert.strictEqual(inline.code, 2);
  assert.ok(/the arguments are not valid JSON/.test(inline.err), inline.err);
  for (const [n, v] of [["list.json", "[1]"], ["str.json", "\"x\""], ["null.json", "null"]]) {
    const r = run("validate_report", argsFile(n, v));
    assert.strictEqual(r.code, 2, n);
    assert.ok(/must be a JSON object/.test(r.err), r.err);
  }
});

check("a byte order mark at the start of the arguments file is accepted", () => {
  const f = path.join(TMP, "bom.json");
  fs.writeFileSync(f, "﻿" + JSON.stringify({ projectPath: out }));
  assert.strictEqual(run("validate_report", "@" + f).code, 0);
});

check("no tool name, or an extra argument, prints the usage and fails with code 2", () => {
  const none = run();
  assert.strictEqual(none.code, 2);
  assert.ok(/usage: node cli\.js/.test(none.err));
  assert.strictEqual(run("validate_report", "{}", "extra").code, 2);
});

check("the script starts no process and opens no network connection", () => {
  const src = fs.readFileSync(CLI, "utf8");
  assert.ok(!/require\((["'])(node:)?(http|https|http2|net|tls|dgram|dns|child_process|worker_threads|cluster)\1\)|\bfetch\(|\bimport\(/.test(src));
});

fs.rmSync(TMP, { recursive: true, force: true });
for (const [s, n, d] of results) console.log(`  ${s}  ${n}${d ? "  <- " + d : ""}`);
const failed = results.filter((r) => r[0] === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
