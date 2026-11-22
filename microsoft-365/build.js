#!/usr/bin/env node
"use strict";
// Builds the Microsoft 365 Copilot app package from this plugin: the hosted Zebra BI MCP as a
// remote connector and the skills listed in config.json, in one zip.
//
// The build reads skills/, mcp/, plugin.json and mcp.json and never writes to them. It stages a
// copy of the plugin in the output folder and changes only that copy:
//
//   1. Copilot-specific text from overlay/ is applied to the skills, as config.json lists it.
//   2. report-authoring/SKILL.md fits Microsoft's 64,000-character limit (line breaks count
//      twice): the sections config.json names move verbatim to a reference file, each heading
//      kept with a one-line pointer.
//   3. The checker in mcp/ ships inside report-authoring as scripts, because connectors are remote
//      HTTPS MCP servers only: mcp/*.js bundled into one file, with scripts/zbi.js as its front end.
//   4. Hidden files are left out.
//
// The build fails if a skill is still over a limit or an overlay anchor is not found. Then
// Microsoft's converter (atk import openplugin) writes the app manifest, the build sets the name,
// descriptions and icons, and atk package and atk validate build and check the zip.
//
// Usage: node microsoft-365/build.js [--out DIR] [--app-id ID] [--auth-reference-id ID]
// Needs Node 20 or later, and network access for npx to fetch the Agents Toolkit CLI.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const HERE = __dirname;
const ROOT = path.resolve(HERE, "..");
const CONFIG = JSON.parse(fs.readFileSync(path.join(HERE, "config.json"), "utf8"));

class BuildError extends Error {}
const fail = (msg) => { throw new BuildError(msg); };

// Microsoft counts each line break as two characters.
const msLength = (text) => text.length + (text.match(/\n/g) || []).length;

// --- frontmatter -----------------------------------------------------------------------------

function splitFrontmatter(text, skill) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!m) fail(`${skill}: SKILL.md has no frontmatter`);
  return { fm: m[1], body: m[2] };
}

// The description as YAML reads it: a double-quoted string, a folded block (>- or >) or plain.
function descriptionOf(fm) {
  const lines = fm.split("\n");
  const i = lines.findIndex((l) => /^description:/.test(l));
  if (i < 0) return "";
  const rest = lines[i].slice("description:".length).trim();
  if (/^>-?$/.test(rest)) {
    const block = [];
    for (let j = i + 1; j < lines.length && /^\s+\S|^\s*$/.test(lines[j]); j++) block.push(lines[j].trim());
    return block.filter(Boolean).join(" ");
  }
  if (rest.startsWith('"')) return JSON.parse(rest);
  return rest;
}

// --- overlay operations ----------------------------------------------------------------------

function readOverlay(file) {
  return fs.readFileSync(path.join(HERE, file), "utf8").replace(/\r\n/g, "\n").replace(/\n*$/, "\n");
}

function onlyIndex(lines, pred, what, skill) {
  const hits = lines.map((l, i) => (pred(l) ? i : -1)).filter((i) => i >= 0);
  if (hits.length !== 1) fail(`${skill}: overlay anchor ${what} matched ${hits.length} line(s), expected 1`);
  return hits[0];
}

