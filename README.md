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

## Python Scripts

A collection of Python scripts in `py_scripts/` to automate Zotero library management, enrichment, and cleanup.

### Setup

1. **Install dependencies:**
   ```bash
   cd py_scripts
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Copy `.env.example` to `.env` in the root `babel/` directory (or create it) and fill in your details:
   ```ini
   ZOTERO_USER_ID="your_user_id"
   ZOTERO_API_KEY="your_api_key_read_write"
   LIBRARY_TYPE="user"      # or "group"
   COLLECTION_KEY=""        # Target collection key (optional)
   TARGET_ITEM_TYPE="book"
   
   # Ollama Configuration
   OLLAMA_URL="http://localhost:11434"
   OLLAMA_MODEL="minimax-m2:cloud"
   OLLAMA_TIMEOUT=60
   OLLAMA_TEMPERATURE=0.3
   ```

### Scripts Description

| Script | Description |
|--------|-------------|
| `zotero_abstracts.py` | Generates book abstracts in Spanish using Ollama LLM and search context, saving them to the `abstractNote` field. |
| `zotero_aquileo.py` | Automatically finds winners of the "Premio Aquileo J. Echeverría" (Cuento) and adds their books to the target collection. |
| `zotero_nobel_winners.py` | Automatically finds Nobel Prize in Literature winners and adds a representative book for each to the target collection. |
| `zotero_enrich.py` | Analyzes books to generate tags, genres, and keywords using an LLM. Also creates `dc:relation` links between similar items. |
| `zotero_tags.py` | Generates 3-6 thematic tags for books using an LLM and adds them to Zotero. |
| `zotero_metadata_fixer.py` | Uses search results and LLM to correct metadata errors (split author names, fix dates/publishers) and fill missing fields. |
| `zotero_original_dates.py` | Finds the *original* publication date for classic works and adds it to the `Extra` field as `original-date: YYYY`. |
| `zotero_images_duckduckgo.py` | Searches DuckDuckGo Images for book covers and attaches them as linked URLs (`Book Cover (Web)`). |
| `zotero_images_google_books.py` | Searches Google Books API for high-res covers and attaches them as linked URLs. |
| `zotero_covers_to_b64.py` | Downloads linked cover images, resizes/compresses them, stores them as a Base64-encoded `note`, and deletes the original link. |

### Usage

Run any script from the `py_scripts` directory:
```bash
python zotero_abstracts.py
```

## Current features (Frontend)

- Responsive cover grid inspired by the provided screenshot.
- Search-as-you-type across titles and creators.
- Collection dropdown populated from the Zotero API.
- Attachment lookups with concurrency control so cover images appear automatically.
- Inline toast messaging for API errors.
- Rich item drawer with info/notes/tags/attachments/related tabs, including live data pulled from Zotero on demand.

## Next steps

Consider adding pagination, lazy-loading to support larger libraries, and richer metadata flyouts (abstract, tags, notes). The toast component is intentionally minimal; wiring it to specific edge cases (invalid key, quota exceeded) would improve the UX. Finally, storing configuration outside the repo (e.g. `config.local.js`) avoids accidental key commits if the project becomes public.
