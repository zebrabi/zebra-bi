// Zebra BI chart or table from the selected range.
//
// Runs inside Excel, in an agent that can run Office.js (an async function body with Excel available): reads the
// selected range (header row, one category column, up to four scenario columns), writes a workbook in memory that
// holds the data and a Zebra BI Charts or Tables for Office visual bound to it, and opens that workbook with
// Excel.createWorkbook. No server call, no sign-in: the visual is drawn by the Zebra BI add-in from the cells,
// wherever it is installed.
//
// The stored settings and data are the ones the Zebra BI workbook builder writes for the same add-ins: Charts =
// AppSource WA200004688 (ChartSettings + SourceData + a matrix binding); Tables = AppSource WA200004689
// (ZBITablesSettings + SourceData + zebrabi:matrix-data + a matrix binding).
//
// Settings the agent sets before running (an IBCS title names the measure, unit and period):
const TITLE = null;        // chart title; default "<scenario headers> by <category header>"
const COSTS = false;       // true when an increase is bad (costs, expenses): flips the variance colours
const VISUAL = "auto";     // "chart" | "table" | "auto": auto draws a chart for a time category, a table otherwise

// ---------- scenario roles from the header ----------
const ROLE_RULES = [
  ["Values", /^(ac|act|actual|actuals|cy|current)\b/i],
  ["Forecast", /^(fc|fct|forecast|outlook)\b/i],
  ["Plan", /^(pl|plan|budget|bu|bud|target)\b/i],
  ["PreviousYear", /^(py|ly|prior|previous|last year|prev)\b/i],
];
const ROLE_ORDER = ["Values", "Forecast", "Plan", "PreviousYear"]; // the Charts add-in's measure order

function roleOf(header) {
  for (const [role, re] of ROLE_RULES) if (re.test(String(header).trim())) return role;
  return null;
}

const ROLE_NAME = { Values: "actuals", Forecast: "forecast", Plan: "plan", PreviousYear: "previous year" };

/** Picks the scenario columns. Recognised headers take their role first; the first other numeric column becomes the
 *  actuals only when no header reads as actuals. Empty columns are ignored; other columns are returned as left out. */
function planColumns(header, rows) {
  const found = [];
  const leftOut = [];
  for (let c = 1; c < header.length; c++) {
    const cells = rows.map((r) => r[c]);
    if (cells.every((v) => v === "" || v === null || v === undefined)) continue;
    const role = roleOf(header[c]);
    const numeric = cells.every((v) => v === "" || v === null || v === undefined || (typeof v === "number" && isFinite(v)));
    if (role && !numeric) throw new Error(`Column "${header[c]}" reads as ${ROLE_NAME[role]} but holds text or errors. Fix those cells, or leave the column out of the selection.`);
    if (!numeric) { leftOut.push(String(header[c])); continue; }
    found.push({ c, role });
  }
  if (!found.length) throw new Error("Select a header row, one category column and at least one numeric column.");
  const used = new Map();
  for (const f of found) {
    if (!f.role) continue;
    if (used.has(f.role)) throw new Error(`Two columns read as ${ROLE_NAME[f.role]} ("${header[used.get(f.role)]}" and "${header[f.c]}"). Select one of them.`);
    used.set(f.role, f.c);
  }
  const cols = found.filter((f) => f.role);
  for (const f of found) {
    if (f.role) continue;
    if (!used.has("Values")) { f.role = "Values"; used.set("Values", f.c); cols.push(f); } else leftOut.push(String(header[f.c]));
  }
  cols.sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role));
  return { cols, leftOut };
}

// ---------- tiny OOXML writer ----------
const esc = (s) => String(s).replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");
const colLetter = (n) => { let s = ""; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; };
const XMLH = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';

function cellXml(ref, v, style) {
  if (typeof v === "number" && isFinite(v)) return `<c r="${ref}" s="${style ?? 3}"><v>${v}</v></c>`;
  if (v === "" || v === null || v === undefined) return "";
  return `<c r="${ref}" s="${style ?? 0}" t="inlineStr"><is><t xml:space="preserve">${esc(v)}</t></is></c>`;
}

