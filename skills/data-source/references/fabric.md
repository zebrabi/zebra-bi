<!-- Summary: data-source adapter for Power BI semantic models through a Microsoft Power BI / Fabric MCP (DAX). Documented, not yet tested. -->
# Adapter: Power BI semantic model (DAX)

**Status: documented, not yet tested end to end.** Verify the steps the first time a Power BI or Fabric MCP is connected.

Zebra BI does not fetch customer data or hold Power BI tokens; the customer's own Claude does, through Microsoft's MCP (Fabric IQ, or the remote Power BI MCP). Row-level security applies to whoever is signed in.

1. Check a Power BI or Fabric MCP is connected. If not, say so and stop; there is no fallback that bypasses the user's Microsoft sign-in.
2. Find the model: use the MCP's own list or search tool for workspaces and semantic models. Record workspace and model names and IDs.
3. Read the model's tables, columns and measures with the MCP's schema tool before writing DAX. Never guess measure names.
4. Write one `EVALUATE SUMMARIZECOLUMNS(...)` query per grain the story needs (for example month x region with `[Sales]` and `[Sales PY]`). Aggregate in DAX; results are capped (about 100k rows on the remote Power BI MCP).
5. Save the result rows to `.zebra/sources/<name>.csv`, profile it with `profile.py`, and record the exact tool name and DAX in the manifest's `mcp.call`, plus `"semantic_model": {"workspace": "...", "model": "..."}`.
6. Prefer the model's own measures (`[Sales PY]`, `[Sales PL]`) over recomputing scenarios: they carry the business's definitions. Map them to `PY` / `PL` in `render_visual`.
