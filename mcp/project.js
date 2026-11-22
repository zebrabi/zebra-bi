// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
/**
 * write_project: a complete Power BI project on disk from a folder of flat files.
 *
 * The other writers return file content for the agent host to save. A semantic model with its rows
 * embedded is too large and too exacting for that route: every table, every DATATABLE row, the
 * calendar and every measure would pass through the conversation twice, and each one is a place for
 * a hand-written fault. This module takes a source path and a small structured spec, and writes the
 * project itself:
 *
 *   <name>.pbip
 *   <name>.Report/definition.pbir
 *   <name>.Report/definition/report.json, version.json, pages/...        (pages through writeReport)
 *   <name>.SemanticModel/definition.pbism
 *   <name>.SemanticModel/definition/database.tmdl, model.tmdl, relationships.tmdl, tables/*.tmdl
 *
 * The write is deliberately narrow. The target folder must not exist or must be empty; no file is
 * ever overwritten and nothing is deleted. Everything is computed and checked in memory first, so a
 * refusal writes nothing. The data never comes back in the result: the caller gets the list of files
 * with their sizes, the model's shape, the assumptions the tool made, and the result of running the
 * validator and the profilers over what it wrote.
 *
 * Every piece of text in the model comes from a template here. There is no free-form DAX input, so
 * every table reference is single-quoted, every DATATABLE row holds constants only, every column of
 * a calculated table binds by inferred name, and indentation is fixed.
 */

const fs = require("fs");
const path = require("path");
const enc = require("./encoders.js");
const wr = require("./writers.js");
const ps = require("./profile-source.js");
const prof = require("./profile.js");
const propose = require("./propose.js");
const tmdl = require("./tmdl.js");

const { EncodeError } = enc;
const need = (cond, msg) => { if (!cond) throw new EncodeError(msg); };

// --- limits and fixed values ----------------------------------------------------------------

/**
 * The size ceiling. The largest embedded tables the authoring references record as opening and
 * refreshing are 39,409 rows by 5 columns and 18,760 rows by 15 columns, both about 1.1 MB of
 * DATATABLE text. The ceiling sits above both and below anything not yet measured.
 */
const LIMITS = Object.freeze({
  cells: 300000,
  datatableBytes: 2 * 1024 * 1024,
  sourceFileBytes: 64 * 1024 * 1024,
  pathLength: 240,
});

// 1600 opens on older Desktop builds, and a higher level refuses to open there; a project handed to
// someone else should open on their Desktop. Desktop raises the level in memory when it opens.
const COMPATIBILITY_LEVEL = 1600;
const BASE_THEME = "CY23SU08";
const CALENDAR = "Calendar";

const SCHEMA_URL = {
  pbip: "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
  pbir: "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
  report: "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.0.0/schema.json",
  pbism: "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
};

const CODES = ["AC", "PL", "FC", "PY"];
const CODE_NAMES = [
  [/^(ac|act|actual|actuals)$/i, "AC"],
  [/^(pl|plan|budget|bu|bud|target)$/i, "PL"],
  [/^(fc|forecast|outlook|estimate|fcst)$/i, "FC"],
  [/^(py|prior ?year|previous ?year|ly|last ?year)$/i, "PY"],
];
const codeOf = (label) => {
  const s = String(label).trim();
  for (const [re, code] of CODE_NAMES) if (re.test(s)) return code;
  return null;
};

/** Table names that load and then break, or do not load at all; and the calendar this tool owns. */
const RESERVED_TABLE_NAMES = new Set(["measures", "measure", "goal", "calculations", "kpi", "calendar"]);
/** File names Windows refuses whatever their extension. */
const DEVICE_NAMES = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

/** The words of a column name: split on separators and on camelCase, lower-cased. */
const words = (s) => String(s).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9%]+/).filter(Boolean);
// A column whose name says it is not an additive amount is not summed by default. Matched on whole
// words, so "generation" is not read as "ratio".
const NOT_ADDITIVE_WORDS = new Set(["rate", "rates", "ratio", "percent", "percentage", "pct", "%", "share",
  "price", "average", "avg", "mean", "index", "score", "rank", "level", "age"]);
const notAdditive = (s) => words(s).some((w) => NOT_ADDITIVE_WORDS.has(w) || w.includes("%"));
const DATE_PART = /^(year|yr|month|month ?no|month ?num(ber)?|day|week|quarter|qtr|period|fy|fiscal.*|calendar ?year|semester)$/i;
const KEY_WORDS = new Set(["key", "id", "code", "no", "nr", "number"]);
const keyish = (s) => { const w = words(s); return w.length > 0 && KEY_WORDS.has(w[w.length - 1]); };
const AMOUNT_HINT = /amount|value|sales|revenue|cost|expense|qty|quantity|units|count|total|net|gross|profit|generation|volume|spend|income|balance|budget|actual|forecast/i;

const INPUT_KEYS = new Set(["name", "outPath", "source", "tables", "date", "scenarios", "metrics", "pages"]);
const TABLE_KEYS = new Set(["source", "name", "role", "types", "hidden", "columns"]);
const SCENARIO_KEYS = new Set(["form", "column", "members", "columns", "py"]);
const METRIC_KEYS = new Set(["name", "column", "columns", "agg", "format", "numerator", "denominator"]);

function knownKeys(obj, allowed, what) {
  const unknown = Object.keys(obj).filter((k) => !allowed.has(k) && obj[k] !== undefined);
  need(!unknown.length, `unknown ${what} field(s): ${unknown.join(", ")}. Known fields: `
    + `${[...allowed].join(", ")}. A field this tool does not read would be silently ignored.`);
}

// --- names ---------------------------------------------------------------------------------

/**
 * One deterministic rule for every table and column name taken from a source. Characters that need
 * escaping in TMDL, in a DAX string, in a DAX bracket or in a Windows file name become a space;
 * runs of space collapse. Letters and digits of any script are kept.
 */