// ---------- chart or table ----------
const MONTH = "(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)\\.?";
const TIME_PATTERNS = [
  new RegExp(`^${MONTH}$`, "i"),                        // Jan, January
  new RegExp(`^${MONTH}[ \\-'/]?\\d{2,4}$`, "i"),       // Jan 2026, Jan-26
  /^\d{4}$/,                                              // 2026
  /^\d{4}[ \-/]?(q[1-4]|h[12])$/i,                        // 2026-Q1, 2026 H1
  /^(q[1-4]|h[12])([ \-/]?\d{2,4})?$/i,                   // Q1, H1, Q1 2026
  /^\d{4}-\d{2}(-\d{2})?$/,                               // 2026-01, 2026-01-31
  /^\d{1,2}\/\d{1,2}\/\d{2,4}$/,                          // 1/31/2026
];
const TIME_HEADER = /^(month|quarter|year|date|period|week|day|time|fiscal|fy|semester|half)/i;

/** True when the category column reads as time periods, so "auto" draws a chart. */
function isTimeCategory(head, rows) {
  if (TIME_HEADER.test(String(head).trim())) return true;
  const labels = rows.map((r) => r[0]).filter((v) => v !== "" && v !== null && v !== undefined);
  return labels.length > 0 && labels.every((v) => TIME_PATTERNS.some((re) => re.test(String(v).trim())));
}

// ---------- Tables add-in data (as the Zebra BI workbook builder writes it for Excel) ----------
const TABLE_ORDER = ["Values", "PreviousYear", "Plan", "Forecast"]; // tablesOptions.ts getValueColumns order
const ROLE_KEY = { Values: "Val", PreviousYear: "PY", Plan: "PL", Forecast: "FC" };
const TEXT_TYPE = () => ({ underlyingType: 1, category: null, primitiveType: 1, extendedType: 1, categoryString: null,
  text: true, numeric: false, integer: false, bool: false, dateTime: false, duration: false, binary: false, json: false, none: false });
const NUM_TYPE = () => ({ underlyingType: 259, category: null, primitiveType: 3, extendedType: 259, categoryString: null,
  text: false, numeric: true, integer: false, bool: false, dateTime: false, duration: false, binary: false, json: false, none: false });

function xcolObject(name, i) {
  const ref = { _kind: 2, source: { _kind: 0, entity: name, kind: 0 }, ref: name, kind: 2 };
  return { roles: { Category: true }, type: TEXT_TYPE(), displayName: name, queryName: `${name}.${name}`,
    expr: { _kind: 2, source: { _kind: 0, entity: name, variable: "d", kind: 0 }, ref: name, kind: 2 },
    rolesIndex: { Category: [i] }, index: i, identityExprs: [ref] };
}

function valueObject(col, index, isMatrix) {
  const fmt = "#,0", name = col.displayName;
  return { roles: { [col.role]: true }, type: NUM_TYPE(), format: fmt, displayName: name, queryName: `${name}.${name}`,
    expr: { _kind: 3, source: { _kind: 0, entity: name, variable: "s", kind: 0 }, ref: name, kind: 3 },
    rolesIndex: { [col.role]: [isMatrix ? 0 : index] }, objects: { general: { formatString: fmt } }, index, isMeasure: true };
}

const tcell = (col, ix, value) => (col.name !== "Val" ? { value, valueSourceIndex: ix } : { value });
const cellValues = (vcols, pick) => Object.fromEntries(vcols.map((c, ix) => [String(ix), tcell(c, ix, pick(c))]));

function tableSettings(title, vcols, costs) {
  const s = { titleText: title, showTitle: true, showRowGrandTotal: true };
  if (costs) s.invert = true;
  const roles = vcols.map((c) => c.role);
  roles.forEach((r, i) => { s[`measure${i + 1}Role`] = r; });
  s.measureRoles = roles;
  s.usedMeasuresCount = roles.length;
  s.showCrossTables = false; // without it, Tables migrates ranges with more than four measures as legacy cross-tables
  return s;
}

