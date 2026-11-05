<!-- Summary: how a Zebra data story shows on each Claude surface, how story-pdf draws the charts for the PDF, and the MCP Apps host contract its page implements. -->
# Surfaces

| Surface | What render_visual shows | How the story is delivered |
| --- | --- | --- |
| Claude Desktop chat, claude.ai | the interactive Zebra chart inline (MCP Apps) | the reply itself, charts inline |
| Claude Code, desktop Code tab | the interactive chart inline; after a reload it shows "could not connect to the chat" | the reply itself, charts inline; `story-pdf` builds a PDF on request |
| Claude Code, terminal | JSON only | the reply as text; charts show inline in Claude Desktop and claude.ai; `story-pdf` builds a PDF on request |

On Windows, Claude Desktop chat can show the text result without the chart. Mention it if charts do not appear.

## How the PDF gets its charts

A conversation is not a file you can send. `pdf.py` lays the story out as one A4 page (`assets/story-template.html`) that embeds the MCP's own renderer (the `ui://` page that `render_visual` names in its `_meta.ui.resourceUri`) once per chart, which keeps every chart identical to the one in the conversation. Headless Chrome, Edge or Chromium opens that page from disk at the A4 printable width (680 CSS px), plays the MCP Apps host for each chart, waits until all of them have drawn and held their size, and prints the PDF. Chart labels and values stay text in the PDF.

The page loads the renderer from a blob URL made from the embedded copy. The iframe sandbox keeps `allow-same-origin`, because an opaque-origin sandbox breaks the renderer's storage access and the chart never draws.

## The host contract the page implements

The renderer is an MCP Apps client talking JSON-RPC 2.0 over `postMessage`:

1. App sends `ui/initialize` (request). The host answers with `protocolVersion`, `hostInfo`, `hostCapabilities: {}` and `hostContext`. `hostContext.toolInfo.tool` must carry an `inputSchema` object, or the app reports "could not connect to the host". `maxWidth` / `viewport.width` set the layout width.
2. App sends `notifications/initialized`. The host pushes `ui/notifications/tool-input` and then `ui/notifications/tool-result` (the full tool result with `structuredContent.visual`).
3. App sends `ui/notifications/size-changed` as it lays out; the host sets the iframe height. The first size change after the tool result sets `data-status="ready"` on the iframe, and a reload of the chart removes it. A chart below the fold draws only once it is scrolled into view, so `pdf.py` scrolls to each chart in turn. A render is settled when no size change arrives for a second.
4. Every other request is answered with an empty result so the app never blocks.

Not offered by this host: fullscreen (needs `availableDisplayModes`), tool calls back to the MCP (`serverTools`), link opening and downloads.

## Renderer cache

`story.py` fetches the renderer with `resources/read` from `$ZEBRA_BI_MCP_URL` (default `https://mcp.zebrabi.com/mcp`, with the token `story.py sign-in` keeps), caches it in `$CLAUDE_PLUGIN_DATA` for a day and falls back to the cached copy when offline. A cached copy is used only when its sha256 matches the one recorded in `visual.json`, it was fetched from the same MCP address and it is not a symlink; otherwise it is fetched again. A fetched renderer must be an HTML page under 20 MB. The page records the renderer's sha256 in `<meta name="zebra-renderer" content="<sha256>">`.
