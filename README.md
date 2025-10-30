# Zotero Frontend

Static frontend for browsing a Zotero library with an image-heavy grid similar to the Zotero web app.

## Setup

1. Duplicate your Zotero Web API credentials:
   - Find the numeric user or group ID (e.g. from https://www.zotero.org/settings/keys).
   - Create an API key with at least `read` access to the library and file attachments.
2. Edit `src/config.js` and fill in:
   - `LIBRARY_TYPE`: `"users"` (default) or `"groups"`.
   - `LIBRARY_ID`: your Zotero numeric ID.
   - `API_KEY`: required for private libraries and to stream attachment files (cover images).
   - `COLLECTION_KEY` (optional): limit the view to a single collection key (e.g. `H4STB4UH`).
3. The helper Python scripts ship with a read-only Zotero API key baked in. Update the
   `ZOTERO_API_KEY` constant in `zotero_images.py`, `zotero_abstracts.py`, or `zotero_tags.py`
   if you want to target a different library.
4. Serve the project with any static server so ES modules load correctly, for example:
   ```bash
   npx http-server .
   ```
   or open `index.html` with your favourite live-server extension.

The grid loads the first 100 top-level items, fetches their attachment children, and uses the first image attachment as a cover. If no attachment is available, the card shows a fallback initial.

## Automation scripts and CI

- `zotero_images.py`, `zotero_abstracts.py`, and `zotero_tags.py` default to a bundled
  read-only Zotero API key. Edit each file's `ZOTERO_API_KEY` constant to customise access.
- `.github/workflows/zotero-automation.yml` provides a `workflow_dispatch` job that installs
  dependencies and runs any of the scripts. Trigger the workflow from the Actions tab and choose
  which script you want to execute.
- `.github/workflows/pages.yml` deploys the static site to GitHub Pages whenever you push to
  `main` (or run it manually). Enable Pages in the repository settings, point it at
  "GitHub Actions", then push to publish the latest build.

## Current features

- Responsive cover grid inspired by the provided screenshot.
- Search-as-you-type across titles and creators.
- Collection dropdown populated from the Zotero API.
- Attachment lookups with concurrency control so cover images appear automatically.
- Inline toast messaging for API errors.
- Rich item drawer with info/notes/tags/attachments/related tabs, including live data pulled from Zotero on demand.

## Next steps

Consider adding pagination, lazy-loading to support larger libraries, and richer metadata flyouts (abstract, tags, notes). The toast component is intentionally minimal; wiring it to specific edge cases (invalid key, quota exceeded) would improve the UX. Finally, storing configuration outside the repo (e.g. `config.local.js`) avoids accidental key commits if the project becomes public.