// A section runs from its heading to the next heading of the same or a higher level.
function sectionEnd(lines, start) {
  const level = lines[start].match(/^#+/)[0].length;
  let fence = false;
  for (let j = start + 1; j < lines.length; j++) {
    if (/^```/.test(lines[j])) fence = !fence;
    const h = !fence && lines[j].match(/^(#+) /);
    if (h && h[1].length <= level) return j;
  }
  return lines.length;
}

function applyOverlay(text, ops, skill) {
  for (const op of ops) {
    if (op.op === "drop-text") {
      const n = text.split(op.text).length - 1;
      if (n !== 1) fail(`${skill}: drop-text matched ${n} time(s), expected 1: ${op.text}`);
      text = text.replace(op.text, "");
      continue;
    }
    const lines = text.split("\n");
    if (op.op === "insert-before" || op.op === "insert-after") {
      const i = onlyIndex(lines, (l) => l === op.line, JSON.stringify(op.line), skill);
      const add = readOverlay(op.file).split("\n");
      if (op.op === "insert-before") lines.splice(i, 0, ...add);
      else lines.splice(i + 1, 0, "", ...add.slice(0, -1));
    } else if (op.op === "drop-section" || op.op === "replace-section") {
      const i = onlyIndex(lines, (l) => l === op.heading, JSON.stringify(op.heading), skill);
      const end = sectionEnd(lines, i);
      const add = op.op === "replace-section" ? readOverlay(op.file).split("\n") : [];
      lines.splice(i, end - i, ...add);
    } else if (op.op === "drop-line") {
      const i = onlyIndex(lines, (l) => l.startsWith(op.startsWith), JSON.stringify(op.startsWith), skill);
      lines.splice(i, 1);
    } else if (op.op === "drop-paragraph") {
      const i = onlyIndex(lines, (l) => l.startsWith(op.startsWith), JSON.stringify(op.startsWith), skill);
      let end = i;
      while (end < lines.length && lines[end].trim() !== "") end++;
      lines.splice(i, end - i + 1);
    } else {
      fail(`${skill}: unknown overlay operation ${op.op}`);
    }
    text = lines.join("\n");
  }
  return text.replace(/\n{3,}/g, "\n\n");
}

// --- report-authoring: sections moved to a reference file ------------------------------------

function moveSections(body, move, skill) {
  const lines = body.split("\n");
  const kept = [];
  const moved = [];
  const found = new Set();
  let fence = false;
  for (let i = 0; i < lines.length;) {
    if (/^```/.test(lines[i])) fence = !fence;
    const prefix = !fence && /^#{1,3} /.test(lines[i]) && move.headings.find((h) => lines[i].startsWith(h));
    if (!prefix) { kept.push(lines[i]); i++; continue; }
    found.add(prefix);
    // Everything up to the next heading of level 1 to 3 moves; deeper headings go with it.
    let end = i + 1;
    let f = false;
    for (; end < lines.length; end++) {
      if (/^```/.test(lines[end])) f = !f;
      if (!f && /^#{1,3} /.test(lines[end])) break;
    }
    kept.push(lines[i], "", "Moved verbatim to `" + move.file + "` (Microsoft 365 Copilot limits SKILL.md to 64,000 characters). Read it there when this applies.", "");
    moved.push(...lines.slice(i, end));
    i = end;
  }
  const missing = move.headings.filter((h) => !found.has(h));
  if (missing.length) fail(`${skill}: section(s) to move not found: ${missing.join("; ")}`);
  const keptText = kept.join("\n");
  const movedText = moved.join("\n");
  const lost = lines.filter((l) => l.trim() && !keptText.includes(l) && !movedText.includes(l));
  if (lost.length) fail(`${skill}: ${lost.length} line(s) missing after the move`);
  return { kept: keptText, moved: movedText };
}

// --- the checker as one script ---------------------------------------------------------------

// mcp/ requires only Node built-ins and its own ./x.js files, so a CommonJS wrapper per file is a
// complete bundle. server.js reads rules.json from skills/report-authoring/; in the package the
// bundle sits in the skill's scripts/ folder, so the path points one folder up.
function bundleChecker() {
  const mcp = path.join(ROOT, "mcp");
  const files = fs.readdirSync(mcp).filter((f) => f.endsWith(".js") && !f.startsWith("test-")).sort();
  if (!files.includes("server.js")) fail("mcp/server.js not found");
  const parts = files.map((f) => {
    let src = fs.readFileSync(path.join(mcp, f), "utf8").replace(/^#!.*/, "");
    if (f === "server.js") {
      const re = /__dirname,\s*"\.\.",\s*"skills",\s*"report-authoring",\s*"rules\.json"/;
      if (!re.test(src)) fail("mcp/server.js: the rules.json path was not found; update bundleChecker()");
      src = src.replace(re, '__dirname, "..", "rules.json"');
    }
    for (const m of src.matchAll(/require\("(\.[^"]+)"\)/g)) {
      if (!files.includes(path.basename(m[1]))) fail(`mcp/${f} requires ${m[1]}, which is not in mcp/`);
    }
    return `${JSON.stringify("./" + f)}: function (module, exports, require, __filename, __dirname) {\n${src}\n}`;
  });
  return [
    "#!/usr/bin/env node",
    "// The Zebra BI checker (mcp/ in the Zebra BI plugin) as one file, written by microsoft-365/build.js.",
    "// Distributed under the terms of the Zebra BI Agent Skills Licence.",
    "\"use strict\";",
    `const __modules = {\n${parts.join(",\n")}\n};`,
    "const __cache = {};",
    "function __require(name) {",
    "  if (!(name in __modules)) return require(name);",
    "  if (__cache[name]) return __cache[name].exports;",
    "  const m = name === \"./server.js\" ? module : { exports: {} };",
    "  __cache[name] = m;",
    "  __modules[name].call(m.exports, m, m.exports, __require, __filename, __dirname);",
    "  return m.exports;",
    "}",
    "__require.main = require.main;",
    "__require(\"./server.js\");",
    "",
  ].join("\n");
}

// --- staging ---------------------------------------------------------------------------------

function listFiles(dir, base = dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) listFiles(p, base, out);
    else out.push(path.relative(base, p).split(path.sep).join("/"));
  }
  return out;
}

function copyVisible(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name.startsWith(".")) continue;
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) copyVisible(s, d);
    else fs.copyFileSync(s, d);
  }
}

// Writes the plugin as Microsoft 365 Copilot takes it into `dir`: plugin.json, mcp.json and the
// configured skills with the overlay applied. Returns what it measured, for the test and the log.
function stage(dir) {
  const limits = CONFIG.limits;
  const pluginJson = JSON.parse(fs.readFileSync(path.join(ROOT, "plugin.json"), "utf8"));
  if (/^0\./.test(pluginJson.version)) fail(`plugin version ${pluginJson.version}: a Microsoft 365 app version may not start with 0`);
  const mcpJson = JSON.parse(fs.readFileSync(path.join(ROOT, "mcp.json"), "utf8"));
  const remote = Object.entries(mcpJson.mcpServers || {}).filter(([, s]) => /^https:/.test(s.url || ""));
  if (remote.length !== 1 || remote[0][0] !== CONFIG.app.connector.id) fail(`mcp.json must list exactly one HTTPS server named ${CONFIG.app.connector.id}`);

  const onDisk = fs.readdirSync(path.join(ROOT, "skills")).filter((s) => fs.existsSync(path.join(ROOT, "skills", s, "SKILL.md")));
  const unlisted = onDisk.filter((s) => !(s in CONFIG.skills));
  if (unlisted.length) fail(`skill(s) not in config.json, say whether each goes in the package: ${unlisted.join(", ")}`);
  const included = Object.entries(CONFIG.skills).filter(([, c]) => c.include).map(([s]) => s);
  const excluded = Object.keys(CONFIG.skills).filter((s) => !included.includes(s));
  for (const s of Object.keys(CONFIG.skills)) if (!onDisk.includes(s)) fail(`config.json lists ${s}, which is not in skills/`);

  fs.mkdirSync(path.join(dir, "skills"), { recursive: true });
  fs.writeFileSync(path.join(dir, "plugin.json"), JSON.stringify(pluginJson, null, 2) + "\n");
  // Microsoft 365 takes remote connectors only: the package's mcp.json carries the hosted server and
  // leaves out the local checker, which ships inside report-authoring as scripts.
  const packaged = { ...mcpJson, mcpServers: Object.fromEntries(remote) };
  fs.writeFileSync(path.join(dir, "mcp.json"), JSON.stringify(packaged, null, 2) + "\n");

  const report = { version: pluginJson.version, skills: {} };
  for (const skill of included) {
    const conf = CONFIG.skills[skill];
    const dst = path.join(dir, "skills", skill);
    copyVisible(path.join(ROOT, "skills", skill), dst);
    let text = fs.readFileSync(path.join(dst, "SKILL.md"), "utf8").replace(/\r\n/g, "\n");
    text = applyOverlay(text, conf.overlay || [], skill);
    if (conf.moveSections) {
      const { fm, body } = splitFrontmatter(text, skill);
      const { kept, moved } = moveSections(body, conf.moveSections, skill);
      text = "---\n" + fm + "\n---\n" + kept;
      fs.writeFileSync(path.join(dst, conf.moveSections.file),
        "# Sections moved from SKILL.md\n\nThese sections are unchanged from `SKILL.md` in the Zebra BI plugin " + pluginJson.version
        + ". They are kept in this file because Microsoft 365 Copilot limits SKILL.md to 64,000 characters.\n\n" + moved + "\n");
    }
    if (skill === CONFIG.checker.skill) {
      const scripts = path.join(dst, "scripts");
      fs.mkdirSync(scripts, { recursive: true });
      fs.writeFileSync(path.join(scripts, CONFIG.checker.bundle), bundleChecker());
      fs.copyFileSync(path.join(HERE, CONFIG.checker.frontEnd), path.join(scripts, path.basename(CONFIG.checker.frontEnd)));
    }
    fs.writeFileSync(path.join(dst, "SKILL.md"), text);

    const { fm } = splitFrontmatter(text, skill);
    const description = descriptionOf(fm);
    const files = listFiles(dst);
    const companions = files.filter((f) => f !== "SKILL.md");
    const s = { skillMd: msLength(text), description: description.length, companions: companions.length };
    report.skills[skill] = s;
    if (s.skillMd > limits.skillMdCharacters) fail(`${skill}: SKILL.md is ${s.skillMd} characters by Microsoft's count, over ${limits.skillMdCharacters}`);
    if (s.description > limits.descriptionCharacters) fail(`${skill}: description is ${s.description} characters, over ${limits.descriptionCharacters}`);
    if (s.companions > limits.companionFilesPerSkill) fail(`${skill}: ${s.companions} companion files, over ${limits.companionFilesPerSkill}`);
    const hidden = files.filter((f) => f.split("/").some((p) => p.startsWith(".")));
    if (hidden.length) fail(`${skill}: hidden file(s) ${hidden.join(", ")}`);
    for (const other of excluded) {
      if ((conf.mayName || {})[other]) continue;
      if (new RegExp(`\\b${other}\\b`).test(text)) fail(`${skill}: SKILL.md names ${other}, which this package leaves out`);
    }
  }
  report.included = included;
  report.excluded = excluded;
  return report;
}

// --- the package -----------------------------------------------------------------------------

function atk(args, cwd) {
  return execFileSync("npx", ["--yes", CONFIG.atk, ...args, "--telemetry", "false"],
    { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], shell: process.platform === "win32" });
}

function main(argv) {
  const opt = (name) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : undefined);
  const out = path.resolve(opt("--out") || path.join(HERE, "dist"));
  fs.mkdirSync(out, { recursive: true });
  const work = fs.mkdtempSync(path.join(out, "work-"));
  try {
    const src = path.join(work, "plugin");
    const measured = stage(src);
    for (const [s, m] of Object.entries(measured.skills)) {
      console.log(`${s}: SKILL.md ${m.skillMd}/${CONFIG.limits.skillMdCharacters}, description ${m.description}/${CONFIG.limits.descriptionCharacters}, companion files ${m.companions}/${CONFIG.limits.companionFilesPerSkill}`);
    }
    console.log(`left out: ${measured.excluded.join(", ")}`);

    const proj = path.join(work, "project");
    const importArgs = ["import", "openplugin", "--path", src, "--output", proj,
      "--privacy-url", CONFIG.app.privacyUrl, "--terms-url", CONFIG.app.termsUrl,
      "--default-auth-type", "OAuthPluginVault"];
    if (opt("--app-id")) importArgs.push("--app-id", opt("--app-id"));
    process.stdout.write(atk(importArgs, work));

    const app = path.join(proj, "appPackage");
    const manifestPath = path.join(app, "manifest.json");
    const m = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    const pluginJson = JSON.parse(fs.readFileSync(path.join(ROOT, "plugin.json"), "utf8"));
    const long = pluginJson.extensions?.["com.openai"]?.interface?.longDescription;
    if (!long) fail("plugin.json has no extensions.com.openai.interface.longDescription to describe the app with");
    m.version = pluginJson.version;
    m.name = { short: CONFIG.app.name, full: CONFIG.app.name };
    m.description = { short: CONFIG.app.shortDescription, full: long + "\n\n" + CONFIG.app.fullDescriptionAddition };
    m.accentColor = CONFIG.app.accentColor;
    m.agentSkills = measured.included.map((s) => ({ folder: `./skills/${s}` }));
    const conns = m.agentConnectors || [];
    const conn = conns.find((c) => c.id === CONFIG.app.connector.id);
    if (!conn || conns.length !== 1) fail(`the converter wrote ${conns.length} connector(s); expected one, ${CONFIG.app.connector.id}`);
    conn.displayName = CONFIG.app.connector.displayName;
    conn.description = CONFIG.app.connector.description;
    if (opt("--auth-reference-id")) conn.toolSource.remoteMcpServer.authorization.referenceId = opt("--auth-reference-id");
    fs.writeFileSync(manifestPath, JSON.stringify(m, null, 4) + "\n");
    fs.copyFileSync(path.join(HERE, "icons", "color.png"), path.join(app, "color.png"));
    fs.copyFileSync(path.join(HERE, "icons", "outline.png"), path.join(app, "outline.png"));

    const zip = path.join(out, CONFIG.output);
    fs.rmSync(zip, { force: true });
    process.stdout.write(atk(["package", "--manifest-file", manifestPath, "--output-package-file", zip,
      "--output-folder", path.join(app, "build"), "--env", "dev"], proj));
    const validation = atk(["validate", "--package-file", zip], proj);
    fs.writeFileSync(path.join(out, "validate.log"), validation);
    process.stdout.write(validation);
    if (/\(×\)|\b[1-9]\d* failed/.test(validation) || !/\d+ passed/.test(validation)) fail(`atk validate reported a failure; see ${path.join(out, "validate.log")}`);
    console.log(`\nWrote ${zip} (app version ${m.version}, connector auth reference ${conn.toolSource.remoteMcpServer.authorization.referenceId})`);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

module.exports = { stage, applyOverlay, moveSections, bundleChecker, descriptionOf, msLength, CONFIG };

if (require.main === module) {
  try { main(process.argv.slice(2)); } catch (e) {
    const detail = e instanceof BuildError ? e.message : (e.stderr || e.stdout || e.message);
    console.error(`microsoft-365/build.js: ${String(detail).trim()}`);
    process.exit(1);
  }
}
