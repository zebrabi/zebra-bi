// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * write_measures: add measures to an existing TMDL model, with the DAX the caller's and the TMDL
 * around it this tool's.
 *
 * A measure is where a hand-edited model most often breaks: a body at property depth swallows the
 * formatString after it, DAX started on the `=` line ends the measure early, a measure shares a
 * column's name, an unquoted reserved table name (`Calculations`, `KPI`) is a syntax error, a VAR is
 * named after a DAX function. This module owns everything except the expression itself:
 *
 *   - the layout: `measure <name> =` alone on its line, the body one level below the properties,
 *     formatString, displayFolder and a `///` description in their places;
 *   - the names: a measure name may not collide with a column or measure of its table or with a
 *     measure anywhere in the model;
 *   - the references: every `Table[Column]`, `'Table'[Column]` and `[Measure]` outside string
 *     literals and comments must resolve in the model, and a bare table reference is single-quoted;
 *   - the validator: every rule runs on the model before and after the edit, in memory, and a rule
 *     that gains a finding refuses the write.
 *
 * The write is the narrowest one possible. It changes one table file: it inserts the new measure
 * blocks at the end of the table's block, or replaces a measure of the same name when `replace` is
 * true. Every other byte of that file is kept, which is checked before the file is written, and no
 * other file is touched. The exact text added (and removed) is returned.
 */

const fs = require("fs");
const path = require("path");
const enc = require("./encoders.js");
const tmdl = require("./tmdl.js");

const { EncodeError } = enc;
const T = tmdl.TREE;
const need = (cond, msg) => { if (!cond) throw new EncodeError(msg); };

const INPUT_KEYS = new Set(["projectPath", "table", "measures", "replace"]);
const MEASURE_KEYS = new Set(["name", "expression", "formatString", "description", "displayFolder"]);
const KEYWORDS = new Set(["var", "return", "true", "false", "in", "and", "or", "not", "asc", "desc",
  "define", "evaluate", "measure", "order", "by", "start", "at", "column", "table"]);

const tmdlName = (n) => (/^[A-Za-z_][A-Za-z0-9_]*$/.test(n) ? n : `'${n.replace(/'/g, "''")}'`);
const daxTable = (n) => `'${n.replace(/'/g, "''")}'`;

// --- reading DAX ----------------------------------------------------------------------------

/**
 * Splits a DAX expression into tokens: string literals, comments, quoted table names, bracketed
 * names, identifiers, numbers and single characters. Only what is outside strings and comments is
 * ever read as a reference.
 */
function tokenize(expr) {
  const out = [];
  let i = 0;
  const n = expr.length;
  const take = (type, end) => { out.push({ type, start: i, end, text: expr.slice(i, end) }); i = end; };
  while (i < n) {
    const ch = expr[i];
    if (ch === "\"") {
      let j = i + 1;
      for (;;) {
        need(j < n, "the expression has a double-quoted string that is never closed.");
        if (expr[j] === "\"") { if (expr[j + 1] === "\"") { j += 2; continue; } break; }
        need(expr[j] !== "\n" && expr[j] !== "\r", "the expression has a string literal that runs over "
          + "a line break. Keep each string on one line; the TMDL indentation would otherwise enter it.");
        j++;
      }
      take("string", j + 1);
    } else if ((ch === "/" && expr[i + 1] === "/") || (ch === "-" && expr[i + 1] === "-")) {
      let j = i; while (j < n && expr[j] !== "\n" && expr[j] !== "\r") j++;
      take("comment", j);
    } else if (ch === "/" && expr[i + 1] === "*") {
      const j = expr.indexOf("*/", i + 2);
      need(j >= 0, "the expression has a /* comment that is never closed.");
      take("comment", j + 2);
    } else if (ch === "'") {
      let j = i + 1;
      for (;;) {
        need(j < n && expr[j] !== "\n", "the expression has a single-quoted table name that is never closed.");
        if (expr[j] === "'") { if (expr[j + 1] === "'") { j += 2; continue; } break; }
        j++;
      }
      take("qtable", j + 1);
    } else if (ch === "[") {
      let j = i + 1;
      for (;;) {
        need(j < n && expr[j] !== "\n", "the expression has a [ that is never closed.");
        if (expr[j] === "]") { if (expr[j + 1] === "]") { j += 2; continue; } break; }
        j++;
      }
      take("bracket", j + 1);
    } else if (/[A-Za-z_]/.test(ch)) {
      let j = i; while (j < n && /[A-Za-z0-9_]/.test(expr[j])) j++;
      take("ident", j);
    } else if (/[0-9]/.test(ch)) {
      let j = i; while (j < n && /[0-9A-Za-z_.]/.test(expr[j])) j++;
      take("number", j);
    } else if (/\s/.test(ch)) {
      let j = i; while (j < n && /\s/.test(expr[j])) j++;
      take("space", j);
    } else {
      take("char", i + 1);
    }
  }
  return out;
}