function tableData(catName, labels, vcols, settings) {
  const names = [catName];
  const start = names.length;
  const sources = vcols.map((c, i) => valueObject(c, start + i, true));
  const children = labels.map((label, i) => ({ value: label, levelValues: [{ value: label, levelSourceIndex: 0 }], level: 0,
    values: cellValues(vcols, (c) => c.data[i].value) }));
  // Office PowerPoint treats a named "Total" node as a ragged category, so the total is unnamed.
  if (settings.showRowGrandTotal) children.push({ level: 0, isSubtotal: true, values: cellValues(vcols, (c) => c.total || 0) });
  const matrix = {
    rows: { levels: names.map((n, i) => ({ sources: [xcolObject(n, i)], canBeExpanded: i !== names.length - 1 })),
      root: { childIdentityFields: names.map((n) => ({ _kind: 2, source: { _kind: 0, entity: n, kind: 0 }, ref: n, kind: 2 })), children } },
    columns: { levels: sources.length ? [{ sources: JSON.parse(JSON.stringify(sources)) }] : [],
      root: { children: sources.map((_, i) => (i ? { level: 0, levelSourceIndex: i } : { level: 0 })) } },
    valueSources: sources,
  };
  const meta = { columns: [...names.map((n, i) => xcolObject(n, i)), ...vcols.map((c, k) => valueObject(c, names.length + k, false))] };
  const valueFields = meta.columns.filter((c) => !c.roles.Category).map((c) => c.displayName);
  // The add-in counts value columns from 0.
  for (const group of [matrix.columns.levels.length ? matrix.columns.levels[0].sources : [], matrix.valueSources, meta.columns]) {
    for (const c of group) if (!c.roles.Category) c.index -= names.length;
  }
  meta.measureRoles = settings.measureRoles;
  const rowFields = meta.columns.filter((c) => c.roles.Category).map((c) => c.displayName);
  return { matrix, rowFields, columnFields: [], valueFields, rowGrandTotal: false, columnGrandTotal: false,
    rowNames: [], columnNames: [], valueNames: valueFields, metadata: meta };
}

// Ids made at run time: the worksheet id the add-ins bind to is the sheet's position in the workbook, and each web
// extension part needs a unique id of its own.
const SHEET_POSITION = 0; // the one sheet of the new workbook
const worksheetId = (position) => "{" + ["00000000", "0001", "0000", String(position).padStart(2, "0") + "00", "000000000000"].join("-") + "}";
const newId = () => "{" + "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, () => Math.floor(Math.random() * 16).toString(16)).toUpperCase() + "}";