function cleanName(raw, fallback) {
  const s = String(raw == null ? "" : raw)
    .replace(/[\u0000-\u001f\u007f﻿.'"`\[\]{}=:;,\/\\|<>?*]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return s || fallback;
}

/** Makes `name` unique against `taken` (lower-cased keys), case-insensitively, as DAX compares. */
function uniqueName(name, taken) {
  let n = name, i = 2;
  while (taken.has(n.toLowerCase())) n = `${name} ${i++}`;
  taken.add(n.toLowerCase());
  return n;
}

/** A TMDL object name: bare when it is a plain identifier, single-quoted otherwise. */
const tmdlName = (n) => (/^[A-Za-z_][A-Za-z0-9_]*$/.test(n) ? n : `'${n.replace(/'/g, "''")}'`);
/** A DAX table reference, always quoted. */
const daxTable = (n) => `'${n.replace(/'/g, "''")}'`;
/** A DAX column or measure reference body. */
const daxBracket = (n) => `[${n.replace(/]/g, "]]")}]`;
const daxCol = (t, c) => `${daxTable(t)}${daxBracket(c)}`;
/** A DAX string literal. */
const daxString = (s) => `"${String(s).replace(/"/g, "\"\"")}"`;

// --- reading the source ---------------------------------------------------------------------

/**
 * Splits delimited text into records, honouring double quotes, so a quoted cell may hold a line
 * break. `profile_source` reads line by line, which is right for a profile and wrong for a writer:
 * a quoted line break would split one row into two and shift every column after it.
 */
function splitRecords(raw) {
  const out = [];
  let cur = "", q = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (ch === "\"") { q = !q; cur += ch; continue; }
    if (ch === "\n" && !q) { out.push(cur.endsWith("\r") ? cur.slice(0, -1) : cur); cur = ""; continue; }
    cur += ch;
  }
  if (cur.length) out.push(cur.endsWith("\r") ? cur.slice(0, -1) : cur);
  return out.filter((r) => r.length);
}

function readDelimited(file, notes) {
  let raw = fs.readFileSync(file, "utf8");
  if (raw.charCodeAt(0) === 0xfeff) raw = raw.slice(1);
  const records = splitRecords(raw);
  if (!records.length) return { header: [], rows: [] };
  const delimiter = ps.sniffDelimiter(records.slice(0, Math.min(25, records.length)));
  const first = ps.splitLine(records[0], delimiter);
  // The same header test profile_source applies, so both tools agree on what a header is.
  const headerless = first.every((c) => c === "" || ps.NUM_RE.test(c) || ps.toIsoDate(c, false))
    || new Set(first).size !== first.length;
  const header = headerless ? first.map((_, i) => `col${i + 1}`) : first.map((h) => h.trim());
  const rows = [];
  let padded = 0;
  for (let i = headerless ? 0 : 1; i < records.length; i++) {
    const r = ps.splitLine(records[i], delimiter);
    if (r.length === 1 && r[0].trim() === "") continue;
    if (r.length !== header.length) {
      need(!/\n/.test(records[i]) && r.length < header.length,
        `${path.basename(file)}: record ${i + 1} has ${r.length} field(s) and the header has `
        + `${header.length}. ${/\n/.test(records[i]) ? "A double quote that is never closed makes the "
        + "rest of the file one record. " : ""}Fix the file so every row has the header's field `
        + "count; the tool does not guess which field moved.");
      while (r.length < header.length) r.push("");
      padded++;
    }
    rows.push(r);
  }
  if (padded) notes.push(`${path.basename(file)}: ${padded} row(s) had fewer fields than the header; the missing trailing fields were read as blank.`);
  return { header, rows, delimiter, headerless };
}

function readSource(root, overrides) {
  need(typeof root === "string" && root.length, "source.path is required: an absolute path to a "
    + "folder of flat files (CSV, TSV, pipe or semicolon text, or JSON arrays) or to one file.");
  need(path.isAbsolute(root), `source.path must be absolute, not '${root}'.`);
  need(fs.existsSync(root), `source.path ${root} does not exist.`);
  const isFolder = fs.statSync(root).isDirectory();
  const files = ps.listFiles(root);
  need(files.length, `no .csv, .txt, .tsv, .psv or .json file was found under ${root}.`);
  const notes = [];
  const tables = [];
  // Every file that is not loaded, with the reason. Nothing is left out in silence.
  const skipped = [];
  const skip = (file, reason) => skipped.push({ file: path.basename(file), reason });
  for (const file of files) {
    const ext = path.extname(file).toLowerCase();
    const stem = path.basename(file, ext);
    const ov = overrides.find((o) => sameSource(o.source, file));
    if (ov && ov.role === "skip") { skip(file, "skipped as asked (role: skip)"); continue; }
    // A file the caller named, or the one file source.path points at, must load or refuse; any other
    // file in a folder that cannot be read as a table is skipped with the reason.
    const required = Boolean(ov) || !isFolder;
    if (/^\.(xlsx|xls|xlsb|parquet|pbix|accdb)$/.test(ext)) {
      need(!required, `${path.basename(file)} is a binary file; export it to CSV.`);
      skip(file, "a binary file; export it to CSV to include it");
      continue;
    }
    const size = fs.statSync(file).size;
    need(size <= LIMITS.sourceFileBytes,
      `${path.basename(file)} is ${size} bytes, above the ${LIMITS.sourceFileBytes}-byte source limit, `
      + "and its rows could not fit under the model's size ceiling anyway. Pre-aggregate it, or point "
      + "source.path at the files you need and skip this one with tables: [{source, role: \"skip\"}].");
    let t;
    if (ext === ".json") {
      t = ps.readJsonTable(file);
      if (t.unreadable) {
        need(!required, `${path.basename(file)} was not read: ${t.unreadable}.`);
        skip(file, `not a table: ${t.unreadable}`);
        continue;
      }
    } else {
      try { t = readDelimited(file, notes); } catch (e) {
        if (required) throw e;
        skip(file, `not readable as a table: ${e.message}`);
        continue;
      }
    }
    if (!t.rows.length) { skip(file, "no data rows"); continue; }
    tables.push({ file: path.basename(file), stem, header: t.header, rows: t.rows, override: ov || null,
      headerless: Boolean(t.headerless), required });
  }
  for (const o of overrides) {
    const hit = files.some((f) => sameSource(o.source, f));
    need(hit, `tables: no source file matches '${o.source}'. Files read: `
      + files.map((f) => path.basename(f)).join(", "));
  }
  need(tables.length, `nothing readable was found under ${root}.${skipped.length ? ` ${skipped.map((s) => `${s.file}: ${s.reason}`).join("; ")}` : ""}`);
  return { tables, notes, skipped, isFolder };
}

function sameSource(name, file) {
  if (typeof name !== "string") return false;
  const b = path.basename(file).toLowerCase();
  const n = name.toLowerCase();
  return n === b || n === path.basename(file, path.extname(file)).toLowerCase();
}

// --- column typing --------------------------------------------------------------------------

const DECIMAL_COMMA = /^[+-]?(\d{1,3}(\.\d{3})+|\d+),\d+$|^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/;
const LEADING_ZERO = /^[+-]?0\d+$/;
const TIME_PART = /[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?Z?$/;
const SLASH_DMY = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/;
const isBlank = (v) => v === "" || v.toUpperCase() === "NULL" || v === "NaN";

/**
 * Decides one column's kind (text, integer, number, date) from its values, and refuses a column
 * whose values would be changed or lost by any kind it could be given. The profile's type is a 98%
 * vote, which is right for describing a file and wrong for writing one: the other 2% would be lost.
 */
function decideKind(table, colName, values, profileType, forced, assumptions) {
  const where = `${table}[${colName}]`;
  const filled = values.filter((v) => !isBlank(v));
  const blanks = values.length - filled.length;
  const firstBad = (test) => filled.find((v) => !test(v));
  const forcedNote = `, or pass tables: [{source: "${table}", types: {"${colName}": "text"}}] to load it as text`;

  if (forced) {
    need(["text", "integer", "number", "date"].includes(forced),
      `${where}: type '${forced}' is not one of text, integer, number, date.`);
    if (forced === "text") return { kind: "text", blanks };
    if (forced === "integer") {
      const bad = firstBad((v) => ps.INT_RE.test(v));
      need(bad === undefined, `${where} was given type integer but holds '${bad}'.`);
      return { kind: "integer", blanks };
    }
    if (forced === "number") {
      const bad = firstBad((v) => ps.NUM_RE.test(v));
      need(bad === undefined, `${where} was given type number but holds '${bad}'.`);
      return { kind: "number", blanks };
    }
    return dateKind(where, filled, blanks, assumptions);
  }

  if (!filled.length) return { kind: "text", blanks };

  if (profileType === "date") return dateKind(where, filled, blanks, assumptions);
  if (profileType === "integer" || profileType === "dateKey" || profileType === "number") {
    const bad = firstBad((v) => ps.NUM_RE.test(v));
    need(bad === undefined, `${where} is a mixed-type column: mostly numbers, but it also holds `
      + `'${bad}'. Clean the source${forcedNote}.`);
    if (profileType !== "number" && filled.some((v) => LEADING_ZERO.test(v))) {
      assumptions.push(`${where} holds values with leading zeros (such as '${filled.find((v) => LEADING_ZERO.test(v))}'), so it is kept as text.`);
      return { kind: "text", blanks };
    }
    if (profileType !== "number" && filled.some((v) => v.replace(/^[+-]/, "").length > 18)) {
      assumptions.push(`${where} holds integers longer than 18 digits, so it is kept as text.`);
      return { kind: "text", blanks };
    }
    return { kind: profileType === "number" ? "number" : "integer", blanks };
  }

  // A text column. Three shapes in it would be silently wrong as text or as a number, so they are
  // refused with the column named.
  const share = (test) => filled.filter(test).length / filled.length;
  if (share((v) => DECIMAL_COMMA.test(v)) >= 0.8) {
    need(false, `${where} holds numbers written with a comma (such as '${filled.find((v) => DECIMAL_COMMA.test(v))}'), `
      + "which is either a decimal comma or a thousands separator and cannot be read safely. "
      + `Export the file with '.' as the decimal separator and no thousands separator${forcedNote}.`);
  }
  if (share((v) => ps.NUM_RE.test(v)) >= 0.8) {
    need(false, `${where} is a mixed-type column: mostly numbers, but it also holds `
      + `'${filled.find((v) => !ps.NUM_RE.test(v))}'. Clean the source${forcedNote}.`);
  }
  if (share((v) => ps.toIsoDate(v.trim(), false) !== null) >= 0.8) {
    need(false, `${where} is mostly dates, but '${filled.find((v) => ps.toIsoDate(v.trim(), false) === null)}' `
      + `is not a date this tool can read (ISO yyyy-mm-dd, yyyy/mm/dd, or a slash form with a four-digit year)${forcedNote}.`);
  }
  return { kind: "text", blanks };
}

function dateKind(where, filled, blanks, assumptions) {
  // Day-first or month-first, decided from the whole column the way profile_source does, and
  // refused when the column gives no evidence either way or evidence both ways.
  let dayFirst = 0, monthFirst = 0, slash = 0;
  for (const v of filled) {
    const m = SLASH_DMY.exec(v);
    if (!m) continue;
    slash++;
    if (+m[1] > 12) dayFirst++;
    if (+m[2] > 12) monthFirst++;
  }
  need(!(dayFirst && monthFirst), `${where} mixes day-first and month-first slash dates. Export the `
    + "dates as ISO yyyy-mm-dd.");
  need(!(slash && !dayFirst && !monthFirst), `${where} holds slash dates whose day and month are all `
    + "12 or less, so the order cannot be told apart. Export the dates as ISO yyyy-mm-dd.");
  const df = dayFirst > 0;
  const bad = filled.find((v) => ps.toIsoDate(v, df) === null);
  need(bad === undefined, `${where} is a date column, but '${bad}' is not a date this tool can read `
    + "(ISO yyyy-mm-dd, yyyy/mm/dd, or a slash form with a four-digit year). Fix the value, or pass "
    + `types: {"${where.replace(/^.*\[|\]$/g, "")}": "text"} to load the column as text.`);
  const timed = filled.find((v) => {
    const m = TIME_PART.exec(v);
    return m && (+m[1] || +m[2] || +(m[3] || 0) || +(m[4] || 0));
  });
  if (timed) assumptions.push(`${where} carries a time of day (such as '${timed}'); only the date is kept.`);
  if (slash) assumptions.push(`${where} is read ${df ? "day-first" : "month-first"} (${df ? "d/m/yyyy" : "m/d/yyyy"}), from values whose ${df ? "first" : "second"} part is above 12.`);
  return { kind: "date", blanks, dayFirst: df };
}

// --- constants in DATATABLE rows -------------------------------------------------------------

/** A number from the source, as a culture-invariant DAX constant, digits kept exactly. */
function plainNumber(s) {
  let t = s.trim().replace(/^\+/, "");
  const m = /^(-?)(\d*)(?:\.(\d*))?[eE]([+-]?\d+)$/.exec(t);
  if (m) {
    const digits = (m[2] || "") + (m[3] || "");
    const point = (m[2] || "").length + Number(m[4]);
    let intPart, frac;
    if (point <= 0) { intPart = "0"; frac = "0".repeat(-point) + digits; }
    else if (point >= digits.length) { intPart = digits + "0".repeat(point - digits.length); frac = ""; }
    else { intPart = digits.slice(0, point); frac = digits.slice(point); }
    intPart = intPart.replace(/^0+(?=\d)/, "");
    frac = frac.replace(/0+$/, "");
    t = `${m[1]}${intPart}${frac ? `.${frac}` : ""}`;
  } else {
    if (/^-?\./.test(t)) t = t.replace(/^(-?)\./, "$10.");
    t = t.replace(/\.$/, "");
    t = t.replace(/^(-?)0+(?=\d)/, "$1");
  }
  if (/^-0(\.0*)?$/.test(t)) t = "0";
  return t;
}

/** Text as a DAX string constant. Line breaks and tabs become one space each; see assumptions. */
function textConstant(v, stats) {
  let s = v.replace(/﻿/g, "");
  if (/[\r\n\t]/.test(s)) { stats.lineBreaks++; s = s.replace(/\r\n|[\r\n\t]/g, " "); }
  return daxString(s);
}

// --- the model ------------------------------------------------------------------------------

/**
 * Turns one source table into its model form: kept columns, kinds, model names, DATATABLE layout.
 * Date columns become three hidden integer columns in the rows (Year, MonthNo, Day) and the date
 * itself is built outside the rows in ADDCOLUMNS, so no row ever holds a function call.
 */
function modelTable(t, modelName, assumptions) {
  const ov = t.override || {};
  const types = ov.types || {};
  const hidden = new Set((ov.hidden || []).map((h) => String(h).toLowerCase()));
  for (const k of Object.keys(types)) {
    need(t.header.some((h) => h.toLowerCase() === k.toLowerCase()),
      `tables[${t.stem}].types names '${k}', which is not a column of ${t.file}. Columns: ${t.header.join(", ")}.`);
  }
  for (const h of hidden) {
    need(t.header.some((x) => x.toLowerCase() === h),
      `tables[${t.stem}].hidden names '${h}', which is not a column of ${t.file}.`);
  }
  const profiled = ps.profileColumns(t.header, t.rows);
  const taken = new Set();
  const cols = t.header.map((h, i) => {
    const values = t.rows.map((r) => (r[i] === undefined ? "" : String(r[i]).trim()));
    const forcedKey = Object.keys(types).find((k) => k.toLowerCase() === h.toLowerCase());
    const k = decideKind(t.stem, h, values, profiled[i].type, forcedKey ? types[forcedKey] : null, assumptions);
    const name = uniqueName(cleanName(h, `Column ${i + 1}`), taken);
    if (name !== h) assumptions.push(`${t.stem}[${h}] is named ${modelName}[${name}] in the model.`);
    return { source: h, index: i, name, ...k, hidden: hidden.has(h.toLowerCase()), profile: profiled[i] };
  });
  return { ...t, name: modelName, cols, profiled, taken, measures: [] };
}

/** The DATATABLE columns in order, with each date replaced by its three parts, then the ADDCOLUMNS. */
function layout(mt, assumptions) {
  const dt = [];
  const added = [];
  for (const c of mt.cols) {
    if (c.kind !== "date") { dt.push({ col: c, type: c.kind }); continue; }
    const parts = ["Year", "MonthNo", "Day"].map((p) => uniqueName(`${c.name} ${p}`, mt.taken));
    c.parts = parts;
    parts.forEach((p, j) => dt.push({ col: c, type: "integer", part: j, name: p }));
    added.push(c);
  }
  void assumptions;
  return { dt, added };
}

const DT_TYPE = { text: "STRING", integer: "INTEGER", number: "DOUBLE" };
const TMDL_TYPE = { text: "string", integer: "int64", number: "double", date: "dateTime" };

/**
 * The partition expression. It starts four tabs in, one level deeper than `source =`, and every
 * nested line adds one more tab, so each row sits at the expression's base indent or deeper and no
 * line is indented with spaces.
 */
function datatableText(mt, lay, stats) {
  const B = "\t\t\t\t";
  const decls = lay.dt.map((d) => `${daxString(d.name || d.col.name)}, ${DT_TYPE[d.type]}`).join(", ");
  const lines = [];
  const wrapped = lay.added.length > 0;
  const pad = wrapped ? "\t" : "";
  if (wrapped) lines.push(`${B}ADDCOLUMNS (`);
  lines.push(`${B}${pad}DATATABLE (`);
  lines.push(`${B}${pad}\t${decls},`);
  lines.push(`${B}${pad}\t{`);
  const rowLines = [];
  for (const r of mt.rows) {
    const cells = [];
    for (const d of lay.dt) {
      const raw = (r[d.col.index] === undefined ? "" : String(r[d.col.index])).trim();
      const blank = isBlank(raw);
      if (d.part !== undefined) {
        if (blank) { cells.push("0"); continue; }
        const iso = ps.toIsoDate(raw, d.col.dayFirst);
        cells.push(String(Number(iso.split("-")[d.part])));
      } else if (d.type === "text") {
        cells.push(blank ? "\"\"" : textConstant(raw, stats));
      } else {
        cells.push(blank ? "0" : plainNumber(raw));
      }
    }
    rowLines.push(`${B}${pad}\t\t{${cells.join(", ")}}`);
  }
  lines.push(rowLines.join(",\n"));
  lines.push(`${B}${pad}\t}`);
  if (wrapped) {
    lines.push(`${B}\t),`);
    lay.added.forEach((c, i) => {
      const [y, m, d] = c.parts.map(daxBracket);
      const build = c.blanks
        ? `IF ( ${y} = 0, BLANK (), DATE ( ${y}, ${m}, ${d} ) )`
        : `DATE ( ${y}, ${m}, ${d} )`;
      lines.push(`${B}\t${daxString(c.name)}, ${build}${i < lay.added.length - 1 ? "," : ""}`);
    });
    lines.push(`${B})`);
  } else {
    lines.push(`${B})`);
  }
  return lines.join("\n");
}

function columnBlock(name, props) {
  return [`\tcolumn ${tmdlName(name)}`, ...props.map((p) => `\t\t${p}`)].join("\n");
}

function dataColumnProps(type, opts = {}) {
  const props = [`dataType: ${type}`];
  if (opts.isKey) props.push("isKey");
  if (opts.hidden) props.push("isHidden");
  props.push("summarizeBy: none", "isNameInferred", `sourceColumn: ${daxBracket(opts.source)}`);
  if (opts.formatString) props.push(`formatString: ${opts.formatString}`);
  if (opts.sortBy) props.push(`sortByColumn: ${tmdlName(opts.sortBy)}`);
  return props;
}

function measureBlock(m) {
  const body = m.dax.split("\n").map((l) => `\t\t\t${l}`).join("\n");
  return [`\tmeasure ${tmdlName(m.name)} =`, body, `\t\tformatString: ${m.format}`].join("\n");
}

function tableText(mt, lay, stats) {
  const parts = [`table ${tmdlName(mt.name)}`];
  for (const m of mt.measures) parts.push("", measureBlock(m));
  const declared = [];
  for (const d of lay.dt) {
    if (d.part !== undefined) {
      declared.push(columnBlock(d.name, dataColumnProps("int64", { hidden: true, source: d.name, formatString: "0" })));
    } else {
      const c = d.col;
      declared.push(columnBlock(c.name, dataColumnProps(TMDL_TYPE[c.kind], {
        hidden: c.hidden, source: c.name, formatString: c.kind === "integer" ? "0" : null, sortBy: c.sortBy })));
    }
  }
  for (const c of lay.added) {
    declared.push(columnBlock(c.name, dataColumnProps("dateTime", { hidden: c.hidden, source: c.name, formatString: "yyyy-MM-dd" })));
  }
  for (const b of declared) parts.push("", b);
  parts.push("", `\tpartition ${tmdlName(mt.name)} = calculated`, "\t\tmode: import", "\t\tsource =",
    datatableText(mt, lay, stats));
  return `${parts.join("\n")}\n`;
}

function calendarText(cal) {
  const B = "\t\t\t\t";
  const cols = [];
  cols.push(columnBlock("Date", dataColumnProps("dateTime", { isKey: true, source: "Date", formatString: "yyyy-MM-dd" })));
  const add = [];
  const col = (name, type, expr, opts = {}) => {
    cols.push(columnBlock(name, dataColumnProps(type, { ...opts, source: name })));
    add.push(`${daxString(name)}, ${expr}`);
  };
  col("Year", "int64", "YEAR ( [Date] )", { formatString: "0" });
  col("MonthNo", "int64", "MONTH ( [Date] )", { hidden: true, formatString: "0" });
  col("Month", "string", "FORMAT ( [Date], \"mmm\" )", { sortBy: "MonthNo" });
  col("QuarterNo", "int64", "QUARTER ( [Date] )", { hidden: true, formatString: "0" });
  col("Quarter", "string", "\"Q\" & QUARTER ( [Date] )", { sortBy: "QuarterNo" });
  col("YearMonth", "string", "YEAR ( [Date] ) & \"-\" & FORMAT ( MONTH ( [Date] ), \"00\" )");
  if (cal.pyDate) {
    // Each date's date one year later, 29 February going to 28 February. The PY measure keeps the
    // prior dates whose next-year date is selected, so every prior date counts in exactly one
    // selected date: a whole month always gets the whole month a year earlier, 29 February included.
    col("Next year date", "dateTime", "IF ( MONTH ( [Date] ) = 2 && DAY ( [Date] ) = 29, "
      + "DATE ( YEAR ( [Date] ) + 1, 2, 28 ), DATE ( YEAR ( [Date] ) + 1, MONTH ( [Date] ), DAY ( [Date] ) ) )",
      { hidden: true, formatString: "yyyy-MM-dd" });
  }
  if (cal.fiscalStart !== 1) {
    col("Fiscal Year", "int64", `YEAR ( EDATE ( [Date], ${(13 - cal.fiscalStart) % 12} ) )`, { formatString: "0" });
    col("Fiscal Period", "int64", `MOD ( MONTH ( [Date] ) - ${cal.fiscalStart}, 12 ) + 1`, { formatString: "0" });
  }
  const [y1] = cal.first.split("-").map(Number);
  const [y2, m2, d2] = cal.last.split("-").map(Number);
  const src = [
    `${B}ADDCOLUMNS (`,
    `${B}\tCALENDAR ( DATE ( ${y1}, 1, 1 ), DATE ( ${y2}, ${m2}, ${d2} ) ),`,
    ...add.map((a, i) => `${B}\t${a}${i < add.length - 1 ? "," : ""}`),
    `${B})`,
  ].join("\n");
  const parts = [`table ${CALENDAR}`, "\tdataCategory: Time"];
  for (const b of cols) parts.push("", b);
  parts.push("", `\tpartition ${CALENDAR} = calculated`, "\t\tmode: import", "\t\tsource =", src);
  return `${parts.join("\n")}\n`;
}

// --- scenarios and measures -------------------------------------------------------------------

function findColumn(mt, ref, what) {
  need(typeof ref === "string" && ref.length, `${what} must name a column.`);
  const m = /^(.+)\[(.+)\]$/.exec(ref);
  const colName = m ? m[2] : ref;
  if (m) {
    const t = m[1].replace(/^'|'$/g, "").toLowerCase();
    need(t === mt.stem.toLowerCase() || t === mt.name.toLowerCase(),
      `${what} '${ref}' names table '${m[1]}', but the measures live on the fact table ${mt.name}.`);
  }
  const c = mt.cols.find((x) => x.source.toLowerCase() === colName.toLowerCase())
    || mt.cols.find((x) => x.name.toLowerCase() === colName.toLowerCase());
  need(c, `${what} '${ref}' is not a column of ${mt.name}. Columns: ${mt.cols.map((x) => x.source).join(", ")}.`);
  return c;
}

/** The scenario block, filled from the profile where it can be and refused where it cannot. */
function resolveScenarios(input, fact, tables, joins, dateCol, assumptions) {
  const s = input === undefined ? null : input;
  if (s !== null) {
    need(s && typeof s === "object" && !Array.isArray(s), "scenarios must be an object.");
    knownKeys(s, SCENARIO_KEYS, "scenarios");
  }
  let form = s && s.form;
  let out;
  if (!s || form === undefined) {
    const detected = detectLong(fact, tables, joins);
    if (detected) {
      form = "long";
      out = { form, column: detected.column, members: detected.members };
      assumptions.push(`Scenarios: long form, read from ${fact.name}[${detected.column.name}]: `
        + Object.entries(detected.members).map(([v, c]) => `${v} = ${c}`).join(", ")
        + `${detected.labels ? ` (keys labelled by ${detected.labels})` : ""}.`);
      for (const v of detected.unmapped) assumptions.push(`Scenarios: rows with ${fact.name}[${detected.column.name}] = ${v} are in no measure; its name is not a scenario this tool recognises.`);
      for (const v of detected.empty) assumptions.push(`Scenarios: ${v} is declared in ${detected.labels} and has no rows in ${fact.name}, so it has no measure.`);
    } else {
      const wide = fact.cols.filter((c) => (c.kind === "integer" || c.kind === "number") && codeOf(c.source));
      need(wide.length < 2, "No scenario column was found, but the columns "
        + `${wide.map((c) => c.source).join(", ")} read like scenarios side by side. Pass `
        + `scenarios: {"form": "wide", "columns": {${wide.map((c) => `"${codeOf(c.source)}": "${c.source}"`).join(", ")}}}`
        + ", or {\"form\": \"none\"} if they are not scenarios.");
      need(false, `No scenario column was found in ${fact.file}. Pass scenarios explicitly: `
        + "{\"form\": \"none\"} for actuals only (add \"py\": \"timeIntelligence\" for a previous year from "
        + "the calendar), {\"form\": \"long\", \"column\": \"<column>\", \"members\": {\"<value>\": \"AC\", "
        + "\"<value>\": \"PL\"}} when one column names the scenario of each row, or {\"form\": \"wide\", "
        + "\"columns\": {\"AC\": \"<column>\", \"PL\": \"<column>\"}} when each scenario has its own column.");
    }
  } else {
    need(["long", "wide", "none"].includes(form), `scenarios.form must be long, wide or none, not '${form}'.`);
    if (form === "long") {
      need(s.column, "scenarios.column is required for the long form: the column whose value names each row's scenario.");
      const column = findColumn(fact, s.column, "scenarios.column");
      need(s.members && typeof s.members === "object" && Object.keys(s.members).length,
        "scenarios.members is required for the long form: a map of each value in the column to "
        + "AC, PL, FC or PY, for example {\"Actual\": \"AC\", \"Budget\": \"PL\"}.");
      out = { form, column, members: { ...s.members } };
    } else if (form === "wide") {
      need(s.columns && typeof s.columns === "object" && Object.keys(s.columns).length,
        "scenarios.columns is required for the wide form: a map of AC, PL, FC or PY to the column "
        + "that holds that scenario, for example {\"AC\": \"Actual\", \"PL\": \"Budget\"}.");
      out = { form, columns: { ...s.columns } };
    } else {
      out = { form };
    }
  }

  if (out.form === "long") {
    const ci = out.column.index;
    const present = new Map();
    for (const r of fact.rows) {
      const v = String(r[ci] === undefined ? "" : r[ci]).trim();
      present.set(v, (present.get(v) || 0) + 1);
    }
    const seenCodes = new Set();
    for (const [v, code] of Object.entries(out.members)) {
      need(CODES.includes(code), `scenarios.members['${v}'] is '${code}'; use AC, PL, FC or PY.`);
      need(!seenCodes.has(code), `scenarios.members maps two values to ${code}. Map one value per code.`);
      seenCodes.add(code);
      need(present.has(v), `scenarios.members names '${v}', which never occurs in ${fact.name}[${out.column.name}]. `
        + `Values found: ${[...present.keys()].slice(0, 20).join(", ")}.`);
      if (out.column.kind !== "text") need(ps.NUM_RE.test(v), `scenarios.members key '${v}' must be a number: ${fact.name}[${out.column.name}] is numeric.`);
    }
    need(seenCodes.has("AC"), "scenarios.members maps no value to AC. One value must be the actuals.");
    if (s && s.form === "long") {
      for (const v of present.keys()) if (!(v in out.members) && v !== "") assumptions.push(`Scenarios: rows with ${fact.name}[${out.column.name}] = ${v} are in no measure; map the value in scenarios.members to include them.`);
    }
  }
  if (out.form === "wide") {
    for (const [code, colRef] of Object.entries(out.columns)) {
      need(CODES.includes(code), `scenarios.columns key '${code}' must be AC, PL, FC or PY.`);
      out.columns[code] = findColumn(fact, colRef, `scenarios.columns.${code}`);
    }
    need(out.columns.AC, "scenarios.columns has no AC: one column must be the actuals.");
  }

  // Previous year.
  const hasPyMember = (out.form === "long" && Object.values(out.members).includes("PY"))
    || (out.form === "wide" && out.columns.PY);
  let py = s ? s.py : undefined;
  if (py === undefined) {
    if (hasPyMember) py = "column";
    else if (dateCol && dateCol.months >= 13) py = "timeIntelligence";
    else py = "none";
    assumptions.push(`Previous year: ${py === "column" ? "taken from the source's own PY scenario"
      : py === "timeIntelligence" ? "computed from the calendar, one year back, day by day"
      : "none (the data spans under 13 months or has no date)"}.`);
  }
  need(["timeIntelligence", "column", "none"].includes(py), `scenarios.py must be timeIntelligence, column or none, not '${py}'.`);
  need(py !== "column" || hasPyMember, "scenarios.py is 'column' but no member or column is mapped to PY.");
  need(py !== "timeIntelligence" || dateCol, "scenarios.py is 'timeIntelligence', which needs a date column; none was found or given.");
  if (py !== "column" && hasPyMember) {
    if (out.form === "long") { for (const k of Object.keys(out.members)) if (out.members[k] === "PY") delete out.members[k]; }
    else delete out.columns.PY;
  }
  out.py = py;
  return out;
}

function detectLong(fact, tables, joins) {
  const sig = ps.scenarioSignal(tables.map((t) => t.rec), joins);
  if (!sig || sig.table !== fact.stem) return null;
  const column = fact.cols.find((c) => c.source === sig.column);
  if (!column) return null;
  const values = [...new Set(fact.rows.map((r) => String(r[column.index] === undefined ? "" : r[column.index]).trim()))].filter(Boolean);
  let labelOf = (v) => v;
  let labels = null;
  const empty = [];
  const j = joins.find((x) => x.fromTable === fact.stem && x.fromColumn === column.source);
  if (j) {
    const dim = tables.find((t) => t.rec.table === j.toTable);
    const ki = dim ? dim.rec.header.indexOf(j.toColumn) : -1;
    const ni = dim ? dim.rec.columns.findIndex((c) => c.type === "text" && c.name !== j.toColumn) : -1;
    if (ki >= 0 && ni >= 0) {
      const map = new Map(dim.rec.rows.map((r) => [String(r[ki]).trim(), String(r[ni]).trim()]));
      labelOf = (v) => map.get(v) || v;
      labels = `${dim.name}[${dim.rec.header[ni]}]`;
      for (const [k, label] of map) if (!values.includes(k) && codeOf(label)) empty.push(`${label} (${k})`);
    }
  }
  const members = {};
  const unmapped = [];
  const used = new Set();
  for (const v of values.sort()) {
    const code = codeOf(labelOf(v));
    if (code && !used.has(code)) { members[v] = code; used.add(code); } else unmapped.push(labels ? `${v} (${labelOf(v)})` : v);
  }
  if (!used.has("AC")) return null;
  return { column, members, unmapped, labels, empty };
}

function defaultFormat(col) {
  if (!col || col.kind !== "number") return "#,0";
  const p = col.profile || {};
  const big = Math.max(Math.abs(p.min || 0), Math.abs(p.max || 0));
  return big >= 1000 ? "#,0" : "#,0.00";
}

/** The metric list, explicit or defaulted, each resolved to columns per scenario code. */
function resolveMetrics(input, fact, sc, assumptions) {
  const list = [];
  const metricCols = new Set();
  const scenarioCol = sc.form === "long" ? sc.column : null;
  if (input === undefined) {
    if (sc.form === "wide") {
      list.push({ name: "Value", columns: sc.columns, agg: "sum", format: defaultFormat(sc.columns.AC) });
      assumptions.push(`Metric: one, named Value, from ${Object.entries(sc.columns).map(([k, c]) => `${k} = ${c.source}`).join(", ")}. Pass metrics to name it.`);
    } else {
      const numeric = fact.cols.filter((c) => (c.kind === "integer" || c.kind === "number")
        && c !== scenarioCol && !keyish(c.source) && !DATE_PART.test(c.source.trim()));
      const picked = numeric.filter((c) => AMOUNT_HINT.test(c.source) && !notAdditive(c.source));
      const chosen = picked.length ? picked : numeric.filter((c) => !notAdditive(c.source));
      need(chosen.length, `No additive amount column was found on ${fact.name}. Pass metrics: [{"name": "<name>", "column": "<column>"}].`);
      for (const c of chosen) list.push({ name: c.name, column: c, agg: "sum", format: defaultFormat(c) });
      assumptions.push(`Metrics: ${chosen.map((c) => c.source).join(", ")}, each summed. Pass metrics to choose others.`);
      const left = numeric.filter((c) => !chosen.includes(c));
      if (left.length) assumptions.push(`Not made a metric: ${left.map((c) => c.source).join(", ")} (not read as an additive amount). Pass it in metrics to add it.`);
    }
  } else {
    need(Array.isArray(input) && input.length, "metrics must be a non-empty array.");
    const names = new Set();
    for (const [i, m] of input.entries()) {
      need(m && typeof m === "object", `metrics[${i}] must be an object.`);
      knownKeys(m, METRIC_KEYS, `metrics[${i}]`);
      if (m.agg !== undefined) need(["sum", "average"].includes(m.agg), `metrics[${i}].agg must be sum or average.`);
      if (m.format !== undefined) need(typeof m.format === "string" && m.format.trim() && !/[\r\n]/.test(m.format), `metrics[${i}].format must be a one-line format string such as #,0 or 0.0%.`);
      if (m.numerator !== undefined || m.denominator !== undefined) {
        need(m.name && m.numerator && m.denominator, `metrics[${i}] is a ratio and needs name, numerator and denominator (metric names).`);
        list.push({ name: cleanName(m.name, `Metric ${i + 1}`), ratio: true, numerator: m.numerator, denominator: m.denominator, format: m.format || "0.0%" });
        continue;
      }
      let columns = null, column = null;
      if (m.columns !== undefined) {
        need(sc.form === "wide", `metrics[${i}].columns is for the wide form; use column with the ${sc.form} form.`);
        columns = {};
        for (const [code, ref] of Object.entries(m.columns)) {
          need(CODES.includes(code), `metrics[${i}].columns key '${code}' must be AC, PL, FC or PY.`);
          if (code === "PY" && sc.py !== "column") continue;
          columns[code] = findColumn(fact, ref, `metrics[${i}].columns.${code}`);
        }
        need(columns.AC, `metrics[${i}].columns has no AC.`);
      } else if (sc.form === "wide") {
        need(m.column === undefined, `metrics[${i}].column is not used with the wide form; give columns: {AC, PL, ...}, or leave it out to use scenarios.columns.`);
        columns = sc.columns;
      } else {
        need(m.column, `metrics[${i}].column is required: the amount column to aggregate.`);
        column = findColumn(fact, m.column, `metrics[${i}].column`);
      }
      const name = cleanName(m.name || (column ? column.name : "Value"), `Metric ${i + 1}`);
      need(!names.has(name.toLowerCase()), `two metrics are both named '${name}'.`);
      names.add(name.toLowerCase());
      const anyCol = column || columns.AC;
      list.push({ name, column, columns, agg: m.agg || "sum", format: m.format || defaultFormat(anyCol) });
    }
  }
  for (const m of list) {
    const cols = m.column ? [m.column] : m.columns ? Object.values(m.columns) : [];
    for (const c of cols) {
      need(c.kind === "integer" || c.kind === "number", `${fact.name}[${c.source}] is ${c.kind}, so it cannot be ${m.agg === "average" ? "averaged" : "summed"}.`);
      need(c !== scenarioCol, `${fact.name}[${c.source}] is the scenario column and cannot be a metric.`);
      metricCols.add(c);
    }
  }
  for (const c of metricCols) c.hidden = true;
  return list;
}

function buildMeasures(fact, metrics, sc, dateCol, assumptions) {
  const T = fact.name;
  const measures = [];
  const byMetric = new Map();
  const memberOf = {};
  if (sc.form === "long") for (const [v, code] of Object.entries(sc.members)) memberOf[code] = v;
  const rowsOf = (v) => fact.rows.filter((r) => String(r[sc.column.index]).trim() === v).length;
  const filterFor = (code) => {
    const v = memberOf[code];
    const lit = sc.column.kind === "text" ? daxString(v) : plainNumber(v);
    return `${daxCol(T, sc.column.name)} = ${lit}`;
  };
  for (const m of metrics) {
    if (m.ratio) continue;
    const fn = m.agg === "average" ? "AVERAGE" : "SUM";
    const codes = {};
    if (sc.form === "long") {
      for (const code of CODES) {
        if (!(code in memberOf)) continue;
        if (!rowsOf(memberOf[code])) {
          assumptions.push(`No ${code} measure for ${m.name}: ${sc.column.name} = ${memberOf[code]} has no rows.`);
          continue;
        }
        codes[code] = `CALCULATE ( ${fn} ( ${daxCol(T, m.column.name)} ), ${filterFor(code)} )`;
      }
    } else if (sc.form === "wide") {
      for (const code of CODES) if (m.columns[code]) codes[code] = `${fn} ( ${daxCol(T, m.columns[code].name)} )`;
    } else {
      codes.AC = `${fn} ( ${daxCol(T, m.column.name)} )`;
    }
    if (sc.py === "timeIntelligence") {
      codes.PY = [
        `VAR SelectedDates = VALUES ( ${daxCol(CALENDAR, "Date")} )`,
        "RETURN",
        `\tCALCULATE ( ${daxBracket(`${m.name} AC`)}, REMOVEFILTERS ( ${daxTable(CALENDAR)} ), TREATAS ( SelectedDates, ${daxCol(CALENDAR, "Next year date")} ) )`,
      ].join("\n");
    }
    byMetric.set(m.name.toLowerCase(), Object.keys(codes));
    for (const code of CODES) if (codes[code]) measures.push({ name: `${m.name} ${code}`, code, metric: m.name, dax: codes[code], format: m.format });
  }
  for (const m of metrics) {
    if (!m.ratio) continue;
    const num = byMetric.get(cleanName(m.numerator, "").toLowerCase());
    const den = byMetric.get(cleanName(m.denominator, "").toLowerCase());
    need(num, `metric ${m.name}: numerator '${m.numerator}' is not a metric with measures. Metrics: ${[...byMetric.keys()].join(", ")}.`);
    need(den, `metric ${m.name}: denominator '${m.denominator}' is not a metric with measures.`);
    const n = cleanName(m.numerator, ""), d = cleanName(m.denominator, "");
    const realName = (k) => metrics.find((x) => x.name.toLowerCase() === k.toLowerCase()).name;
    for (const code of CODES) {
      if (!num.includes(code) || !den.includes(code)) continue;
      measures.push({ name: `${m.name} ${code}`, code, metric: m.name, format: m.format,
        dax: `DIVIDE ( ${daxBracket(`${realName(n)} ${code}`)}, ${daxBracket(`${realName(d)} ${code}`)} )` });
    }
  }
  // Measures and columns share one namespace per table: a column that collides is renamed and hidden.
  const mnames = new Set();
  for (const m of measures) {
    need(!mnames.has(m.name.toLowerCase()), `two measures would both be named '${m.name}'. Rename a metric.`);
    mnames.add(m.name.toLowerCase());
  }
  for (const c of fact.cols) {
    if (!mnames.has(c.name.toLowerCase())) continue;
    const old = c.name;
    fact.taken.delete(old.toLowerCase());
    for (const x of mnames) fact.taken.add(x);
    c.name = uniqueName(`${old} Amt`, fact.taken);
    c.hidden = true;
    assumptions.push(`${fact.name}[${old}] is renamed ${c.name} and hidden: a measure has its name.`);
    // Measures were written against the old name; rewrite them.
    for (const m of measures) m.dax = m.dax.split(daxCol(fact.name, old)).join(daxCol(fact.name, c.name));
  }
  return measures;
}

// --- relationships ------------------------------------------------------------------------

function buildRelationships(models, joins, fact, dateCol, assumptions) {
  const byStem = new Map(models.map((m) => [m.stem, m]));
  const rels = [];
  // A many-to-one relationship lets the one side filter the many side. `filtersOf(t)` is every table
  // that already filters t, t included. A new relationship from F to D is ambiguous when some table
  // already filters both, because it would then reach F along two paths; it is written inactive.
  const filtersOf = (t, seen = new Set()) => {
    if (seen.has(t)) return seen;
    seen.add(t);
    for (const r of rels) if (r.active && r.fromTable === t) filtersOf(r.toTable, seen);
    return seen;
  };
  const relNames = new Set();
  const push = (fromT, fromC, toT, toC, note) => {
    const a = filtersOf(fromT);
    const ambiguous = [...filtersOf(toT)].some((x) => a.has(x));
    const name = uniqueName(`${fromT}_${toT}`.replace(/[^A-Za-z0-9_]/g, "_"), relNames);
    rels.push({ name, fromTable: fromT, fromColumn: fromC, toTable: toT, toColumn: toC, active: !ambiguous });
    if (ambiguous) assumptions.push(`Relationship ${fromT}[${fromC}] to ${toT}[${toC}] is written inactive: an active one would give a second filter path between the same tables.`);
    if (note) assumptions.push(note);
  };

  for (const j of joins) {
    const f = byStem.get(j.fromTable), d = byStem.get(j.toTable);
    if (!f || !d) continue;
    const fc = f.cols.find((c) => c.source === j.fromColumn);
    const dc = d.cols.find((c) => c.source === j.toColumn);
    if (!fc || !dc) continue;
    if (fc.kind !== dc.kind) {
      assumptions.push(`No relationship from ${f.name}[${fc.name}] to ${d.name}[${dc.name}]: one is ${fc.kind} and the other ${dc.kind}.`);
      continue;
    }
    // The one side must hold unique values as the engine compares them: text case-insensitively,
    // and a blank written as 0 or "" counts as a value.
    const seen = new Map();
    for (const r of d.rows) {
      const raw = String(r[dc.index] === undefined ? "" : r[dc.index]).trim();
      let k = isBlank(raw) ? (dc.kind === "text" ? "" : "0") : raw;
      if (dc.kind === "text") k = k.toLowerCase(); else k = plainNumber(k);
      seen.set(k, (seen.get(k) || 0) + 1);
    }
    const dup = [...seen.entries()].find(([, n]) => n > 1);
    need(!dup, `${d.name}[${dc.name}] would be the one side of a relationship from ${f.name}[${fc.name}], `
      + `but it holds '${dup && dup[0]}' ${dup && dup[1]} times${dc.kind === "text" ? " (compared without case, as Power BI does)" : ""}`
      + `${dc.blanks ? `, counting blanks, which are written as ${dc.kind === "text" ? "empty text" : "0"}` : ""}. `
      + "A one side with duplicates loads and then fails. De-duplicate the source, or skip the table with "
      + `tables: [{source: "${d.stem}", role: "skip"}].`);
    push(f.name, fc.name, d.name, dc.name, j.orphanRows
      ? `${j.orphanRows} row(s) of ${f.name} carry a ${fc.name} that ${d.name} does not have (for example ${j.orphanExamples.join(", ")}); they show as a (Blank) member.`
      : null);
  }
  if (dateCol) push(fact.name, dateCol.col.name, CALENDAR, "Date", null);
  return rels;
}

function relationshipsText(rels) {
  const end = (t, c) => `${tmdlName(t)}.${tmdlName(c)}`;
  return `${rels.map((r) => [`relationship ${r.name}`,
    ...(r.active ? [] : ["\tisActive: false"]),
    `\tfromColumn: ${end(r.fromTable, r.fromColumn)}`,
    `\ttoColumn: ${end(r.toTable, r.toColumn)}`].join("\n")).join("\n\n")}\n`;
}

// --- the report -----------------------------------------------------------------------------

function pbipText(name) {
  return wr.serialise({ $schema: SCHEMA_URL.pbip, version: "1.0",
    artifacts: [{ report: { path: `${name}.Report` } }], settings: { enableAutoRecovery: true } });
}
function pbirText(name) {
  return wr.serialise({ $schema: SCHEMA_URL.pbir, version: "4.0",
    datasetReference: { byPath: { path: `../${name}.SemanticModel` } } });
}
function pbismText() {
  return wr.serialise({ $schema: SCHEMA_URL.pbism, version: "4.2", settings: {} });
}
/** report.json with publicCustomVisuals taken from the visual types the written pages use. */
function reportJsonText(customVisuals) {
  const r = {
    $schema: SCHEMA_URL.report,
    themeCollection: { baseTheme: { name: BASE_THEME,
      reportVersionAtImport: { visual: "2.11.0", page: "2.1.0", report: "3.0.0" }, type: "SharedResources" } },
  };
  if (customVisuals.length) r.publicCustomVisuals = customVisuals;
  return wr.serialise(r);
}
/** A custom visual's type ends in a 32-digit hex id; built-in types (textbox, slicer) do not. */
const isCustomVisualType = (t) => typeof t === "string" && /[0-9A-F]{32}$/i.test(t);

/** Every binding in a spec must name a table and a column or measure the written model has. */
function checkBindings(spec, modelIndex) {
  const problems = [];
  for (const p of spec.pages || []) {
    for (const v of (p && p.visuals) || []) {
      for (const [role, list] of Object.entries((v && v.bindings) || {})) {
        for (const b of Array.isArray(list) ? list : []) {
          let ref;
          try { ref = typeof b === "string" ? wr.splitRef(b) : { ...b }; } catch (e) { problems.push(`${p.name}/${v.name} ${role}: ${e.message}`); continue; }
          const kind = ref.kind || wr.ROLE_FIELD_KIND[role] || "Measure";
          const t = modelIndex.get(String(ref.entity).toLowerCase());
          if (!t) { problems.push(`${p.name}/${v.name} ${role}: no table '${ref.entity}'`); continue; }
          const has = (kind === "Column" ? t.columns : t.measures).has(String(ref.property).toLowerCase());
          if (!has) problems.push(`${p.name}/${v.name} ${role}: ${ref.entity} has no ${kind.toLowerCase()} '${ref.property}'`);
        }
      }
    }
  }
  return problems;
}

/** The model as profile_project sees it, built from the texts in memory, before anything is written. */
function modelFromTexts(files) {
  const model = { present: true, files: [], sources: [], tables: [] };
  for (const [rel, text] of Object.entries(files)) {
    if (!rel.endsWith(".tmdl")) continue;
    model.files.push(rel);
    model.sources.push({ rel, hadBom: false, text, lines: tmdl.classifyLines(text), tree: tmdl.TREE.parseTree(text) });
    for (const tb of tmdl.parseTmdl(text).tables) model.tables.push({ ...tb, file: rel });
  }
  return model;
}

function proposeSpec(model, fact, metrics, sc, rels, assumptions) {
  // Every question is ranked here, not the top 25 propose_pages sees, because the narrowing below
  // would otherwise start from a list that key and date-part columns have already filled.
  const profile = prof.profileProject({ model }, { rankLimit: 1e6 });
  if (!profile.ok) return { error: profile.error };
  assumptions.push("Pages: profile_project ran on the model text in memory, before anything was written; the same files are on disk now.");
  // Pages cut by the actuals: keep the questions that rank an AC source column, and never use the
  // scenario column, or a table it relates to, as a page's category.
  const acCols = new Set(metrics.filter((m) => !m.ratio).map((m) => `${fact.name}[${(m.column || m.columns.AC).name}]`));
  const scenarioDim = sc.form === "long" ? `${fact.name}[${sc.column.name}]` : null;
  const scenarioTables = new Set(sc.form === "long" ? rels.filter((x) => x.fromTable === fact.name && x.fromColumn === sc.column.name).map((x) => x.toTable.toLowerCase()) : []);
  const ranked = (profile.signal && profile.signal.ranked) || [];
  const keep = ranked.filter((q) => q.dimension !== scenarioDim && !scenarioTables.has(q.dimension.replace(/\[.*$/, "").toLowerCase()));
  const onActuals = keep.filter((q) => acCols.has(q.measureColumn));
  profile.signal = { ...profile.signal, ranked: onActuals.length ? onActuals : keep, rankedCount: (onActuals.length ? onActuals : keep).length };
  assumptions.push(`Pages: before propose_pages, the ranked questions were narrowed to those on the actuals (${[...acCols].join(", ")})`
    + `${scenarioDim ? `, leaving out ${scenarioDim}${scenarioTables.size ? ` and the columns of ${[...scenarioTables].join(", ")}` : ""} as page categories` : ""}; a direct propose_pages call does not narrow them.`);
  const r = propose.proposePages(profile, {});
  if (!r.ok) return { error: r.error };
  assumptions.push(`Pages: proposed from the written model, ${r.spec.pages.length} page(s) of ${r.subjectMeasure}`
    + `${r.comparisonBound ? ` against ${[r.comparisonBound.plan, r.comparisonBound.previousYear].filter(Boolean).join(" and ")}` : " with no comparison"}, ordered by measured effect.`);
  return { spec: r.spec, rationale: r.rationale };
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ym = (iso) => iso.slice(0, 7);
const shiftMonths = (m, k) => { const [y, mo] = m.split("-").map(Number); const t = y * 12 + (mo - 1) + k; return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`; };

/**
 * The reporting window of a proposed page: from the start of the latest year that has actuals (the
 * fiscal year when one is set) to the last month with actuals. All scenarios on the page are then
 * read over the same months; previous year is the same months a year earlier. Returns the months,
 * a label, and a line for each bound comparison that does not cover the whole window.
 */
function reportingWindow(fact, sc, dateCol, fiscalStart, bound) {
  const di = dateCol.index;
  const monthsWhere = (pred) => new Set(fact.rows.filter(pred)
    .map((r) => String(r[di] || "").trim()).filter((v) => !isBlank(v)).map((v) => ym(ps.toIsoDate(v, dateCol.dayFirst))));
  const val = (r, c) => String(r[c.index] === undefined ? "" : r[c.index]).trim();
  const memberOf = {};
  if (sc.form === "long") for (const [v, code] of Object.entries(sc.members)) memberOf[code] = v;
  const covers = (code) => {
    if (sc.form === "long") return memberOf[code] === undefined ? null : monthsWhere((r) => val(r, sc.column) === memberOf[code]);
    if (sc.form === "wide") return sc.columns[code] ? monthsWhere((r) => !isBlank(val(r, sc.columns[code]))) : null;
    return code === "AC" ? monthsWhere(() => true) : null;
  };
  const ac = covers("AC");
  if (!ac || !ac.size) return null;
  const actualDates = fact.rows.filter((r) => sc.form !== "long" || val(r, sc.column) === memberOf.AC)
    .map((r) => String(r[di] || "").trim()).filter((v) => !isBlank(v)).map((v) => ps.toIsoDate(v, dateCol.dayFirst)).sort();
  const lastDate = actualDates[actualDates.length - 1];
  const last = ym(lastDate);
  const [ly, lm] = last.split("-").map(Number);
  const startYear = lm >= fiscalStart ? ly : ly - 1;
  const start = `${startYear}-${String(fiscalStart).padStart(2, "0")}`;
  const months = [];
  for (let m = start; m <= last; m = shiftMonths(m, 1)) months.push(m);
  const limits = [];
  for (const role of bound) {
    let have;
    // Exact by month: the PY measure counts each prior date in its next-year date, which is always in
    // the same month a year later (29 February in 28 February), so prior month m covers month m + 12.
    if (role === "PY" && sc.py === "timeIntelligence") have = new Set([...ac].map((m) => shiftMonths(m, 12)));
    else have = covers(role);
    if (!have) continue;
    const missing = months.filter((m) => !have.has(m));
    if (missing.length) limits.push(`${role} has no rows for ${missing.join(", ")}${role === "PY" ? " (a year earlier)" : ""}, so those months compare against nothing`);
  }
  const monthStartsOnly = actualDates.every((d) => d.endsWith("-01"));
  const lastDay = new Date(Date.UTC(ly, lm, 0)).getUTCDate();
  if (!monthStartsOnly && Number(lastDate.slice(8, 10)) < lastDay) {
    limits.push(`the actuals stop on ${lastDate}, so ${last} is a part month; previous year follows the same days through the calendar, plan does not`);
  }
  const name = (m) => `${MONTH_NAMES[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
  const label = months.length === 1 ? name(months[0]) : `${name(months[0])} to ${name(months[months.length - 1])}`;
  return { months, label, limits, fiscal: fiscalStart !== 1 };
}

/** A native slicer on the calendar's YearMonth, preset to the window, in the double-nested form. */
function windowSlicer(window) {
  return {
    name: "periodSlicer", visualType: "slicer",
    position: { x: 16, y: 16, z: 2000, width: 260, height: 64, tabOrder: 0 },
    bindings: { Values: [{ entity: CALENDAR, property: "YearMonth", kind: "Column" }] },
    properties: {
      "data.mode": "Dropdown", "data.textSize": 10, "general.orientation": 0, "slicerHeader.show": true,
      "general.filter": { __raw: { filter: {
        Version: 2,
        From: [{ Name: "c", Entity: CALENDAR, Type: 0 }],
        Where: [{ Condition: { In: {
          Expressions: [{ Column: { Expression: { SourceRef: { Source: "c" } }, Property: "YearMonth" } }],
          Values: window.months.map((m) => [{ Literal: { Value: `'${m}'` } }]),
        } } }],
      } } },
    },
    visualContainerObjects: { "title.show": false },
  };
}

/** Adds the period slicer to every proposed page and names the window in each table's title. */
function applyReportingWindow(spec, fact, sc, dateInfo, fiscalStart, assumptions) {
  if (!dateInfo) return null;
  const roles = new Set();
  for (const p of spec.pages || []) for (const v of p.visuals || []) {
    if (v.bindings && v.bindings.Plan) roles.add("PL");
    if (v.bindings && v.bindings.PreviousYear) roles.add("PY");
    if (v.bindings && v.bindings.Forecast) roles.add("FC");
  }
  const w = reportingWindow(fact, sc, dateInfo.col, fiscalStart, [...roles]);
  if (!w) return null;
  for (const p of spec.pages || []) {
    for (const v of p.visuals || []) {
      if (v.position) { v.position = { ...v.position, y: 96, height: 608 }; }
      if (v.properties && typeof v.properties["titleSettings.text"] === "string") {
        v.properties["titleSettings.text"] = `${v.properties["titleSettings.text"]}, ${w.label}`;
      }
    }
    p.visuals = [windowSlicer(w), ...(p.visuals || [])];
  }
  assumptions.push(`Period: each proposed page opens on ${w.label} (${w.fiscal ? "fiscal" : "calendar"} year to date, ending with the last month that has actuals), set as the default of a slicer on ${CALENDAR}[YearMonth] that the reader can change; ${[...roles].length ? `${[...roles].join(" and ")} ${[...roles].length > 1 ? "are" : "is"} read over the same months` : "no comparison is bound"}. With nothing selected the page would add up every year in the data.`);
  for (const l of w.limits) assumptions.push(`Period: ${l}.`);
  assumptions.push(`Period: the table titles name ${w.label}; a title does not follow the slicer when the reader changes it, and Tables+ may draw its own automatic title instead (see warnings), so the slicer is where the window shows for certain.`);
  return w;
}

/** A column that gives the order of the rows of a statement: Sort, Order, LineNo, AccountSort. */
const ORDER_LAST_WORDS = new Set(["sort", "order", "ordinal", "sequence", "seq", "position"]);
function isOrderName(name) {
  const w = words(name);
  if (!w.length) return false;
  if (ORDER_LAST_WORDS.has(w[w.length - 1])) return true;
  return w.length === 2 && w[0] === "line" && ["no", "nr", "number", "num"].includes(w[1]);
}

/**
 * Statement order. For each proposed table whose category's own table carries an order column, that
 * column is bound as the category's sortByColumn in the model and the table sorts by the model's
 * order (sortSettings.categorySort 0) instead of by value. An order column counts only when every
 * category value has exactly one order value. Without one the sort is left as proposed and said.
 */
function applyStatementOrder(spec, models, assumptions) {
  const touched = new Set();
  for (const page of spec.pages || []) {
    for (const v of page.visuals || []) {
      const cat = v.bindings && v.bindings.Category && v.bindings.Category[0];
      if (!cat || typeof cat !== "object") continue;
      const m = models.find((x) => x.name === cat.entity);
      const c = m && m.cols.find((x) => x.name === cat.property);
      if (!c) continue;
      const cands = m.cols.filter((x) => x !== c && (x.kind === "integer" || x.kind === "number") && isOrderName(x.source));
      let order = null;
      for (const o of cands) {
        const seen = new Map();
        let ok = true;
        for (const r of m.rows) {
          const k = String(r[c.index] === undefined ? "" : r[c.index]).trim();
          const val = String(r[o.index] === undefined ? "" : r[o.index]).trim();
          if (seen.has(k) && seen.get(k) !== val) { ok = false; break; }
          seen.set(k, val);
        }
        if (ok) { order = o; break; }
        assumptions.push(`Pages: ${m.name}[${o.name}] reads like an order column, but some ${c.name} values carry two different orders in it, so it is not used.`);
      }
      if (!order) {
        assumptions.push(`Pages: the rows of ${m.name}[${c.name}] on page ${page.displayName} are sorted by value, as propose_pages sorts them; ${m.name} has no order column (such as Sort, Order or LineNo) to keep a statement order.`);
        continue;
      }
      c.sortBy = order.name;
      touched.add(m);
      delete v.properties["sortSettings.columnName"];
      v.properties["sortSettings.categorySort"] = 0;
      assumptions.push(`Pages: the rows of ${m.name}[${c.name}] on page ${page.displayName} follow ${m.name}[${order.name}] (sortByColumn in the model; the table sorts by the model's order, categorySort 0), not value.`);
    }
  }
  return [...touched];
}

// --- writing --------------------------------------------------------------------------------

function checkOutPath(outPath) {
  need(typeof outPath === "string" && outPath.length, "outPath is required: the absolute path of a "
    + "folder that does not exist yet, or is empty, to hold the project.");
  need(path.isAbsolute(outPath), `outPath must be absolute, not '${outPath}'.`);
  if (fs.existsSync(outPath)) {
    const st = fs.statSync(outPath);
    need(st.isDirectory(), `outPath ${outPath} exists and is not a folder. This tool never overwrites a file.`);
    const inside = fs.readdirSync(outPath);
    need(!inside.length, `outPath ${outPath} is not empty (${inside.slice(0, 5).join(", ")}${inside.length > 5 ? ", ..." : ""}). `
      + "This tool writes only into a new or empty folder and never overwrites or deletes a file. "
      + "Choose a new folder.");
  }
}

function writeAll(outPath, files) {
  fs.mkdirSync(outPath, { recursive: true });
  const written = [];
  for (const [rel, content] of Object.entries(files)) {
    const abs = path.join(outPath, ...rel.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    const buf = Buffer.from(content, "utf8");
    // `wx` fails if the file exists, so nothing is ever overwritten.
    fs.writeFileSync(abs, buf, { flag: "wx" });
    written.push({ path: rel, bytes: buf.length });
  }
  return written;
}

// --- the tool ------------------------------------------------------------------------------

/** Refuses a model above the cell ceiling. `dates` per table: date columns, written as three cells. */
function checkCells(list) {
  const cells = list.reduce((s, { t, dates }) => s + t.rows.length * (t.header.length + 2 * dates), 0);
  need(cells <= LIMITS.cells, `The tables hold ${cells} cells as embedded rows, above the ${LIMITS.cells}-cell `
    + "ceiling for a model that opens reliably. Pass a keep-list of the columns you need (tables: "
    + "[{source, columns: [...]}]), skip tables you do not need (role: \"skip\"), or pre-aggregate the "
    + `source. Rows per table: ${list.map(({ t }) => `${t.file} ${t.rows.length} x ${t.header.length}`).join(", ")}.`);
}

function writeProject(args = {}, deps = {}) {
  need(args && typeof args === "object" && !Array.isArray(args), "write_project takes an object of arguments.");
  knownKeys(args, INPUT_KEYS, "write_project");
  const name = args.name;
  need(typeof name === "string" && /^[A-Za-z0-9_-]{1,60}$/.test(name),
    "name is required and becomes <name>.pbip, <name>.Report and <name>.SemanticModel, so it must be "
    + "1 to 60 letters, digits, underscores or hyphens.");
  checkOutPath(args.outPath);
  const outPath = path.resolve(args.outPath);
  need(args.source && typeof args.source === "object" && !Array.isArray(args.source),
    "source is required: {\"path\": \"<absolute path to a folder of flat files, or one file>\"}.");
  knownKeys(args.source, new Set(["path"]), "source");

  const overrides = args.tables === undefined ? [] : args.tables;
  need(Array.isArray(overrides), "tables must be an array of overrides.");
  for (const [i, o] of overrides.entries()) {
    need(o && typeof o === "object", `tables[${i}] must be an object.`);
    knownKeys(o, TABLE_KEYS, `tables[${i}]`);
    need(typeof o.source === "string" && o.source, `tables[${i}].source is required: the file name, or its name without the extension.`);
    if (o.role !== undefined) need(["fact", "dimension", "skip"].includes(o.role), `tables[${i}].role must be fact, dimension or skip.`);
    if (o.types !== undefined) need(o.types && typeof o.types === "object" && !Array.isArray(o.types), `tables[${i}].types must be a map of column to type.`);
    if (o.hidden !== undefined) need(Array.isArray(o.hidden), `tables[${i}].hidden must be an array of column names.`);
    if (o.columns !== undefined) need(Array.isArray(o.columns) && o.columns.length, `tables[${i}].columns must be a non-empty array of the columns to keep.`);
    if (o.name !== undefined) need(typeof o.name === "string" && cleanName(o.name, ""), `tables[${i}].name must be a non-empty string.`);
  }
  if (args.date !== undefined) {
    need(args.date && typeof args.date === "object", "date must be an object: {column, fiscalYearStartMonth}.");
    knownKeys(args.date, new Set(["column", "fiscalYearStartMonth"]), "date");
    if (args.date.fiscalYearStartMonth !== undefined) {
      need(Number.isInteger(args.date.fiscalYearStartMonth) && args.date.fiscalYearStartMonth >= 1
        && args.date.fiscalYearStartMonth <= 12, "date.fiscalYearStartMonth must be a whole number from 1 to 12.");
    }
  }
  need(args.pages === undefined || args.pages === "propose" || (args.pages && typeof args.pages === "object" && !Array.isArray(args.pages)),
    "pages must be a write_report spec ({pages: [...]}), the string \"propose\", or left out.");

  const assumptions = [];
  const src = readSource(args.source.path, overrides);

  // Keep-lists, then the profile on what is kept.
  for (const t of src.tables) {
    const keep = t.override && t.override.columns;
    if (keep) {
      const idx = keep.map((k) => {
        const i = t.header.findIndex((h) => h.toLowerCase() === String(k).toLowerCase());
        need(i >= 0, `tables[${t.stem}].columns names '${k}', which is not a column of ${t.file}. Columns: ${t.header.join(", ")}.`);
        return i;
      });
      t.header = idx.map((i) => t.header[i]);
      t.rows = t.rows.map((r) => idx.map((i) => (r[i] === undefined ? "" : r[i])));
    }
  }
  // The size ceiling, first on the plain cell count so a source far too large is refused before it
  // is profiled; again below once each date counts as its three parts. A file named like a date
  // table is left out of this first count, because a date table is set aside by default.
  checkCells(src.tables.filter((t) => !/date|calendar|period|time/i.test(t.stem)
    || (t.override && t.override.role)).map((t) => ({ t, dates: 0 })));
  for (const t of src.tables) {
    const columns = ps.profileColumns(t.header, t.rows);
    const keyColumn = ps.keyColumnOf(t.stem, columns);
    const rec = { table: t.stem, file: t.file, header: t.header, rows: t.rows, columns, keyColumn };
    rec.role = ps.classifyFile(t.stem, columns, t.rows);
    if (rec.role === "fact") rec.keyColumn = keyColumn && columns.find((c) => c.name === keyColumn && c.unique) ? keyColumn : null;
    if (t.override && t.override.role) rec.role = t.override.role;
    t.rec = rec;
  }

  // From a folder, a file becomes a table only when it is plausibly data: it reads as a fact, has a
  // date or an amount column, or joins another file in the folder. A headerless file with one column
  // never is. A file the caller names in `tables` is always loaded.
  if (src.isFolder) {
    const joins0 = ps.joinSignals(src.tables.map((t) => t.rec));
    for (const t of src.tables) {
      if (t.required) continue;
      const r = t.rec;
      const hasDate = r.columns.some((c) => c.type === "date");
      const hasAmount = r.columns.some((c) => (c.type === "integer" || c.type === "number") && !keyish(c.name));
      const joined = joins0.some((j) => j.fromTable === r.table || j.toTable === r.table);
      let reason = null;
      if (t.headerless && t.header.length === 1) reason = "one column and no header row, so not a table of data";
      else if (r.role === "date") reason = null;                       // set aside below, with its own note
      else if (r.role === "fact" || hasDate || hasAmount || joined) reason = null;
      else reason = `reads as ${r.role === "unknown" ? "neither a fact nor a dimension" : `a ${r.role} table`}, joins no other file, and has no date or amount column`;
      if (reason) { t.notData = true; src.skipped.push({ file: t.file, reason: `${reason}; name it in tables to load it` }); }
    }
    src.tables = src.tables.filter((t) => !t.notData);
    need(src.tables.length, `no file under ${args.source.path} reads as data: ${src.skipped.map((s) => `${s.file}: ${s.reason}`).join("; ")}.`);
  }
  for (const t of src.tables) {
    if (t.headerless) src.notes.push(`${t.file} has no header row, so its columns are named col1..col${t.header.length}.`);
  }

  // Calendar-like source tables are set aside by default: the model's own Calendar replaces them.
  for (const t of src.tables) {
    if (t.rec.role === "date") {
      t.dropped = true;
      assumptions.push(`${t.file} reads as a date table and is left out: the model's Calendar replaces it. Pass tables: [{source: "${t.stem}", role: "dimension"}] to keep it.`);
      src.skipped.push({ file: t.file, reason: "a date table; the model's Calendar replaces it. Pass it with role: \"dimension\" to keep it" });
    }
  }
  const kept = src.tables.filter((t) => !t.dropped);

  // The size ceiling, on the cells as written (each date is three), before any text is built.
  checkCells(kept.map((t) => ({ t, dates: t.rec.columns.filter((c) => c.type === "date").length })));

  // Model names: cleaned, reserved names renamed, unique.
  const tableNames = new Set([CALENDAR.toLowerCase()]);
  const models = [];
  for (const t of kept) {
    const wanted = cleanName(t.override && t.override.name ? t.override.name : t.stem, "Table");
    let n = wanted;
    if (RESERVED_TABLE_NAMES.has(n.toLowerCase())) {
      n = `${wanted} Data`;
      assumptions.push(`Table ${wanted} is named ${n}: '${wanted}' is a name Power BI or DAX reserves${wanted.toLowerCase() === "calendar" ? ", or the model's own calendar uses" : ""}.`);
    }
    n = uniqueName(n, tableNames);
    if (n !== t.stem && !RESERVED_TABLE_NAMES.has(wanted.toLowerCase())) assumptions.push(`${t.file} is the table ${n}.`);
    models.push(modelTable(t, n, assumptions));
  }
  for (const m of models) m.rec = src.tables.find((t) => t.stem === m.stem).rec;

  // The fact the measures live on.
  let dateRef = args.date && args.date.column;
  let dateTableHint = null;
  if (typeof dateRef === "string") {
    const mm = /^(.+)\[(.+)\]$/.exec(dateRef);
    if (mm) dateTableHint = mm[1].replace(/^'|'$/g, "").toLowerCase();
  }
  let facts = models.filter((m) => m.rec.role === "fact");
  if (!facts.length) {
    // profile_source reads a small table with a scenario-like column as a scenario list, and a
    // table without keys as unknown. With no fact found, the largest table that has a date and a
    // number is the fact, and the assumption says so.
    const numeric = (m) => m.cols.some((c) => (c.kind === "integer" || c.kind === "number") && !keyish(c.source));
    const cand = models.filter((m) => !(m.override && m.override.role === "dimension") && numeric(m)
      && m.cols.some((c) => c.kind === "date")).sort((a, b) => b.rows.length - a.rows.length);
    if (cand.length) {
      cand[0].rec.role = "fact";
      facts = [cand[0]];
      assumptions.push(`${cand[0].file} is the fact table: it has dates and amounts. Pass tables: [{source, role: "fact"}] to choose another.`);
    }
  }
  let fact;
  if (dateTableHint) fact = models.find((m) => m.stem.toLowerCase() === dateTableHint || m.name.toLowerCase() === dateTableHint);
  if (!fact) fact = facts.slice().sort((a, b) => (b.cols.some((c) => c.kind === "date") - a.cols.some((c) => c.kind === "date")) || (b.rows.length - a.rows.length))[0];
  need(fact, "No table reads as a fact table (amounts beside dates or keys). Name it: tables: "
    + `[{source: "<file>", role: "fact"}]. Tables read: ${models.map((m) => `${m.file} (${m.rec.role})`).join(", ")}.`);
  if (facts.length > 1) assumptions.push(`Measures live on ${fact.name}; the other fact table(s) ${facts.filter((f) => f !== fact).map((f) => f.name).join(", ")} are loaded with no measures and no calendar relationship.`);

  // The date column and the calendar.
  let dateCol = null;
  if (dateRef !== undefined) {
    const c = findColumn(fact, String(dateRef), "date.column");
    need(c.kind === "date", `date.column ${fact.name}[${c.source}] is ${c.kind}, not a date.`);
    dateCol = c;
  } else {
    dateCol = fact.cols.find((c) => c.kind === "date") || null;
    if (dateCol) assumptions.push(`Date: ${fact.name}[${dateCol.name}] relates to the Calendar.`);
  }
  let dateInfo = null;
  if (dateCol) {
    const isos = fact.rows.map((r) => String(r[dateCol.index] || "").trim()).filter((v) => !isBlank(v))
      .map((v) => ps.toIsoDate(v, dateCol.dayFirst)).sort();
    need(isos.length, `${fact.name}[${dateCol.source}] holds no dates.`);
    const months = new Set(isos.map((d) => d.slice(0, 7))).size;
    dateInfo = { col: dateCol, first: isos[0], last: isos[isos.length - 1], months };
    if (dateCol.blanks) assumptions.push(`${dateCol.blanks} row(s) of ${fact.name} have no ${dateCol.name}; they count in every total and sit under no calendar date.`);
  } else {
    assumptions.push("No date column on the fact table, so there is no Calendar and no time intelligence.");
  }

  const joins = ps.joinSignals(models.map((m) => m.rec));
  const sc = resolveScenarios(args.scenarios, fact, models, joins, dateInfo, assumptions);
  const metrics = resolveMetrics(args.metrics, fact, sc, assumptions);

  let fiscalStart = 1;
  if (dateInfo) {
    if (args.date && args.date.fiscalYearStartMonth !== undefined) fiscalStart = args.date.fiscalYearStartMonth;
    else {
      const fs_ = ps.fiscalSignal(src.tables.map((t) => t.rec));
      if (fs_ && fs_.startMonth && fs_.startMonth !== 1) {
        fiscalStart = fs_.startMonth;
        assumptions.push(`Fiscal year: starts in ${fs_.startMonthName}, read from ${fs_.table}[${fs_.column}]. Pass date.fiscalYearStartMonth to change it.`);
      }
    }
    if (fiscalStart !== 1) assumptions.push(`Fiscal Year is named by the calendar year in which it ends (the fiscal year that starts in month ${fiscalStart} of 2025 is Fiscal Year 2026); Fiscal Period 1 is month ${fiscalStart}.`);
    assumptions.push(`Calendar: ${dateInfo.first.slice(0, 4)}-01-01 to ${dateInfo.last}, the last date in ${fact.name}[${dateCol.name}].`);
  }

  // Coverage per scenario, so a variance over unequal windows is stated rather than discovered.
  if (sc.form === "long" && dateInfo) {
    for (const [v, code] of Object.entries(sc.members)) {
      const ds = fact.rows.filter((r) => String(r[sc.column.index]).trim() === v)
        .map((r) => String(r[dateCol.index] || "").trim()).filter((x) => !isBlank(x))
        .map((x) => ps.toIsoDate(x, dateCol.dayFirst)).sort();
      if (ds.length) assumptions.push(`${code} (${v}) covers ${ds[0]} to ${ds[ds.length - 1]}; a variance compares like periods only where both scenarios have rows.`);
    }
  }

  fact.measures = buildMeasures(fact, metrics, sc, dateInfo, assumptions);
  if (sc.py === "timeIntelligence") {
    assumptions.push("PY measures read the same days one year earlier: each date of the prior year counts in its date one year later (the Calendar's Next year date column, TREATAS, with the calendar filter removed), and 29 February counts in 28 February. A period that stops mid-month is compared with the same days a year earlier, and the months of previous year add up to its year.");
  }
  const rels = buildRelationships(models, joins, fact, dateInfo, assumptions);

  // Column and cell encodings the reader must know.
  const stats = { lineBreaks: 0 };
  for (const m of models) {
    for (const c of m.cols) {
      if (c.blanks && (c.kind === "integer" || c.kind === "number")) assumptions.push(`${m.name}[${c.name}]: ${c.blanks} blank cell(s) are written as 0, because an embedded row takes constants only. Sums are unchanged; counts and averages include them.`);
      if (c.blanks && c.kind === "text") assumptions.push(`${m.name}[${c.name}]: ${c.blanks} blank cell(s) are written as empty text.`);
    }
  }

  // The files.
  const R = `${name}.Report`, S = `${name}.SemanticModel`;
  const modelFiles = {};
  modelFiles[`${S}/definition/database.tmdl`] = `database\n\tcompatibilityLevel: ${COMPATIBILITY_LEVEL}\n`;
  const order = [...models.map((m) => m.name), ...(dateInfo ? [CALENDAR] : [])];
  modelFiles[`${S}/definition/model.tmdl`] = ["model Model", "\tculture: en-US",
    "\tdefaultPowerBIDataSourceVersion: powerBI_V3", "\tsourceQueryCulture: en-US", "",
    "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
    ...order.map((n) => `ref table ${tmdlName(n)}`), ""].join("\n");
  if (rels.length) modelFiles[`${S}/definition/relationships.tmdl`] = relationshipsText(rels);
  const fileNames = new Set();
  const tableFile = (n) => uniqueName(DEVICE_NAMES.test(n) ? `${n}_table` : n, fileNames);
  let datatableBytes = 0;
  const modelSummary = [];
  for (const m of models) {
    const lay = layout(m, assumptions);
    const text = tableText(m, lay, stats);
    datatableBytes += Buffer.byteLength(text, "utf8");
    m.fileRel = `${S}/definition/tables/${tableFile(m.name)}.tmdl`;
    modelFiles[m.fileRel] = text;
    m.lay = lay;
    modelSummary.push({ name: m.name, source: m.file, role: m.rec.role === "fact" ? "fact" : "dimension",
      rows: m.rows.length, columns: lay.dt.length + lay.added.length, measures: m.measures.map((x) => x.name) });
  }
  need(datatableBytes <= LIMITS.datatableBytes, `The tables come to ${datatableBytes} bytes of TMDL, above `
    + `the ${LIMITS.datatableBytes}-byte ceiling for a model that opens reliably. Pass a keep-list of the `
    + "columns you need (tables: [{source, columns: [...]}]), skip tables you do not need, or pre-aggregate the source.");
  if (dateInfo) {
    modelFiles[`${S}/definition/tables/${tableFile(CALENDAR)}.tmdl`] = calendarText({
      first: dateInfo.first, last: dateInfo.last, fiscalStart, pyDate: sc.py === "timeIntelligence" });
    modelSummary.push({ name: CALENDAR, source: null, role: "calendar", rows: null, columns: null, measures: [] });
  }
  if (stats.lineBreaks) assumptions.push(`${stats.lineBreaks} text cell(s) held a line break or a tab, written as a space: a DAX string constant in an embedded row has no line-break escape.`);

  // Pages.
  const model = modelFromTexts(modelFiles);
  const index = new Map();
  for (const t of model.tables) {
    index.set(t.name.toLowerCase(), {
      columns: new Set(t.columns.map((c) => c.name.toLowerCase())),
      measures: new Set(t.measures.map((x) => x.name.toLowerCase())),
    });
  }
  let spec;
  let rationale;
  let reportWindow = null;
  if (args.pages === undefined) {
    spec = { pages: [{ name: "p1", displayName: "Page 1" }] };
    assumptions.push("Pages: one empty page, so the project opens. Pass pages as a write_report spec, or \"propose\".");
  } else if (args.pages === "propose") {
    const p = proposeSpec(model, fact, metrics, sc, rels, assumptions);
    if (p.error) {
      spec = { pages: [{ name: "p1", displayName: "Page 1" }] };
      assumptions.push(`Pages: none could be proposed, so the report has one empty page. propose_pages said: ${p.error}`);
    } else {
      spec = p.spec;
      rationale = p.rationale;
      // A sortByColumn changes the category's table file, which is rebuilt before anything is written.
      for (const m of applyStatementOrder(spec, models, assumptions)) modelFiles[m.fileRel] = tableText(m, m.lay, { lineBreaks: 0 });
      reportWindow = applyReportingWindow(spec, fact, sc, dateInfo, fiscalStart, assumptions);
    }
  } else {
    spec = args.pages;
    const problems = checkBindings(spec, index);
    need(!problems.length, `pages binds fields the written model does not have, which renders `
      + `"Something's wrong with one or more fields": ${problems.slice(0, 8).join("; ")}. Measures are named `
      + `'<metric> AC', '<metric> PL' and so on; tables and columns as the model lists them: `
      + modelSummary.map((t) => t.name).join(", ") + ".");
  }
  need(!spec.existingReportPath, "pages.existingReportPath does not apply: write_project creates a new report.");
  const rep = wr.writeReport(spec);
  const customVisuals = [...new Set(Object.entries(rep.files)
    .filter(([p]) => p.endsWith("/visual.json"))
    .map(([, c]) => JSON.parse(c).visual && JSON.parse(c).visual.visualType)
    .filter(isCustomVisualType))].sort();

  const files = {};
  files[`${name}.pbip`] = pbipText(name);
  files[`${R}/definition.pbir`] = pbirText(name);
  files[`${R}/definition/report.json`] = reportJsonText(customVisuals);
  for (const [p, c] of Object.entries(rep.files)) files[`${R}/${p}`] = c;
  files[`${S}/definition.pbism`] = pbismText();
  Object.assign(files, modelFiles);

  // The path limit, on the deepest file, before anything is written.
  const longest = Object.keys(files).map((p) => path.join(outPath, ...p.split("/")))
    .sort((a, b) => b.length - a.length)[0];
  need(longest.length <= LIMITS.pathLength, `The deepest file would be ${longest.length} characters long `
    + `(${longest}), above the ${LIMITS.pathLength}-character limit past which Windows tools and Power BI `
    + "Desktop's Save As fail. Choose a shorter outPath or name.");

  checkOutPath(outPath);
  const written = writeAll(outPath, files);

  // Self-check: the validator, then an independent read-back of what is on disk.
  const validateReport = deps.validateReport || require("./server.js").validateReport;
  const resolveProject = deps.resolveProject || require("./server.js").resolveProject;
  const v = validateReport({ projectPath: outPath });
  const findings = v.ok ? v.findings.slice() : [{ code: "validation-did-not-run", severity: "wont-open", location: outPath, message: v.error }];
  const back = prof.profileProject(resolveProject(outPath));
  const readBack = [];
  if (!back.ok) {
    findings.push({ code: "read-back-failed", severity: "wont-open", location: outPath, message: back.error });
  } else {
    for (const m of models) {
      const t = back.tables.find((x) => x.name === m.name);
      const want = [...m.lay.dt.map((d) => (d.part !== undefined ? "int64" : TMDL_TYPE[d.col.kind])),
        ...m.lay.added.map(() => "dateTime")];
      const got = t ? t.columns.map((c) => c.dataType) : [];
      const ok = t && t.rowCount === m.rows.length && JSON.stringify(got) === JSON.stringify(want);
      readBack.push({ table: m.name, rowsWritten: m.rows.length, rowsRead: t ? t.rowCount : null, ok: Boolean(ok) });
      if (!ok) {
        findings.push({ code: "read-back-mismatch", severity: "wrong-numbers", location: m.name,
          message: `The written table reads back as ${t ? `${t.rowCount} row(s) with column types ${got.join(", ")}` : "absent"}; `
            + `${m.rows.length} row(s) with ${want.join(", ")} were written.` });
      }
    }
  }
  const sev = (s) => findings.filter((f) => f.severity === s).length;

  return {
    ok: true,
    outPath,
    files: written,
    model: {
      tables: modelSummary,
      measures: fact.measures.map((m) => `${fact.name}[${m.name}]`),
      relationships: rels.map((r) => `${r.fromTable}[${r.fromColumn}] -> ${r.toTable}[${r.toColumn}]${r.active ? "" : " (inactive)"}`),
    },
    assumptions: [`Loaded as tables: ${models.map((m) => (m.name === m.stem ? m.file : `${m.file} as ${m.name}`)).join(", ")}.`,
      ...(src.skipped.length ? [`Not loaded: ${src.skipped.map((s) => s.file).join(", ")} (reasons under skipped).`] : []),
      ...src.notes, ...assumptions],
    skipped: src.skipped,
    ...(rationale ? { pageRationale: rationale } : {}),
    ...(reportWindow ? { period: { field: `${CALENDAR}[YearMonth]`, months: reportWindow.months, label: reportWindow.label } } : {}),
    ...(rep.warnings ? { warnings: rep.warnings } : {}),
    validation: {
      rulesRun: v.ok ? v.rulesRun : 0,
      wontOpen: sev("wont-open"), blank: sev("blank"),
      findings,
      readBack,
    },
    notChecked: [
      ...(v.ok ? v.notChecked : []),
      "Whether Power BI Desktop opens the project, computes every embedded table and draws the pages. Only a cold open, a refresh and a screenshot show that.",
    ],
    next: [
      `This project has not been rendered. Open ${name}.pbip in Power BI Desktop, refresh, and look at every page before you hand it over.`,
      "A changed model opens empty until refreshed and saved: after any change to the model, refresh in Power BI Desktop and save before the project is opened again or handed over.",
      ...(sev("wont-open") || sev("blank") ? ["Fix every wont-open and blank finding in validation before opening the project."] : []),
    ],
  };
}

module.exports = { writeProject, LIMITS, cleanName, plainNumber, splitRecords, isCustomVisualType };