const bracketName = (t) => t.text.slice(1, -1).replace(/]]/g, "]");
const quotedName = (t) => t.text.slice(1, -1).replace(/''/g, "'");

/** Edit distance, for "did you mean". */
function distance(a, b) {
  a = a.toLowerCase(); b = b.toLowerCase();
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
  }
  return d[a.length][b.length];
}
const nearest = (name, pool) => [...new Set(pool)]
  .map((p) => [p, distance(name, p)]).sort((x, y) => x[1] - y[1] || x[0].localeCompare(y[0]))
  .slice(0, 3).map(([p]) => p);

/**
 * Resolves every reference in one expression against the model, and returns the expression with
 * each bare table reference single-quoted, plus a note per quoted name.
 */
function resolveReferences(measureName, expr, index, batch) {
  const toks = tokenize(expr);
  const significant = (k, dir) => { let j = k + dir; while (toks[j] && (toks[j].type === "space" || toks[j].type === "comment")) j += dir; return toks[j]; };
  const varNames = new Set();
  toks.forEach((t, k) => {
    if (t.type === "ident" && t.text.toLowerCase() === "var") {
      const nx = significant(k, 1);
      if (nx && nx.type === "ident") varNames.add(nx.text.toLowerCase());
    }
  });
  const introduced = new Set(toks.filter((t) => t.type === "string").map((t) => t.text.slice(1, -1).replace(/""/g, "\"").toLowerCase()));
  const problems = [];
  const quoted = new Map();
  const edits = [];
  const columnTypes = [];
  const tableNames = [...index.tables.values()].map((t) => t.name);
  const allMeasures = [...index.measures.values()].map((m) => m.name).concat(batch);
  const allColumns = [...index.tables.values()].flatMap((t) => [...t.columns]);

  for (let k = 0; k < toks.length; k++) {
    const t = toks[k];
    if (t.type === "qtable" || t.type === "ident") {
      const nx = significant(k, 1);
      if (t.type === "ident" && nx && nx.type === "char" && nx.text === "(") continue;      // a function
      const prev = significant(k, -1);
      if (t.type === "ident" && prev && prev.type === "char" && prev.text === ".") continue;
      const name = t.type === "qtable" ? quotedName(t) : t.text;
      const followedByColumn = nx && nx.type === "bracket";
      if (t.type === "ident" && !followedByColumn) {
        // A bare word: a table reference only when it names a table and is not a variable or keyword.
        if (varNames.has(name.toLowerCase()) || KEYWORDS.has(name.toLowerCase())) continue;
        if (!index.tables.has(name.toLowerCase())) continue;
      }
      const table = index.tables.get(name.toLowerCase());
      if (!table) {
        problems.push(`${t.type === "qtable" ? t.text : name}${followedByColumn ? nx.text : ""}: no table '${name}' in the model (nearest: ${nearest(name, tableNames).join(", ")})`);
        if (followedByColumn) k = toks.indexOf(nx);
        continue;
      }
      if (t.type === "ident") {
        edits.push({ start: t.start, end: t.end, text: daxTable(table.name) });
        quoted.set(name, daxTable(table.name));
      }
      if (followedByColumn) {
        const col = bracketName(nx);
        const own = batch.some((b) => b.toLowerCase() === col.toLowerCase()) && table.name.toLowerCase() === index.home.toLowerCase();
        if (table.columns.has(col.toLowerCase())) columnTypes.push(table.types.get(col.toLowerCase()));
        else if (table.measures.has(col.toLowerCase()) || own) columnTypes.push("measure");
        if (!table.columns.has(col.toLowerCase()) && !table.measures.has(col.toLowerCase()) && !own) {
          problems.push(`${daxTable(table.name)}${nx.text}: ${table.name} has no column or measure '${col}' (nearest: ${nearest(col, [...table.columnNames, ...table.measureNames]).join(", ")})`);
        }
        k = toks.indexOf(nx);
      }
      continue;
    }
    if (t.type === "bracket") {
      const name = bracketName(t);
      if (name.toLowerCase() === measureName.toLowerCase()) { problems.push(`[${name}]: the measure refers to itself`); continue; }
      const ok = index.measures.has(name.toLowerCase()) || batch.some((b) => b.toLowerCase() === name.toLowerCase())
        || allColumns.includes(name.toLowerCase()) || introduced.has(name.toLowerCase());
      if (!ok) problems.push(`[${name}]: no measure or column '${name}' in the model (nearest: ${nearest(name, [...allMeasures, ...[...index.tables.values()].flatMap((x) => x.columnNames)]).join(", ")})`);
    }
  }
  let out = expr;
  for (const e of edits.sort((a, b) => b.start - a.start)) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  // A date-valued measure: an aggregate that returns a date (MAX, MIN, LASTDATE, ...) whose first
  // argument is a dateTime column, and nothing in the expression that turns the date into a number or
  // text. Its format must be a date format, or the date shows as a serial number.
  const AGG = new Set(["max", "min", "lastdate", "firstdate", "lastnonblank", "firstnonblank", "eomonth", "edate"]);
  const NOT_A_DATE = /\b(FORMAT|DATEDIFF|YEAR|MONTH|DAY|QUARTER|WEEKNUM|COUNT|COUNTA|COUNTROWS|DISTINCTCOUNT|INT|VALUE|CONCATENATE|COMBINEVALUES|IF|SWITCH|DIVIDE)\s*\(/i;
  let dateValued = false;
  for (let k = 0; k < toks.length; k++) {
    const t = toks[k];
    if (t.type !== "ident" || !AGG.has(t.text.toLowerCase())) continue;
    const open = significant(k, 1);
    if (!open || open.text !== "(") continue;
    const arg = significant(toks.indexOf(open), 1);
    if (!arg || (arg.type !== "qtable" && arg.type !== "ident")) continue;
    const br = significant(toks.indexOf(arg), 1);
    if (!br || br.type !== "bracket") continue;
    const tb = index.tables.get((arg.type === "qtable" ? quotedName(arg) : arg.text).toLowerCase());
    if (tb && tb.types.get(bracketName(br).toLowerCase()) === "datetime") dateValued = true;
  }
  const code = toks.filter((t) => t.type !== "string" && t.type !== "comment").map((t) => t.text).join("");
  if (NOT_A_DATE.test(code)) dateValued = false;
  return { expression: out, problems, dateValued, quoted: [...quoted.entries()].map(([from, to]) => `${from} -> ${to}`) };
}

// --- the model -------------------------------------------------------------------------------

function modelIndex(model, home) {
  const tables = new Map();
  const measures = new Map();
  for (const t of model.tables) {
    const k = t.name.toLowerCase();
    if (!tables.has(k)) tables.set(k, { name: t.name, columns: new Set(), measures: new Set(), columnNames: [], measureNames: [], types: new Map() });
    const e = tables.get(k);
    for (const c of t.columns) { e.columns.add(c.name.toLowerCase()); e.columnNames.push(c.name); e.types.set(c.name.toLowerCase(), String(c.dataType || "").toLowerCase()); }
    for (const m of t.measures) {
      e.measures.add(m.name.toLowerCase()); e.measureNames.push(m.name);
      measures.set(m.name.toLowerCase(), { name: m.name, table: t.name, file: t.file });
    }
  }
  return { tables, measures, home };
}

/** The model with one source's text replaced, parsed the way tmdl.readModel parses a file. */
function withSource(model, rel, text) {
  const sources = model.sources.map((s) => (s.rel === rel
    ? { rel, hadBom: s.hadBom, text, lines: tmdl.classifyLines(text), tree: T.parseTree(text) } : s));
  const tables = [];
  for (const s of sources) for (const tb of tmdl.parseTmdl(s.text).tables) tables.push({ ...tb, file: s.rel });
  return { ...model, sources, tables };
}

/** Line start offsets of a text, and the text split into lines without their line endings. */
function lineMap(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i++) if (text[i] === "\n") starts.push(i + 1);
  const lines = text.split(/\r?\n/);
  return { starts, lines };
}