function buildParts(header, rows, opts) {
  const kind = opts.visual === "chart" || opts.visual === "table" ? opts.visual : (isTimeCategory(header[0], rows) ? "chart" : "table");
  const plan = planColumns(header, rows);
  let cols = plan.cols;
  if (kind === "table") cols = [...cols].sort((a, b) => TABLE_ORDER.indexOf(a.role) - TABLE_ORDER.indexOf(b.role));
  const sheetName = kind === "chart" ? "Zebra BI chart" : "Zebra BI table";
  const top = 3; // data header row
  const out = [[header[0], ...cols.map((x) => header[x.c])], ...rows.map((r) => [r[0], ...cols.map((x) => r[x.c])])];
  const ncols = out[0].length, last = top + out.length - 1;
  const title = opts.title || `${cols.map((x) => header[x.c]).join(" vs ")} by ${header[0]}`;

  let sd = `<row r="1">${cellXml("A1", title, 1)}</row>`;
  out.forEach((row, i) => {
    const r = top + i;
    sd += `<row r="${r}">` + row.map((v, c) => cellXml(colLetter(c + 1) + r, v, i === 0 ? 2 : undefined)).join("") + "</row>";
  });
  const widths = out[0].map((_, i) => `<col min="${i + 1}" max="${i + 1}" width="14" customWidth="1"/>`).join("");
  const sheet = `${XMLH}<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView workbookViewId="0" showGridLines="0" tabSelected="1"/></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>${widths}</cols><sheetData>${sd}</sheetData><drawing r:id="rIdDrawing"/></worksheet>`;

  const appref = worksheetId(SHEET_POSITION);
  const source = { range: `'${sheetName.replace(/'/g, "''")}'!A${top}:${colLetter(ncols)}${last}`, worksheetId: appref };
  const prop = (n, v) => `<we:property name="${n}" value="${esc(v)}"/>`;
  let we;
  if (kind === "chart") {
    const settings = { titleText: title, chartType: cols.length > 1 ? 3 : 2, usedMeasuresCount: cols.length };
    cols.forEach((x, i) => { settings[`measure${i + 1}Role`] = x.role; });
    if (cols.length <= 1) settings.differenceHighlight = false;
    if (opts.costs) settings.invert = true;
    const chooser = { isVisible: false, selectedDimension: "time", selectedChart: "time-comparison-column", areComments: true };
    we = `${XMLH}<we:webextension xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11" id="${newId()}"><we:reference id="wa200004688" version="1.0.4.0" store="en-US" storeType="OMEX"/><we:alternateReferences><we:reference id="WA200004688" version="1.0.4.0" store="" storeType="OMEX"/></we:alternateReferences><we:properties>${prop("ZBILicenseSettings", "")}${prop("ChartChooserStore", JSON.stringify(chooser))}${prop("ChartSettings", JSON.stringify(settings))}${prop("SourceData", JSON.stringify(source))}${prop("zebrabi:table-data", "")}</we:properties><we:bindings><we:binding id="chartsRangeChangeEvent" type="matrix" appref="${appref}"/></we:bindings><we:snapshot/></we:webextension>`;
  } else {
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
    const vcols = cols.map((x) => {
      const data = rows.map((r) => ({ value: num(r[x.c]) }));
      return { name: ROLE_KEY[x.role], displayName: String(header[x.c]), role: x.role, data,
        total: data.reduce((s, d) => s + (d.value || 0), 0) };
    });
    const settings = tableSettings(title, vcols, opts.costs);
    const data = tableData(String(header[0]), rows.map((r) => String(r[0])), vcols, settings);
    const chooser = { isVisible: false, selectedDimension: "comparison", selectedTable: "comparison-table-one", areComments: false };
    we = `${XMLH}<we:webextension xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11" id="${newId()}"><we:reference id="wa200004689" version="1.0.0.0" store="en-US" storeType="OMEX"/><we:alternateReferences><we:reference id="WA200004689" version="1.0.0.0" store="omex" storeType="OMEX"/></we:alternateReferences><we:properties>${prop("ZBILicenseSettings", "")}${prop("TableChooserStore", JSON.stringify(chooser))}${prop("ZBITablesSettings", JSON.stringify(settings))}${prop("SourceData", JSON.stringify(source))}${prop("zebrabi:matrix-data", JSON.stringify(JSON.stringify(data)))}</we:properties><we:bindings><we:binding id="tablesRangeChanged" type="matrix" appref="${appref}"/></we:bindings><we:snapshot/></we:webextension>`;
  }

  const c0 = ncols + 1, r0 = top - 1, c1 = c0 + 13;
  const r1 = kind === "chart" ? r0 + 20 : r0 + Math.max(12, out.length + 6); // a table frame grows with its rows plus the total
  const frameName = kind === "chart" ? "Zebra BI Charts" : "Zebra BI Tables";
  const drawing = `${XMLH}<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><xdr:twoCellAnchor editAs="oneCell"><xdr:from><xdr:col>${c0}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${r0}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>${c1}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${r1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to><mc:AlternateContent><mc:Choice xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11" Requires="we"><xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="${frameName}"/><xdr:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></xdr:cNvGraphicFramePr></xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/webextensions/webextension/2010/11"><we:webextensionref r:id="rIdWe"/></a:graphicData></a:graphic></xdr:graphicFrame></mc:Choice><mc:Fallback/></mc:AlternateContent><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>`;

  const R = (id, type, target) => `<Relationship Id="${id}" Type="${type}" Target="${target}"/>`;
  const rels = (inner) => `${XMLH}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${inner}</Relationships>`;
  const OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const styles = `${XMLH}<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="3"><font><sz val="10"/><color rgb="FF242424"/><name val="Segoe UI"/><family val="2"/></font><font><sz val="13"/><color rgb="FF242424"/><name val="Segoe UI Semibold"/><family val="2"/></font><font><b/><sz val="10"/><color rgb="FF242424"/><name val="Segoe UI"/><family val="2"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="thin"><color rgb="FF242424"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;

  return {
    meta: { kind, title, roles: cols.map((x) => `${header[x.c]}=${x.role}`), leftOut: plan.leftOut, range: source.range },
    parts: [
      ["[Content_Types].xml", `${XMLH}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/><Override PartName="/xl/webextensions/webextension1.xml" ContentType="application/vnd.ms-office.webextension+xml"/></Types>`],
      ["_rels/.rels", rels(R("rId1", OD + "/officeDocument", "xl/workbook.xml"))],
      ["xl/workbook.xml", `${XMLH}<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="${OD}"><bookViews><workbookView activeTab="0"/></bookViews><sheets><sheet name="${esc(sheetName)}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
      ["xl/_rels/workbook.xml.rels", rels(R("rId1", OD + "/worksheet", "worksheets/sheet1.xml") + R("rIdStyles", OD + "/styles", "styles.xml"))],
      ["xl/styles.xml", styles],
      ["xl/worksheets/sheet1.xml", sheet],
      ["xl/worksheets/_rels/sheet1.xml.rels", rels(R("rIdDrawing", OD + "/drawing", "../drawings/drawing1.xml"))],
      ["xl/drawings/drawing1.xml", drawing],
      ["xl/drawings/_rels/drawing1.xml.rels", rels(R("rIdWe", "http://schemas.microsoft.com/office/2011/relationships/webextension", "../webextensions/webextension1.xml"))],
      ["xl/webextensions/webextension1.xml", we],
    ],
  };
}

