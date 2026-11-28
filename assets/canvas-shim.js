// MCP Apps host for an Excel Canvas page, filled by scripts/zoffice/canvas.py and placed before the Zebra BI renderer.
//
// The page draws one tool result: {visual} (render_visual) or {dashboard: {title, items: [{width, visual}]}} (render_dashboard).
// The binding reads the cells of one visual, the bound one: the visual itself, or the first item of a dashboard. The other
// visuals of a dashboard draw as built.
//
// The canvas page runs the renderer itself (a canvas page cannot hold an iframe), so the renderer's transport is
// pointed at window.__zbiHost, which is this page, and window.postMessage hands its JSON-RPC to this host. The host
// answers ui/initialize, then sends tool-input and tool-result after the renderer reports it is initialized and the
// bound cells have been read once, so an open sends one result.
//
// The double-underscore names in the var statement below are placeholders, each replaced by one JSON value: the
// binding name, the tool result the build drew, the cells the build wrote (a header row, then rows of text;
// a number is the list of strings the cell may read as: its plain value first, then the way a #,##0 cell shows it),
// where each scenario sits in the cells, whether the totals of the bound visual are the sum of its rows, the line shown
// when the data cannot be redrawn, and the plugin version (cells, columns and totals are those of the bound visual).
//
// The data rule: the page draws the result the build drew whenever the bound cells still hold what the build wrote,
// so the server's totals stand. When cells changed and the totals are sums, it keeps every cell that still matches the
// build, takes the changed cells as numbers and sums the totals. In every other case it keeps the drawing and shows
// the notice; it never sends a total it had to invent. Whether Excel hands the page raw values or the text a cell
// shows is not settled, so a built number matches in either form (any thousands grouping). A changed cell is used as
// a number only when the read is shown to be raw (a built value that differs from the way it shows matched in its
// plain form) and the cell is a plain number.
(function () {
  var KEY = __KEY__, BUILT = __RESULT__, CELLS = __CELLS__, COLUMNS = __COLUMNS__, ADDITIVE = __ADDITIVE__,
      NOTICE = __NOTICE__, VERSION = __VERSION__;
  var realPost = window.postMessage.bind(window);
  var current = BUILT, connected = false, ready = false, ticket = 0, noticeEl = null;
  window.__zbiHost = window;
  window.postMessage = function (msg, target, transfer) {
    if (msg && msg.jsonrpc === '2.0') { setTimeout(function () { onRendererMessage(msg); }, 0); return; }
    return realPost(msg, target, transfer);
  };
  function toRenderer(m) { window.dispatchEvent(new MessageEvent('message', { data: m, source: window, origin: location.origin })); }
  function onRendererMessage(m) {
    if (m.id != null && m.method) {
      if (m.method === 'ui/initialize') {
        var w = document.documentElement.clientWidth || 900;
        toRenderer({ jsonrpc: '2.0', id: m.id, result: { protocolVersion: (m.params && m.params.protocolVersion) || '2025-11-21',
          hostInfo: { name: 'zebra-bi-excel-canvas', version: VERSION }, hostCapabilities: {},
          hostContext: { toolInfo: { id: 1, tool: { name: BUILT.structuredContent.dashboard ? 'render_dashboard' : 'render_visual', inputSchema: { type: 'object' } } },
                         theme: 'light', displayMode: 'inline', maxWidth: w, viewport: { width: w } } } });
      } else toRenderer({ jsonrpc: '2.0', id: m.id, result: {} });
      return;
    }
    if (m.method === 'ui/notifications/initialized' || m.method === 'notifications/initialized') { connected = true; send(); }
  }
  function send() {
    if (!connected || !ready) return;
    toRenderer({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: {} });
    toRenderer({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: current });
  }
  // The first send waits for the first read of the cells, so an open sends one result; a read that never answers
  // does not hold the drawing back for more than a moment.
  function markReady() { if (!ready) { ready = true; send(); } }
  setTimeout(markReady, 1500);

  var PLAIN = /^-?\d+(\.\d+)?$/;
  var GROUPED = /^-?\d{1,3}([.,'\u00a0\u202f ]\d{3})+$/;   // 43,476 or 43.476 or 43 476: display text, not a number to trust
  function text(c) { return c == null ? '' : String(c).trim(); }
  function digits(s) { return s.replace(/[.,'\u00a0\u202f ]/g, ''); }
  // How a cell matches the number the build wrote: 'raw' (its plain value, which differs from the way it shows),
  // 'shown' (the way a #,##0 cell shows it, in any grouping), 'any' (the two read alike, as for a whole number under
  // 1,000), or null (it does not match). A string with the same digits counts as shown only when it is grouped.
  function match(cell, accepted) {
    var s = text(cell), shown = accepted.slice(1);
    var asRaw = PLAIN.test(s) && Number(s) === Number(accepted[0]);
    var asShown = shown.indexOf(s) >= 0 || (GROUPED.test(s) && shown.length > 0 && digits(s) === digits(shown[0]));
    if (asRaw && asShown) return 'any';
    return asRaw ? 'raw' : asShown ? 'shown' : null;
  }
  // The number a changed cell holds, or null. Grouped numbers are display text and are never read as numbers.
  function number(cell) {
    var s = text(cell);
    return PLAIN.test(s) && !GROUPED.test(s) ? Number(s) : null;
  }
  // Compares the cells with what the build wrote. Null when the shape differs (row count, width, header). Otherwise
  // the numeric cells that changed, whether a label changed, and which forms the matching numbers were read in.
  function inspect(rows) {
    if (rows.length !== CELLS.length) return null;
    var out = { changed: [], labels: false, raw: false, shown: false };
    for (var r = 0; r < CELLS.length; r++) {
      if (!Array.isArray(rows[r]) || rows[r].length < CELLS[r].length) return null;
      for (var c = 0; c < CELLS[r].length; c++) {
        var want = CELLS[r][c];
        if (typeof want === 'string') {
          if (text(rows[r][c]) !== want.trim()) { if (r === 0) return null; out.labels = true; }
          continue;
        }
        var kind = match(rows[r][c], want);
        if (kind === null) out.changed.push([r, c]);
        else if (kind === 'raw') out.raw = true;
        else if (kind === 'shown') out.shown = true;
      }
    }
    return out;
  }
  // The built drawing with the changed labels and numbers of the bound visual taken from the cells, and each of its totals
  // summed from the rows; the other visuals stay as built. Null when a changed cell cannot be read as a number.
  function bound(sc) { return sc.dashboard ? sc.dashboard.items[0].visual : sc.visual; }
  function patched(rows, info) {
    var sc = JSON.parse(JSON.stringify(BUILT.structuredContent)), visual = bound(sc), sums = {}, r, k;
    for (r = 1; r < CELLS.length; r++) {
      var name = text(rows[r][0]);
      if (name === '') return null;
      if (name !== CELLS[r][0].trim()) visual.xcol[r - 1].name = name;
    }
    for (var i = 0; i < info.changed.length; i++) {
      r = info.changed[i][0];
      var col = null, n = number(rows[r][info.changed[i][1]]);
      for (k = 0; k < COLUMNS.length; k++) if (COLUMNS[k].col === info.changed[i][1]) col = COLUMNS[k];
      if (col === null || n === null) return null;
      visual[col.field][r - 1].value = n;
    }
    for (k = 0; k < COLUMNS.length; k++) {
      var sum = 0;
      for (r = 0; r < visual[COLUMNS[k].field].length; r++) sum += visual[COLUMNS[k].field][r].value;
      sums[COLUMNS[k].total] = Number(sum.toFixed(9));
    }
    for (var key in sums) if (visual.grand_total && visual.grand_total[key] != null) visual.grand_total[key] = sums[key];
    return { content: BUILT.content, structuredContent: sc, isError: false };
  }
  function showNotice(on) {
    function apply() {
      if (!noticeEl && on) {
        noticeEl = document.createElement('div');
        noticeEl.id = 'zbi-notice';
        noticeEl.textContent = NOTICE;
        noticeEl.setAttribute('style', 'position:fixed;left:8px;bottom:6px;z-index:10;padding:2px 6px;font:12px/1.4 Segoe UI,Arial,sans-serif;color:#616161;background:#fffc');
        document.body.appendChild(noticeEl);
      }
      if (noticeEl) noticeEl.style.display = on ? 'block' : 'none';
    }
    if (document.body) apply(); else document.addEventListener('DOMContentLoaded', apply);
  }
  async function readCells() {
    if (typeof window.getDataBindingValue !== 'function') return null;
    try {
      var raw = await window.getDataBindingValue(KEY);
      var rows = typeof raw === 'string' ? JSON.parse(raw) : raw;
      return Array.isArray(rows) && rows.length ? rows : null;
    } catch (e) { return null; }
  }
  function use(result) { if (current !== result) { current = result; send(); } }
  async function refresh() {
    var mine = ++ticket, rows = await readCells();
    if (mine !== ticket) return;
    var info = rows === null ? { changed: [], labels: false, raw: false, shown: false } : inspect(rows);
    if (info === null) { use(BUILT); showNotice(true); markReady(); return; }
    if (!info.changed.length && !info.labels && !(info.raw && info.shown)) { use(BUILT); showNotice(false); markReady(); return; }
    // A changed number is trusted only when this read is shown to be raw values and does not mix in shown text.
    var proven = !(info.raw && info.shown) && (info.changed.length === 0 || info.raw);
    var next = ADDITIVE && proven ? patched(rows, info) : null;
    if (next === null) { use(BUILT); showNotice(true); markReady(); return; }
    use(next);
    showNotice(false);
    markReady();
  }
  window.addEventListener('dataBindingKeyChanged', function (e) { if (e.detail && e.detail.key === KEY) refresh(); });
  refresh();
})();