/** Index (0-based) of the last non-blank line before 0-based line `stop`, from `from`. */
function lastNonBlank(lines, from, stop) {
  let e = stop - 1;
  while (e > from && !lines[e].trim()) e--;
  return e;
}

/** First line (0-based) of a node, counting the `///` description above it. */
const startOf = (node) => (node.desc && node.desc.length && node.descLine >= 0 ? node.descLine : node.line - 1);

function layoutBlock(m, unit, eol) {
  const L1 = unit, L2 = unit + unit, L3 = unit + unit + unit;
  const raw = m.expression.replace(/\r\n?/g, "\n").split("\n").map((l) => l.replace(/[ \t]+$/, ""));
  while (raw.length && !raw[0].trim()) raw.shift();
  while (raw.length && !raw[raw.length - 1].trim()) raw.pop();
  const body = raw.filter((l) => l.trim());
  const lead = (l) => /^[ \t]*/.exec(l)[0].length;
  const rest = body.slice(1);
  const min = rest.length ? Math.min(...rest.map(lead)) : 0;
  const lines = [];
  if (m.description) for (const d of m.description.split(/\r?\n/)) lines.push(`${L1}/// ${d.trim()}`.replace(/ +$/, ""));
  lines.push(`${L1}measure ${tmdlName(m.name)} =`);
  lines.push(`${L3}${body[0].trim()}`);
  for (const l of rest) lines.push(`${L3}${l.slice(min)}`);
  lines.push(`${L2}formatString: ${m.formatString}`);
  if (m.displayFolder) lines.push(`${L2}displayFolder: ${m.displayFolder}`);
  return lines.join(eol);
}