// ---------- zip (stored, no compression) ----------
const CRC_TABLE = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = (b) => { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC_TABLE[(c ^ b[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };

function zip(parts) {
  const enc = new TextEncoder(), chunks = [], central = [];
  let offset = 0;
  const u16 = (v) => [v & 0xff, (v >>> 8) & 0xff], u32 = (v) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
  for (const [name, text] of parts) {
    const nb = enc.encode(name), data = enc.encode(text), crc = crc32(data);
    const local = [...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0)];
    chunks.push(Uint8Array.from(local), nb, data);
    central.push(Uint8Array.from([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset)]), nb);
    offset += local.length + nb.length + data.length;
  }
  const cdSize = central.reduce((s, b) => s + b.length, 0);
  const end = Uint8Array.from([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(parts.length), ...u16(parts.length), ...u32(cdSize), ...u32(offset), ...u16(0)]);
  const all = [...chunks, ...central, end], out = new Uint8Array(all.reduce((s, b) => s + b.length, 0));
  let p = 0; for (const b of all) { out.set(b, p); p += b.length; }
  return out;
}

const toBase64 = (bytes) => { let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(s); };

// ---------- run in Excel ----------
await Excel.run(async (context) => {
  const range = context.workbook.getSelectedRange();
  range.load("values,text,address");
  await context.sync();
  // The category column and the header are taken as the text the cells show (a year, a date or a code stays what the
  // reader sees); the scenario columns are taken as numbers.
  const kept = range.values.map((_, i) => i).filter((i) => range.values[i].some((v) => v !== "" && v !== null));
  const values = kept.map((i) => range.values[i].map((v, c) => (c === 0 || i === kept[0] ? String(range.text[i][c]) : v)));
  if (values.length < 2 || values[0].length < 2) throw new Error("Select the data with its header row first (category column plus AC/PY/PL/FC columns).");
  const [header, ...rows] = values;
  const { meta, parts } = buildParts(header, rows, { title: TITLE, costs: COSTS, visual: VISUAL });
  await Excel.createWorkbook(toBase64(zip(parts)));
  await context.sync();
  const left = meta.leftOut.length ? `; left out: ${meta.leftOut.join(", ")}` : "";
  console.log(`Zebra BI ${meta.kind} workbook opened: "${meta.title}" from ${range.address}; scenarios ${meta.roles.join(", ")}${left}.`);
  return { opened: true, source: range.address, ...meta };
});
