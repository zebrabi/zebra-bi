#!/usr/bin/env node
"use strict";
// Tests for microsoft-365/build.js. Offline: it runs the staging step, which reads the plugin and
// writes the package's skills to a temporary folder, and never calls the Agents Toolkit CLI.
//
//   node microsoft-365/test-build.js

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const build = require("./build.js");
const { CONFIG } = build;
const ROOT = path.resolve(__dirname, "..");

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(["PASS", name]); }
  catch (e) { results.push(["FAIL", name + ": " + String(e.message).split("\n")[0].slice(0, 160)]); }
};

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "zbi-m365-test-"));
const dir = path.join(tmp, "plugin");
let staged;
const read = (rel) => fs.readFileSync(path.join(dir, rel), "utf8");
const walk = (d, out = []) => {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p, out); else out.push(path.relative(dir, p).split(path.sep).join("/"));
  }
  return out;
};

try {
  check("the plugin stages without error", () => { staged = build.stage(dir); });

  check("every skill in skills/ is in config.json with a reason", () => {
    for (const s of fs.readdirSync(path.join(ROOT, "skills"))) {
      assert.ok(CONFIG.skills[s], `${s} is not in config.json`);
      assert.ok(CONFIG.skills[s].reason, `${s} has no reason`);
    }
  });

  check("the package holds exactly the included skills", () => {
    const want = Object.keys(CONFIG.skills).filter((s) => CONFIG.skills[s].include).sort();
    assert.deepStrictEqual(fs.readdirSync(path.join(dir, "skills")).sort(), want);
  });

  check("every skill is within Microsoft's limits", () => {
    for (const s of staged.included) {
      const text = read(`skills/${s}/SKILL.md`);
      const ms = build.msLength(text);
      assert.ok(ms <= CONFIG.limits.skillMdCharacters, `${s}: ${ms} characters`);
      const d = build.descriptionOf(text.match(/^---\n([\s\S]*?)\n---/)[1]);
      assert.ok(d.length > 0 && d.length <= CONFIG.limits.descriptionCharacters, `${s}: description ${d.length}`);
      const files = walk(path.join(dir, "skills", s)).length;
      assert.ok(files - 1 <= CONFIG.limits.companionFilesPerSkill, `${s}: ${files - 1} companion files`);
    }
  });

  check("no hidden files", () => {
    const hidden = walk(dir).filter((f) => f.split("/").some((p) => p.startsWith(".")));
    assert.deepStrictEqual(hidden, []);
  });

  check("the source report-authoring/SKILL.md is over the limit, so the move is exercised", () => {
    const src = fs.readFileSync(path.join(ROOT, "skills", "report-authoring", "SKILL.md"), "utf8");
    assert.ok(build.msLength(src) > CONFIG.limits.skillMdCharacters);
  });

  check("moved sections keep every line of the source", () => {
    const conf = CONFIG.skills["report-authoring"].moveSections;
    const moved = read(`skills/report-authoring/${conf.file}`);
    const kept = read("skills/report-authoring/SKILL.md");
    for (const h of conf.headings) {
      assert.ok(moved.split("\n").some((l) => l.startsWith(h)), `${h} not in the moved file`);
      assert.ok(kept.split("\n").some((l) => l.startsWith(h)), `${h} heading not kept in SKILL.md`);
    }
    const src = fs.readFileSync(path.join(ROOT, "skills", "report-authoring", "SKILL.md"), "utf8");
    for (const line of src.split("\n")) {
      if (line.trim()) assert.ok(kept.includes(line) || moved.includes(line), `line lost: ${line.slice(0, 60)}`);
    }
  });

  check("every overlay is applied", () => {
    for (const [s, conf] of Object.entries(CONFIG.skills)) {
      if (!conf.include) continue;
      const text = read(`skills/${s}/SKILL.md`);
      for (const op of conf.overlay || []) {
        if (op.file) {
          const body = fs.readFileSync(path.join(__dirname, op.file), "utf8").trim();
          assert.ok(text.includes(body), `${s}: ${op.file} not applied`);
        }
        if (op.op === "drop-section" || op.op === "replace-section") {
          assert.ok(!text.split("\n").includes(op.heading), `${s}: ${op.heading} still present`);
        }
        if (op.op === "drop-text") assert.ok(!text.includes(op.text), `${s}: text still present`);
        if (op.op === "drop-line" || op.op === "drop-paragraph") {
          assert.ok(!text.split("\n").some((l) => l.startsWith(op.startsWith)), `${s}: ${op.startsWith} still present`);
        }
      }
    }
  });

  check("outside the overlay, every packaged file is the plugin's own", () => {
    for (const f of walk(path.join(dir, "skills"))) {
      const rel = `skills/${f}`;
      const src = path.join(ROOT, rel);
      if (!fs.existsSync(src) || f.endsWith("/SKILL.md")) continue;
      assert.ok(fs.readFileSync(src).equals(fs.readFileSync(path.join(dir, rel))), `${rel} differs`);
    }
    for (const s of staged.included) {
      const conf = CONFIG.skills[s];
      if ((conf.overlay || []).length || conf.moveSections) continue;
      assert.strictEqual(read(`skills/${s}/SKILL.md`), fs.readFileSync(path.join(ROOT, "skills", s, "SKILL.md"), "utf8"), `${s}/SKILL.md differs`);
    }
  });

  check("an overlay anchor that is missing fails the build", () => {
    assert.throws(() => build.applyOverlay("# T\n\nbody\n", [{ op: "drop-section", heading: "## Gone" }], "t"), /matched 0 line/);
    assert.throws(() => build.applyOverlay("# T\n", [{ op: "drop-text", text: "absent" }], "t"), /matched 0 time/);
  });

  check("a section to move that is missing fails the build", () => {
    assert.throws(() => build.moveSections("# T\n\n## Kept\n\ntext\n", { file: "references/x.md", headings: ["## Gone"] }, "t"), /not found/);
  });

  const scripts = path.join(dir, "skills", CONFIG.checker.skill, "scripts");
  check("the checker scripts are present", () => {
    assert.ok(fs.existsSync(path.join(scripts, "zbi.js")));
    assert.ok(fs.existsSync(path.join(scripts, CONFIG.checker.bundle)));
  });

  check("the bundled checker lists its tools with node", () => {
    const r = spawnSync(process.execPath, [path.join(scripts, "zbi.js"), "list"], { encoding: "utf8", timeout: 30000 });
    assert.strictEqual(r.status, 0, r.stderr);
    const tools = r.stdout.trim().split("\n").map((l) => l.split(" - ")[0]);
    for (const t of ["validate_report", "write_report", "profile_source"]) assert.ok(tools.includes(t), `${t} missing`);
  });

  check("the bundled checker loads its rules and checks a project", () => {
    const proj = path.join(tmp, "project");
    fs.mkdirSync(path.join(proj, "Demo.Report", "definition"), { recursive: true });
    fs.writeFileSync(path.join(proj, "Demo.pbip"), "{}");
    fs.writeFileSync(path.join(proj, "Demo.Report", "definition", "report.json"), "{}");
    const r = spawnSync(process.execPath, [path.join(scripts, "zbi.js"), "validate_report", JSON.stringify({ projectPath: proj })], { encoding: "utf8", timeout: 30000 });
    const out = JSON.parse(r.stdout);
    const rules = JSON.parse(fs.readFileSync(path.join(ROOT, "skills", "report-authoring", "rules.json"), "utf8")).rules;
    assert.strictEqual(out.rulesRun, rules.length, `rulesRun ${out.rulesRun}, catalogue ${rules.length}`);
    assert.ok(out.findings.length > 0, "a project with no version.json must have findings");
  });

  check("the package's mcp.json carries the hosted server only, whatever mcp.json lists besides it", () => {
    const packaged = JSON.parse(read("mcp.json"));
    assert.deepStrictEqual(Object.keys(packaged.mcpServers), [CONFIG.app.connector.id]);
    assert.ok(/^https:/.test(packaged.mcpServers[CONFIG.app.connector.id].url), "the connector is not an HTTPS server");
    assert.strictEqual(packaged.$schema, JSON.parse(fs.readFileSync(path.join(ROOT, "mcp.json"), "utf8")).$schema);
  });

  check("the plugin's own files are unchanged by the build", () => {
    const r = spawnSync("git", ["status", "--porcelain", "--", "skills", "mcp", "plugin.json", "mcp.json"], { cwd: ROOT, encoding: "utf8" });
    if (r.status === 0) assert.strictEqual(r.stdout.trim(), "");
  });
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}

for (const [s, n] of results) console.log(`  ${s}  ${n}`);
const failed = results.filter(([s]) => s === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
