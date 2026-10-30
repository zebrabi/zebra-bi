# brand.json

A company brand at `.zebra/brand/brand.json` is merged over the default at `assets/brand/default/brand.json`, so it only lists what differs. File names are relative to the brand folder. `scripts/brand.py check` validates a brand; `show` prints the merged result.

```json
{
  "name": "Acme",
  "version": 1,
  "colors": { "ink": "#242424", "muted": "#616161", "hairline": "#E0E0E0", "paper": "#FFFFFF", "surface": "#FAFAFA", "accent": null },
  "fonts": {
    "headline": { "family": "Segoe UI Semibold", "fallback": "'Segoe UI', system-ui, -apple-system, sans-serif" },
    "body": { "family": "Segoe UI", "fallback": "system-ui, -apple-system, sans-serif" },
    "web_css": null
  },
  "kicker": null,
  "logo": { "file": "logo.png", "svg": "logo.svg", "placement": "cover" },
  "credit": "Charts by Zebra BI",
  "deck": { "template": "template.pptx", "layouts": { "title": "Title Slide", "content": "Title Only", "closing": "Title Only" } },
  "voice": { "language": "en-GB", "address": "you", "notes": ["short sentences"], "avoid_words": ["leverage"], "sample": null },
  "confirmed": { "colors.ink": "2026-10-09" }
}
```

| Field | Used by | Notes |
| --- | --- | --- |
| `colors.ink` | text everywhere | needs ≥ 4.5:1 contrast on `paper` (error below) |
| `colors.muted` | subtitles, labels, footer | a warning below 4.5:1 |
| `colors.hairline` | rules between sections, under the kicker | |
| `colors.paper`, `surface` | page background, light fills | |
| `colors.accent` | KPI rules and other marks; `null` draws them in ink | a warning when close in hue to the charts' variance green `#7ACA00`, red `#FF0000` or highlight blue `#0070C0` |
| `fonts.headline/body` | PDF, workbook; the default deck's theme | a company template's own theme fonts decide its deck; uncommon fonts get a substitution warning |
| `fonts.web_css` | PDF and artifact page | an `https://` stylesheet (e.g. Google Fonts), loaded with its font files each time a PDF is built and embedded in it, and by every reader of an artifact page (Google Fonts only); `brand.py check` warns and names the host, and refuses anything but `https://`; `null` keeps the build offline |
| `kicker` | the label above the key message (page, cover, Story sheet) when the story has no `kicker` of its own | default `null`: each story labels itself with its reporting unit and period |
| `logo.file` / `logo.svg` | `file` (PNG) on the cover and in Office; `svg` in the PDF | `brand.py logo` stores both; `placement` is `cover` or `none` |
| `credit` | footer of page, cover and Story sheet | default "Charts by Zebra BI"; removing it gives a warning |
| `deck.template` | the deck is built on it | its masters, layouts, theme and media are kept, its slides dropped; slide size follows it |
| `deck.layouts` | which layout per role | names as `brand.py inspect-template` prints them; unknown names are an error |
| `voice` | `data-story` headings and findings | never overrides the IBCS writing rules |

## What a brand cannot change

The charts. `render_visual` accepts no style arguments and the Zebra BI renderer always draws in its IBCS style, both in the conversation and in the pictures in Office files. The live Office visuals keep the Zebra BI add-in defaults too, so a live visual and its picture look the same. This keeps a variance green and a scenario fill meaning the same thing in every story.

## How the deck uses a template

- **Placeholders.** The cover's key message and subtitle go into the title layout's title and subtitle placeholders. Each section heading goes into the content layout's title placeholder. The template's own typography and positions therefore apply, and outline view works.
- **Content area.** The finding and the visual sit in the layout's body placeholder area if it has one, otherwise under the title.
- **Logo.** It is added to the cover only when the template's master carries no picture of its own.
- **Kicker and hairlines.** The kicker is placed above the title. The editorial hairlines belong to the default design and are left out on a company template.