// --- the tool ------------------------------------------------------------------------------

function writeMeasures(args = {}, deps = {}) {
  need(args && typeof args === "object" && !Array.isArray(args), "write_measures takes an object of arguments.");
  const unknown = Object.keys(args).filter((k) => !INPUT_KEYS.has(k));
  need(!unknown.length, `unknown write_measures field(s): ${unknown.join(", ")}. Known fields: ${[...INPUT_KEYS].join(", ")}.`);
  const { projectPath, table } = args;
  need(typeof projectPath === "string" && path.isAbsolute(projectPath) && fs.existsSync(projectPath),
    "projectPath is required: the absolute path of the folder that holds the .pbip.");
  const smDirs = fs.readdirSync(projectPath, { withFileTypes: true }).filter((e) => e.isDirectory() && e.name.endsWith(".SemanticModel"));
  need(smDirs.length === 1, `projectPath must hold exactly one .SemanticModel folder; ${projectPath} holds ${smDirs.length}.`);
  need(typeof table === "string" && table.trim(), "table is required: the name of the table the measures live on.");
  need(Array.isArray(args.measures) && args.measures.length, "measures is required: a non-empty array of {name, expression, formatString}.");
  need(args.replace === undefined || typeof args.replace === "boolean", "replace must be true or false.");
  const replace = args.replace === true;

  const model = tmdl.readModel(projectPath);
  need(model.present && model.tables.length, `no TMDL tables could be read under ${projectPath}.`);
  const index = modelIndex(model, table);
  const home = index.tables.get(table.toLowerCase());
  need(home, `table '${table}' is not in the model. Tables: ${[...index.tables.values()].map((t) => t.name).join(", ")}.`);
  index.home = home.name;

  // The measures, each checked on its own.
  const seen = new Set();
  const specs = args.measures.map((m, i) => {
    need(m && typeof m === "object", `measures[${i}] must be an object.`);
    const bad = Object.keys(m).filter((k) => !MEASURE_KEYS.has(k));
    need(!bad.length, `measures[${i}]: unknown field(s) ${bad.join(", ")}. Known: ${[...MEASURE_KEYS].join(", ")}.`);
    need(typeof m.name === "string" && m.name.trim() === m.name && m.name.length && !/[\u0000-\u001f\u007f]/.test(m.name),
      `measures[${i}].name is required, with no leading or trailing space and no control character.`);
    need(typeof m.expression === "string" && m.expression.trim(), `measures[${i}] (${m.name}): expression is required.`);
    need(!m.expression.includes("```"), `measures[${i}] (${m.name}): the expression holds a TMDL fence (\`\`\`); give the DAX only.`);
    need(typeof m.formatString === "string" && m.formatString.trim() && !/[\r\n]/.test(m.formatString),
      `measures[${i}] (${m.name}): formatString is required, on one line, for example '#,0' or '0.0%'. Charts and tables read their number format from it.`);
    if (m.displayFolder !== undefined) need(typeof m.displayFolder === "string" && !/[\r\n]/.test(m.displayFolder), `measures[${i}] (${m.name}): displayFolder must be one line.`);
    if (m.description !== undefined) need(typeof m.description === "string", `measures[${i}] (${m.name}): description must be a string.`);
    const k = m.name.toLowerCase();
    need(!seen.has(k), `two measures in this call are named '${m.name}'.`);
    seen.add(k);
    need(!home.columns.has(k), `'${m.name}' is a column of ${home.name}; a measure and a column cannot share a name in one table. Choose another name.`);
    const other = index.measures.get(k);
    if (other) {
      need(other.table.toLowerCase() === home.name.toLowerCase(),
        `a measure named '${other.name}' already exists on ${other.table}; measure names are unique across the model.`);
      need(replace, `${home.name} already has a measure named '${other.name}'. Pass replace: true to replace it, or choose another name.`);
    }
    return { ...m, existing: other || null };
  });

  // References.
  const quoted = [];
  const problems = [];
  const formats = [];
  const batch = specs.map((s) => s.name);
  for (const s of specs) {
    const r = resolveReferences(s.name, s.expression, index, batch.filter((b) => b !== s.name));
    s.expression = r.expression;
    if (r.dateValued) {
      const f = s.formatString.replace(/"[^"]*"/g, "");
      if (!(/[dMy]/.test(f) || /^(short|long|general) date$/i.test(s.formatString.trim()))) {
        formats.push(`${s.name} returns a date, so its formatString must be a date format such as yyyy-MM-dd, mmm yyyy or Short Date, not '${s.formatString}'`);
      }
    }
    for (const q of r.quoted) quoted.push(`${s.name}: ${q}`);
    for (const p of r.problems) problems.push(`${s.name}: ${p}`);
  }
  need(!problems.length, `the expression(s) name things the model does not have, so the measure would `
    + `return an error: ${problems.join("; ")}.`);
  need(!formats.length, `${formats.join("; ")}. A date under a number format shows as a serial number.`);

  // The one file.
  const homeSources = model.sources.filter((s) => s.tree.root.children.some((n) => n.type === "table" && n.name.toLowerCase() === home.name.toLowerCase()));
  const replacing = specs.filter((s) => s.existing);
  const replFiles = [...new Set(replacing.map((s) => s.existing.file))];
  need(replFiles.length <= 1, `the measures to replace live in different files (${replFiles.join(", ")}); replace them in separate calls.`);
  const src = replFiles.length ? model.sources.find((s) => s.rel === replFiles[0])
    : (homeSources.find((s) => /\/tables\//.test(s.rel)) || homeSources[0]);
  need(src, `no file declares table ${home.name}.`);
  const abs = path.join(projectPath, ...src.rel.split("/"));
  const buf = fs.readFileSync(abs);
  const original = buf.toString("utf8");
  need(Buffer.from(original, "utf8").equals(buf), `${src.rel} is not valid UTF-8, so it cannot be edited without changing other bytes.`);
  const eol = /\r\n/.test(original) ? "\r\n" : "\n";
  const text = original;
  const tree = T.parseTree(text.replace(/^﻿/, ""));
  const node = tree.root.children.find((n) => n.type === "table" && n.name.toLowerCase() === home.name.toLowerCase());
  const { starts, lines } = lineMap(text);
  const firstChild = node.children[0] || node.props[0];
  const unit = firstChild ? (/^[\t ]*/.exec(lines[(firstChild.line || 1) - 1])[0] || "\t") : "\t";
  const rootIdx = tree.root.children.indexOf(node);
  const next = tree.root.children[rootIdx + 1];
  const tableEnd = lastNonBlank(lines, node.line - 1, next ? startOf(next) : lines.length);
  const lineEnd = (i) => (i + 1 < starts.length ? starts[i + 1] : text.length);

  // Replacements first (spans inside the table block), then one insertion at the end of the block.
  const edits = [];
  const removed = [];
  for (const s of replacing) {
    const m = node.children.find((c) => c.type === "measure" && c.name.toLowerCase() === s.name.toLowerCase());
    need(m, `the measure '${s.name}' to replace is declared in another file than ${src.rel}.`);
    const sibling = node.children[node.children.indexOf(m) + 1];
    const from = startOf(m);
    const to = lastNonBlank(lines, m.line - 1, sibling ? startOf(sibling) : tableEnd + 1);
    const a = starts[from];
    const b = to + 1 < starts.length ? starts[to + 1] - (text[starts[to + 1] - 2] === "\r" ? 2 : 1) : text.length;
    const block = layoutBlock(s, unit, eol);
    edits.push({ a, b, text: block });
    removed.push({ name: s.name, removed: text.slice(a, b), added: block });
  }
  const appended = specs.filter((s) => !s.existing);
  if (appended.length) {
    const at = lineEnd(tableEnd);
    const endsWithEol = at > 0 && text[at - 1] === "\n";
    const blocks = appended.map((s) => layoutBlock(s, unit, eol)).join(eol + eol);
    edits.push({ a: at, b: at, text: `${endsWithEol ? "" : eol}${eol}${blocks}${eol}` });
  }
  edits.sort((x, y) => x.a - y.a);
  let out = "";
  let at = 0;
  for (const e of edits) { out += text.slice(at, e.a) + e.text; at = e.b; }
  out += text.slice(at);

  // Every byte outside the edits is kept: rebuild the original from the output and compare.
  let back = "", o = 0;
  at = 0;
  for (const e of edits) {
    const keep = e.a - at;
    back += out.slice(o, o + keep) + text.slice(e.a, e.b);
    o += keep + e.text.length;
    at = e.b;
  }
  back += out.slice(o);
  need(Buffer.from(back, "utf8").equals(buf), "internal check failed: the edit would change bytes outside the measure blocks. Nothing was written.");

  // Every rule, before and after, in memory. A rule that gains a finding refuses the write.
  const resolveProject = deps.resolveProject || require("./server.js").resolveProject;
  const loadRules = deps.loadRules || require("./server.js").loadRules;
  const { checkers } = deps.checkers ? { checkers: deps.checkers } : require("./checkers.js");
  const rules = loadRules() || [];
  const before = resolveProject(projectPath);
  const after = { ...before, model: withSource(before.model, src.rel, out.replace(/^﻿/, "")) };
  const gained = [];
  for (const rule of rules) {
    const fn = checkers[rule.check];
    if (!fn) continue;
    const a = fn(before).length, b = fn(after).length;
    if (b > a) gained.push(`${rule.id}: ${rule.message}${fn(after).filter((h) => h.detail).map((h) => ` (${h.detail})`).slice(0, 2).join("")}`);
  }
  need(!gained.length, `the measures would add ${gained.length} validator finding(s), so nothing was written: ${gained.join(" | ")}`);

  fs.writeFileSync(abs, Buffer.from(out, "utf8"));

  const validateReport = deps.validateReport || require("./server.js").validateReport;
  const v = validateReport({ projectPath });
  const findings = v.ok ? v.findings : [{ code: "validation-did-not-run", severity: "wont-open", location: projectPath, message: v.error }];
  const sev = (s) => findings.filter((f) => f.severity === s).length;
  const addedEdit = edits.find((e) => e.a === e.b);
  return {
    ok: true,
    file: path.relative(projectPath, abs).split(path.sep).join("/"),
    measures: specs.map((s) => `${home.name}[${s.name}]`),
    added: addedEdit ? addedEdit.text : "",
    ...(removed.length ? { replaced: removed } : {}),
    quoted,
    lineEnding: eol === "\r\n" ? "CRLF" : "LF",
    validation: { rulesRun: v.ok ? v.rulesRun : 0, wontOpen: sev("wont-open"), blank: sev("blank"), findings },
    next: [
      "These measures have not been evaluated. Close Power BI Desktop before calling this tool, then open the project, refresh, and check each number against the source.",
      "A changed model opens empty until refreshed and saved: after this change, refresh in Power BI Desktop and save before the project is opened again or handed over.",
    ],
  };
}

module.exports = { writeMeasures, tokenize, resolveReferences };
