<!-- Summary: data-source adapter for Excel and CSV files in SharePoint or OneDrive, read through the Microsoft 365 connector. -->
# Adapter: SharePoint / OneDrive

Uses the Microsoft 365 connector (`sharepoint_search`, `read_resource`). It reads; it never writes back.

1. Find the file: `sharepoint_search` with `query` (name words or column names) and `fileType: "xlsx"` (or `csv`). The result gives `uri` (`file:///<driveId>/<itemId>`), `name`, `webUrl`, `lastModifiedDateTime`. If several match, show name, folder and date and let the user pick.
2. Read it: `read_resource` with that `uri`.
   - `.xlsx` comes back as the whole workbook in one response: a `## Sheet: <name> — R rows × C columns (A1:D13)` header per sheet followed by tab-separated rows. Paging does not apply to workbooks.
   - `.csv` comes back paged; follow the `startPage` hint in the footer until the last page.
   - One response holds roughly 200k characters. A bigger sheet will not fit: ask the user to sync the file locally (OneDrive) and use the file adapter.
3. Save the chosen sheet's rows, header first, to `.zebra/sources/<name>.tsv` exactly as returned. Do not retype or reorder values.
4. Profile the extract: `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/profile.py .zebra/sources/<name>.tsv --name <name>`.
5. Add the `sharepoint` block (`uri`, `name`, `web_url`, `sheet`, `last_modified`) to the manifest. The URI is enough to read it again; no download link is stored.

Notes:
- The connector returns displayed values; formulas are listed separately and are not needed for a story.
- Workbook content is company data. Keep extracts out of shared repositories unless the user says otherwise.
