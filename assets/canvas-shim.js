// MCP Apps host for an Excel Canvas page, filled by scripts/zoffice/canvas.py and placed before the Zebra BI renderer.
//
// The page draws one tool result: {visual} (render_visual) or {dashboard: {title, items: [{width, visual}]}} (render_dashboard).
// The binding reads the cells of one visual, the bound one: the visual itself, or the first item of a dashboard.
//
// The canvas page runs the renderer itself (a canvas page cannot hold an iframe), so the renderer's transport is
// pointed at window.__zbiHost, which is this page, and window.postMessage hands its JSON-RPC to this host. The host
// answers ui/initialize, then sends tool-input and tool-result after the renderer reports it is initialized and the
// bound cells have been read once, so an open sends one result.
//
// The double-underscore names in the var statement below are placeholders, each replaced by one JSON value: the
// binding name, the tool result the build drew, the cells the build wrote for the bound visual (a header row, then rows of
// text; a number is the list of strings the cell may read as: its plain value first, then the way a #,##0 cell shows it),
// the line shown when the cells differ from the build (it names the data sheet), and the plugin version.
//
// The data rule: the page always draws the result the build embedded. No content of a worksheet cell becomes chart or
// table data, a label or a total. The cells are read only to compare them with what the build wrote; when they differ,
// the page keeps the drawing and shows the notice. To draw new data, build again or render the visual again through the
// Zebra BI MCP server.
//
// Whether Excel hands the page raw values or the text a cell shows is not settled, so a built number matches in either
// form (any thousands grouping), and a regional number format does not raise the notice. A read that mixes the two forms
// is not what the build wrote.
(function () {
  var KEY = __KEY__, BUILT = __RESULT__, CELLS = __CELLS__, NOTICE = __NOTICE__, VERSION = __VERSION__;
  var realPost = window.postMessage.bind(window);
  var connected = false, ready = false, ticket = 0, noticeEl = null;
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
    toRenderer({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: BUILT });
  }
  // The first send waits for the first read of the cells, so an open sends one result; a read that never answers
  // does not hold the drawing back for more than a moment.
  function markReady() { if (!ready) { ready = true; send(); } }
  setTimeout(markReady, 1500);

  var PLAIN = /^-?\d+(\.\d+)?$/;
  var GROUPED = /^-?\d{1,3}([.,'\u00a0\u202f ]\d{3})+$/;   // 43,476 or 43.476 or 43 476: the way a number shows
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
  // True when the cells are not what the build wrote: a read that gave no rows, a different row count, a cell range narrower
  // than the build's, a changed header, label or number, or a read that mixes raw values with shown text.
  function differs(rows) {
    if (!Array.isArray(rows) || rows.length !== CELLS.length) return true;
    var raw = false, shown = false;
    for (var r = 0; r < CELLS.length; r++) {
      if (!Array.isArray(rows[r]) || rows[r].length < CELLS[r].length) return true;
      for (var c = 0; c < CELLS[r].length; c++) {
        var want = CELLS[r][c];
        if (typeof want === 'string') {
          if (text(rows[r][c]) !== want.trim()) return true;
          continue;
        }
        var kind = match(rows[r][c], want);
        if (kind === null) return true;
        if (kind === 'raw') raw = true;
        else if (kind === 'shown') shown = true;
      }
    }
    return raw && shown;
  }
  function showNotice(on) {
    function apply() {
      if (!noticeEl && on) {
        noticeEl = document.createElement('div');
        noticeEl.id = 'zbi-notice';
        noticeEl.textContent = NOTICE;
        // First in the page and in the normal flow: the top of the page is what Excel shows on open (a page taller than the
        // sheet scrolls), and a line in the flow pushes the visuals down instead of covering one of them.
        noticeEl.setAttribute('style', 'padding:4px 8px;font:12px/1.4 Segoe UI,Arial,sans-serif;color:#424242;background:#f5f5f5;border-bottom:1px solid #e0e0e0');
        document.body.insertBefore(noticeEl, document.body.firstChild);
      }
      if (noticeEl) noticeEl.style.display = on ? 'block' : 'none';
    }
    if (document.body) apply(); else document.addEventListener('DOMContentLoaded', apply);
  }
  // null: the page has no getDataBindingValue (it is not in Excel), so there is nothing to compare. Otherwise {rows}, whatever
  // the read gave. A read that throws, does not parse, is empty or is not a list of rows (what Excel gives for a deleted data
  // sheet is not known) counts as changed, so a notice is never cleared by a binding that cannot be read.
  async function readCells() {
    if (typeof window.getDataBindingValue !== 'function') return null;
    try {
      var raw = await window.getDataBindingValue(KEY);
      return { rows: typeof raw === 'string' ? JSON.parse(raw) : raw };
    } catch (e) { return { rows: null }; }
  }
  async function refresh() {
    var mine = ++ticket, read = await readCells();
    if (mine !== ticket) return;
    showNotice(read !== null && differs(read.rows));
    markReady();
  }
  window.addEventListener('dataBindingKeyChanged', function (e) { if (e.detail && e.detail.key === KEY) refresh(); });
  refresh();
})();
