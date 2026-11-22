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
/**
 * Words DAX reads as an enumeration value where a function takes one: DATEADD ( ..., -1, YEAR ),
 * CONVERT ( ..., CURRENCY ), RANKX ( ..., DESC, DENSE ). A bare one is never rewritten, even when a
 * table shares the name: quoting it would turn valid DAX into a table reference. It is left as
 * written, and the result says so, because the caller may have meant the table.
 */
const ENUM_WORDS = new Set(["day", "month", "quarter", "year", "week", "hour", "minute", "second",
  "integer", "int64", "double", "decimal", "string", "text", "boolean", "logical", "currency", "datetime",
  "dense", "skip", "abs", "rel", "first", "last", "none", "both", "oneway", "twoway", "left", "right"]);
/** Functions whose string arguments name new columns, so `[Name]` may refer to one of them. */
const INTRODUCERS = new Set(["addcolumns", "selectcolumns", "summarize", "summarizecolumns", "groupby",
  "row", "datatable", "addmissingitems", "substitutewithindex", "topnskip"]);
const AGGREGATES_OF_DATES = new Set(["max", "min", "lastdate", "firstdate", "lastnonblank", "firstnonblank", "eomonth", "edate"]);

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
    } else if (/[\p{L}_]/u.test(ch)) {
      // Letters of any script: a bare table name such as Umsätze is a reference to check and quote.
      let j = i; while (j < n && /[\p{L}\p{N}_]/u.test(expr[j])) j++;
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
 * each bare table reference single-quoted, plus a note per quoted name and per word left as written.
 *
 *   'Table'[Column] and Table[Column]   the table must exist, and the column or measure be on it.
 *   bare Table                          a table reference when it names a table and is not a variable,
 *                                       a keyword or an enumeration value; quoted.
 *   [Name]                              a measure anywhere in the model; or a column of the home table
 *                                       or of a table the expression names (the tables in scope); or a
 *                                       column the expression itself introduces as a string argument
 *                                       of ADDCOLUMNS, SELECTCOLUMNS, SUMMARIZE and the like.
 */
function resolveReferences(measureName, expr, index, batch) {
  const toks = tokenize(expr);
  const sig = toks.map((t, k) => k).filter((k) => toks[k].type !== "space" && toks[k].type !== "comment");
  const at = new Map(sig.map((k, i) => [k, i]));
  const next = (k) => { const i = at.get(k); return i === undefined || i + 1 >= sig.length ? undefined : toks[sig[i + 1]]; };
  const prevOf = (k) => { const i = at.get(k); return i === undefined || i === 0 ? undefined : toks[sig[i - 1]]; };
  const varNames = new Set();
  for (const k of sig) {
    const t = toks[k];
    if (t.type === "ident" && t.text.toLowerCase() === "var") {
      const nx = next(k);
      if (nx && nx.type === "ident") varNames.add(nx.text.toLowerCase());
    }
  }
  // The innermost open function at each token, to know which strings introduce a column name.
  const introduced = new Set();
  const stack = [];
  for (const k of sig) {
    const t = toks[k];
    if (t.type === "char" && t.text === "(") {
      const p = prevOf(k);
      stack.push(p && p.type === "ident" ? p.text.toLowerCase() : "");
    } else if (t.type === "char" && t.text === ")") stack.pop();
    else if (t.type === "string" && stack.length && INTRODUCERS.has(stack[stack.length - 1])) {
      introduced.add(t.text.slice(1, -1).replace(/""/g, "\"").toLowerCase());
    }
  }
  const problems = [];
  const quoted = new Map();
  const leftAsWritten = new Map();
  const edits = [];
  const inScope = new Set([index.home.toLowerCase()]);
  const tableNames = [...index.tables.values()].map((t) => t.name);
  const allMeasures = [...index.measures.values()].map((m) => m.name).concat(batch);
  const brackets = [];

  for (let i = 0; i < sig.length; i++) {
    const k = sig[i];
    const t = toks[k];
    if (t.type === "qtable" || t.type === "ident") {
      const nx = next(k);
      if (t.type === "ident" && nx && nx.type === "char" && nx.text === "(") continue;      // a function
      const prev = prevOf(k);
      if (t.type === "ident" && prev && prev.type === "char" && prev.text === ".") continue;
      const name = t.type === "qtable" ? quotedName(t) : t.text;
      const lower = name.toLowerCase();
      const followedByColumn = nx && nx.type === "bracket";
      if (t.type === "ident" && !followedByColumn) {
        // A bare word: a table reference only when it names a table and is not a variable or keyword.
        if (varNames.has(lower) || KEYWORDS.has(lower)) continue;
        if (ENUM_WORDS.has(lower)) {
          if (index.tables.has(lower)) leftAsWritten.set(name, index.tables.get(lower).name);
          continue;
        }
        if (!index.tables.has(lower)) continue;
      }
      const table = index.tables.get(lower);
      if (!table) {
        problems.push(`${t.type === "qtable" ? t.text : name}${followedByColumn ? nx.text : ""}: no table '${name}' in the model (nearest: ${nearest(name, tableNames).join(", ")})`);
        if (followedByColumn) i++;
        continue;
      }
      inScope.add(table.name.toLowerCase());
      if (t.type === "ident") {
        edits.push({ start: t.start, end: t.end, text: daxTable(table.name) });
        quoted.set(name, daxTable(table.name));
      }
      if (followedByColumn) {
        const col = bracketName(nx);
        const own = batch.some((b) => b.toLowerCase() === col.toLowerCase()) && table.name.toLowerCase() === index.home.toLowerCase();
        if (!table.columns.has(col.toLowerCase()) && !table.measures.has(col.toLowerCase()) && !own) {
          problems.push(`${daxTable(table.name)}${nx.text}: ${table.name} has no column or measure '${col}' (nearest: ${nearest(col, [...table.columnNames, ...table.measureNames]).join(", ")})`);
        }
        i++;
      }
      continue;
    }
    if (t.type === "bracket") brackets.push(t);
  }
  // Unqualified names, once every table the expression names is known.
  for (const t of brackets) {
    const name = bracketName(t);
    const lower = name.toLowerCase();
    if (lower === measureName.toLowerCase()) { problems.push(`[${name}]: the measure refers to itself`); continue; }
    if (index.measures.has(lower) || batch.some((b) => b.toLowerCase() === lower) || introduced.has(lower)) continue;
    const scoped = [...inScope].map((s) => index.tables.get(s)).filter(Boolean);
    if (scoped.some((tb) => tb.columns.has(lower))) continue;
    const elsewhere = [...index.tables.values()].filter((tb) => tb.columns.has(lower)).map((tb) => tb.name);
    problems.push(elsewhere.length
      ? `[${name}]: a column of ${elsewhere.join(", ")}, which the expression does not name; write ${daxTable(elsewhere[0])}[${name}], or name the table it iterates`
      : `[${name}]: no measure or column '${name}' in the model (nearest: ${nearest(name, [...allMeasures, ...[...index.tables.values()].flatMap((x) => x.columnNames)]).join(", ")})`);
  }
  let out = expr;
  for (const e of edits.sort((a, b) => b.start - a.start)) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  return { expression: out, problems, dateValued: returnsDate(toks, sig, index),
    quoted: [...quoted.entries()].map(([from, to]) => `${from} -> ${to}`),
    leftAsWritten: [...leftAsWritten.entries()].map(([w, tb]) => `${w} was left as written: it is a DAX keyword; write ${daxTable(tb)} to mean the table`) };
}

/**
 * Whether the measure's result is a date: the result (the whole expression, the part after the last
 * top-level RETURN, or CALCULATE's first argument) is one aggregate that returns a date (MAX, MIN,
 * LASTDATE and the like) over a dateTime column. A MAX of a date used as an argument, as in a rolling
 * DATESINPERIOD window, is not the result and does not count.
 */
function returnsDate(toks, sig, index) {
  const all = sig.map((k) => toks[k]);
  // After the last RETURN at depth 0; and each depth-0 VAR's definition, to follow `RETURN d`.
  let depth = 0, from = 0;
  const vars = new Map();
  let open = null;
  all.forEach((t, i) => {
    if (t.type === "char" && t.text === "(") depth++;
    else if (t.type === "char" && t.text === ")") depth--;
    else if (depth === 0 && t.type === "ident" && /^(var|return)$/i.test(t.text)) {
      if (open) vars.set(open.name, all.slice(open.start, i));
      open = null;
      if (/^return$/i.test(t.text)) from = i + 1;
      else if (all[i + 1] && all[i + 2] && all[i + 2].text === "=") open = { name: all[i + 1].text.toLowerCase(), start: i + 3 };
    }
  });
  let s = all.slice(from);
  if (s.length === 1 && s[0].type === "ident" && vars.has(s[0].text.toLowerCase())) s = vars.get(s[0].text.toLowerCase());
  const strip = (a) => {
    // Drop parentheses that wrap the whole sequence.
    while (a.length >= 2 && a[0].text === "(" && a[a.length - 1].text === ")") {
      let d = 0, wraps = true;
      for (let i = 0; i < a.length - 1; i++) { if (a[i].text === "(") d++; else if (a[i].text === ")") d--; if (d === 0) { wraps = false; break; } }
      if (!wraps) break;
      a = a.slice(1, -1);
    }
    return a;
  };
  /** The argument list of a call starting at a[0] (ident) a[1] "(", if the call is the whole of a. */
  const callArgs = (a) => {
    if (a.length < 3 || a[0].type !== "ident" || a[1].text !== "(" || a[a.length - 1].text !== ")") return null;
    let d = 0;
    const args = [[]];
    for (let i = 1; i < a.length; i++) {
      const t = a[i];
      if (t.text === "(") { d++; if (d === 1) continue; }
      if (t.text === ")") { d--; if (d === 0) { if (i !== a.length - 1) return null; continue; } }
      if (d === 1 && t.text === ",") { args.push([]); continue; }
      args[args.length - 1].push(t);
    }
    return args;
  };
  s = strip(s);
  let args = callArgs(s);
  if (args && s[0].text.toLowerCase() === "calculate") { s = strip(args[0]); args = callArgs(s); }
  if (!args || !AGGREGATES_OF_DATES.has(s[0].text.toLowerCase())) return false;
  const a0 = args[0];
  if (a0.length !== 2 || (a0[0].type !== "qtable" && a0[0].type !== "ident") || a0[1].type !== "bracket") return false;
  const tb = index.tables.get((a0[0].type === "qtable" ? quotedName(a0[0]) : a0[0].text).toLowerCase());
  return Boolean(tb && tb.types.get(bracketName(a0[1]).toLowerCase()) === "datetime");
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

function layoutBlock(m, unit, eol, keep = null) {
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
  if (keep && keep.lineageTag) lines.push(`${L2}lineageTag: ${keep.lineageTag}`);
  if (keep && keep.lines.length) lines.push("", ...keep.lines);
  return lines.join(eol);
}

/**
 * The last line (0-based) that belongs to a measure: its body, its properties and its children.
 * A line belongs while it is blank, part of an expression body or fence, or indented deeper than
 * the measure; the first other line (a sibling, a table property, the next table) ends it.
 */
function measureEnd(tree, lines, m) {
  let last = m.line - 1;
  for (let i = m.line; i < lines.length; i++) {
    const L = tree.lines[i];
    if (!lines[i].trim()) continue;
    if (L && (L.inExpr || L.kind === "body" || L.kind === "fence-close" || L.ind > m.ind)) { last = i; continue; }
    break;
  }
  return last;
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
  const leftAsWritten = [];
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
    for (const q of r.leftAsWritten) leftAsWritten.push(`${s.name}: ${q}`);
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
    const from = startOf(m);
    const to = measureEnd(tree, lines, m);
    const a = starts[from];
    const b = to + 1 < starts.length ? starts[to + 1] - (text[starts[to + 1] - 2] === "\r" ? 2 : 1) : text.length;
    // The identity Desktop gave the measure and its annotations carry over to the replacement.
    const lineageTag = T.propValue(m, "lineageTag");
    const keepLines = m.children.filter((c) => c.type === "annotation" || c.type === "extendedproperty" || c.type === "changedproperty")
      .map((c) => lines[c.line - 1]);
    const block = layoutBlock(s, unit, eol, { lineageTag, lines: keepLines });
    edits.push({ a, b, text: block });
    removed.push({ name: s.name, removed: text.slice(a, b), added: block,
      kept: [...(lineageTag ? ["lineageTag"] : []), ...keepLines.map((l) => l.trim())] });
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

  // The file is written beside itself and renamed over the original, so a failure part-way never
  // leaves a truncated model file; and it is read again first, so a save made by another program
  // since it was read is not lost.
  if (deps.beforeCommit) deps.beforeCommit(abs);
  need(fs.readFileSync(abs).equals(buf), `${src.rel} changed on disk while the measures were being prepared (another program saved it). Nothing was written; call again.`);
  const tmp = path.join(path.dirname(abs), `.${path.basename(abs)}.write_measures-${process.pid}.tmp`);
  fs.writeFileSync(tmp, Buffer.from(out, "utf8"), { flag: "wx" });
  try { fs.renameSync(tmp, abs); } catch (e) {
    need(false, `writing ${src.rel} failed while replacing it (${e.code || e.message}); the original is unchanged and the new content is in ${tmp}.`);
  }

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
    ...(leftAsWritten.length ? { leftAsWritten } : {}),
    lineEnding: eol === "\r\n" ? "CRLF" : "LF",
    validation: { rulesRun: v.ok ? v.rulesRun : 0, wontOpen: sev("wont-open"), blank: sev("blank"), findings },
    next: [
      "These measures have not been evaluated. Close Power BI Desktop before calling this tool, then open the project, refresh, and check each number against the source.",
      "A changed model opens empty until refreshed and saved: after this change, refresh in Power BI Desktop and save before the project is opened again or handed over.",
    ],
  };
}

module.exports = { writeMeasures, tokenize, resolveReferences };
