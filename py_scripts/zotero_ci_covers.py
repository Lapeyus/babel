"""
CI-friendly script to find and generate base64 covers for Zotero items.
Does not require local Ollama. Uses standard PIL for processing.

Cover search waterfall (most reliable sources first):
  1. Open Library covers by ISBN (exact match)
  2. Google Books API (by ISBN, then by title/author)
  3. Open Library search API (title/author)
  4. DuckDuckGo image search (last resort)
"""

import os
import re
import time
import io
import json
import base64
from urllib.parse import urljoin
import requests
from PIL import Image
from pyzotero import zotero
from dotenv import load_dotenv

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs): return iterable

load_dotenv()

# Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
COLLECTION_KEY = os.getenv("COLLECTION_KEY", "").strip() or None

TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE", "").strip() or "book"
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"

# Optional JSON mapping of item key (or "title:<lowercase title>") -> cover
# URL or list of candidate URLs. Entries prefixed with "page:" point at an
# HTML page whose og:image is used; the single entry "remove" deletes an
# existing cover note instead. A manual mapping always replaces whatever
# cover note the item currently has.
MANUAL_COVERS = os.getenv("MANUAL_COVERS", "").strip()


COVER_NOTE_TITLE = "Book Cover (b64)"
MAX_SEARCH_RESULTS = 5
REQUEST_TIMEOUT = 10
SEARCH_DELAY = 2
MAX_B64_SIZE = 500000
MAX_IMAGE_WIDTH = 600
JPEG_QUALITY = 85

# Reject images too small to be a usable cover, and (for untrusted
# search-engine results) clearly-landscape images that are unlikely covers.
MIN_COVER_WIDTH = 100
MIN_COVER_HEIGHT = 140

def compress_image(image_data, max_size=MAX_B64_SIZE, max_width=MAX_IMAGE_WIDTH):
    """Compress image to ensure base64 output is under the size limit"""
    try:
        img = Image.open(io.BytesIO(image_data))

        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # Resize if wider than max_width
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        # Try different quality levels
        quality = JPEG_QUALITY
        while quality >= 20:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            compressed_data = buffer.getvalue()
            b64_size = len(base64.b64encode(compressed_data))

            if b64_size <= max_size:
                return compressed_data, 'image/jpeg'

            quality -= 10

        # Aggressive resize
        for scale in [0.75, 0.5, 0.25]:
            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            resized.save(buffer, format='JPEG', quality=60, optimize=True)
            compressed_data = buffer.getvalue()
            b64_size = len(base64.b64encode(compressed_data))

            if b64_size <= max_size:
                return compressed_data, 'image/jpeg'

        return compressed_data, 'image/jpeg'

    except Exception as e:
        print(f"    ✗ Error compressing image: {e}")
        return None, None

def download_and_encode(url, trusted=True):
    """Download an image, validate it looks like a cover, return a data URI.

    trusted=False applies a stricter shape check for search-engine results.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        image_data = response.content

        try:
            img = Image.open(io.BytesIO(image_data))
            width, height = img.size
        except Exception:
            print("    ✗ Not a decodable image, skipping candidate.")
            return None

        if width < MIN_COVER_WIDTH or height < MIN_COVER_HEIGHT:
            print(f"    ✗ Image too small ({width}x{height}), skipping candidate.")
            return None

        if not trusted and height < width * 0.95:
            print(f"    ✗ Landscape image ({width}x{height}) unlikely to be a cover, skipping.")
            return None

        content_type = Image.MIME.get(img.format, 'image/jpeg')

        initial_b64_size = len(base64.b64encode(image_data))
        if initial_b64_size > MAX_B64_SIZE:
            image_data, content_type = compress_image(image_data)
            if image_data is None:
                return None

        b64_data = base64.b64encode(image_data).decode('utf-8')
        return f"data:{content_type};base64,{b64_data}"
    except Exception as e:
        print(f"    ✗ Error downloading/encoding: {e}")
        return None

def parse_manual_covers():
    if not MANUAL_COVERS:
        return {}
    try:
        data = json.loads(MANUAL_COVERS)
        return {
            (key.strip().lower() if key.startswith('title:') else key): (
                value if isinstance(value, list) else [value]
            )
            for key, value in data.items()
        }
    except Exception as e:
        print(f"⚠ Could not parse MANUAL_COVERS: {e}")
        return {}

def manual_urls_for(book, manual_covers):
    by_key = manual_covers.get(book['key'])
    if by_key:
        return by_key
    title = book['data'].get('title', '').strip().lower()
    return manual_covers.get(f"title:{title}", ())

# Generic site banners/logos that some pages (e.g. Goodreads for
# unauthenticated bots) serve as og:image — never usable as covers.
GENERIC_PAGE_IMAGE_RE = re.compile(
    r'goodreads_wide|/assets/facebook|logo|placeholder|default[_-]?(?:og|share|social)',
    re.I,
)

def extract_cover_from_page(page_url):
    """Fetch an HTML page and pull its og:image / twitter:image (or a
    plausible product/cover thumbnail) to use as a cover candidate."""
    try:
        response = requests.get(
            page_url, timeout=REQUEST_TIMEOUT, headers={'User-Agent': 'Mozilla/5.0'}
        )
        response.raise_for_status()
        html = response.text

        for prop in ('og:image', 'twitter:image'):
            m = re.search(
                r'<meta[^>]+(?:property|name)\s*=\s*["\']' + re.escape(prop) +
                r'["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
                html, re.I,
            ) or re.search(
                r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]*(?:property|name)\s*=\s*["\']' +
                re.escape(prop) + r'["\']',
                html, re.I,
            )
            if m:
                candidate = urljoin(page_url, m.group(1))
                if GENERIC_PAGE_IMAGE_RE.search(candidate):
                    print(f"    ✗ Page og:image looks like a site logo, skipping: {candidate[:80]}")
                else:
                    return candidate

        m = re.search(
            r'<img[^>]+src\s*=\s*["\']([^"\']*(?:pictures\.abebooks\.com|cover|portada)[^"\']*)["\']',
            html, re.I,
        )
        if m and not GENERIC_PAGE_IMAGE_RE.search(m.group(1)):
            return urljoin(page_url, m.group(1))
    except Exception as e:
        print(f"    ⚠ Page fetch error ({page_url[:70]}): {e}")
    return None

def isbn13_to_10(isbn13):
    if len(isbn13) != 13 or not isbn13.startswith('978') or not isbn13.isdigit():
        return None
    core = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ('X' if check == 10 else str(check))

def amazon_isbn10s(isbns):
    """ISBN-10s usable with Amazon's images/P/ cover endpoint"""
    result = []
    for isbn in isbns:
        candidate = isbn if len(isbn) == 10 else isbn13_to_10(isbn)
        if candidate and candidate not in result:
            result.append(candidate)
    return result

def normalize_isbns(raw_isbn):
    """Extract clean ISBN-10/13 strings from Zotero's free-text ISBN field"""
    isbns = []
    for token in re.split(r"[,;\s]+", raw_isbn or ""):
        cleaned = re.sub(r"[^0-9Xx]", "", token)
        if len(cleaned) in (10, 13):
            isbns.append(cleaned.upper())
    return list(dict.fromkeys(isbns))

def get_with_backoff(url, params=None, headers=None, tries=3, base_delay=5):
    """GET with exponential backoff on 429 (GitHub runner IPs are often
    rate-limited by public APIs). Returns the response or None."""
    for attempt in range(tries):
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            delay = base_delay * (2 ** attempt)
            print(f"    … rate limited (429), retrying in {delay}s")
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response
    print("    ⚠ Still rate limited after retries, giving up on this query.")
    return None

def google_books_candidates(title, author, isbns):
    """Yield cover URLs from the Google Books API (ISBN first, then title/author)"""
    queries = [f"isbn:{isbn}" for isbn in isbns]
    title_query = f'intitle:"{title}"'
    if author:
        title_query += f' inauthor:"{author}"'
    queries.append(title_query)

    for query in queries:
        try:
            response = get_with_backoff(
                "https://www.googleapis.com/books/v1/volumes",
                params={"q": query, "maxResults": MAX_SEARCH_RESULTS, "printType": "books"},
            )
            if response is None:
                continue
            for volume in response.json().get("items", []):
                image_links = volume.get("volumeInfo", {}).get("imageLinks", {})
                for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
                    if key in image_links:
                        url = image_links[key].replace("http://", "https://")
                        url = url.replace("&zoom=1", "").replace("zoom=1&", "")
                        yield url, "Google Books"
                        break
        except Exception as e:
            print(f"    ⚠ Google Books error ({query[:40]}): {e}")

def openlibrary_search_candidates(title, author):
    """Yield cover URLs from the Open Library search API"""
    params = {"title": title, "limit": MAX_SEARCH_RESULTS, "fields": "cover_i,title"}
    if author:
        params["author"] = author
    try:
        response = get_with_backoff(
            "https://openlibrary.org/search.json",
            params=params,
            headers={"User-Agent": "babel-cover-fetcher"},
        )
        if response is None:
            return
        for doc in response.json().get("docs", []):
            cover_id = doc.get("cover_i")
            if cover_id:
                yield f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg", "Open Library search"
    except Exception as e:
        print(f"    ⚠ Open Library search error: {e}")

def duckduckgo_candidates(title, author):
    """Yield cover URLs from DuckDuckGo image search (last resort).

    Tries an English and a Spanish query; DDG rate-limits aggressively from
    CI, so pause between queries and retry each one once after a cooldown.
    """
    queries = []
    base = f"{title} book cover"
    if author:
        base += f" by {author}"
    queries.append(base)
    queries.append(f'"{title}" libro portada')

    for query in queries:
        for attempt in range(2):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.images(query, max_results=MAX_SEARCH_RESULTS))
                for result in results:
                    candidate = result.get("image")
                    if candidate:
                        yield candidate, "DuckDuckGo"
                if results:
                    return
                break  # empty result set: try next query, don't retry this one
            except Exception as e:
                print(f"    ⚠ DuckDuckGo search error ({query[:40]}): {e}")
                if attempt == 0:
                    time.sleep(10)
        time.sleep(2)

def find_and_encode_cover(title, author, isbns, manual_urls=()):
    """Walk the source waterfall; return (data_uri, source) for the first
    candidate that downloads and validates as a plausible cover image."""
    def candidates():
        for entry in manual_urls:
            if entry.startswith('page:'):
                resolved = extract_cover_from_page(entry[len('page:'):])
                if resolved:
                    yield resolved, "Manual (page og:image)", True
            else:
                yield entry, "Manual", True
        for isbn in isbns:
            yield (
                f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false",
                "Open Library ISBN",
                True,
            )
        for isbn10 in amazon_isbn10s(isbns):
            # Amazon serves a 1x1 placeholder for unknown ISBNs, which the
            # minimum-size validation rejects automatically.
            yield (
                f"https://images-na.ssl-images-amazon.com/images/P/{isbn10}.01.LZZZZZZZ.jpg",
                "Amazon ISBN",
                True,
            )
        for url, source in google_books_candidates(title, author, isbns):
            yield url, source, True
        for url, source in openlibrary_search_candidates(title, author):
            yield url, source, True
        for url, source in duckduckgo_candidates(title, author):
            yield url, source, False

    for url, source, trusted in candidates():
        print(f"  → Trying {source}: {url[:100]}")
        b64_data = download_and_encode(url, trusted=trusted)
        if b64_data:
            return b64_data, source
    return None, None

def get_b64_note(zotero_api, item_key):
    """Check if 'Book Cover (b64)' note exists and if it has valid b64 data
    Returns: (note, needs_regeneration) tuple
    - note: the note object if found, None otherwise
    - needs_regeneration: True if note exists but was corrupted by Zotero 7
    """
    try:
        notes = zotero_api.children(item_key, itemType='note')
        for note in notes:
            content = note.get('data', {}).get('note', '')
            if COVER_NOTE_TITLE in content:
                # Check if it has actual base64 data or was converted by Zotero 7
                has_valid_b64 = 'data:image' in content and 'base64,' in content
                needs_regeneration = not has_valid_b64
                if needs_regeneration:
                    print(f"    ⚠ Note exists but was corrupted by Zotero 7 (no base64 data)")
                return note, needs_regeneration
        return None, False
    except Exception as e:
        print(f"    ✗ Error checking notes: {e}")
        return None, False


def create_b64_note(zotero_api, item_key, b64_data):
    """Create the b64 cover note"""
    note_html = f'<div><h3>{COVER_NOTE_TITLE}</h3><img src="{b64_data}" alt="Book Cover" style="max-width: 300px; height: auto;" /></div>'
    note_data = {
        "itemType": "note",
        "parentItem": item_key,
        "note": note_html
    }
    try:
        resp = zotero_api.create_items([note_data])
        return bool(resp.get('successful'))
    except Exception as e:
        print(f"    ✗ Error creating note: {e}")
        return False

def get_book_author(creators):
    for c in creators:
        if c.get('creatorType') == 'author':
            if c.get('name'):
                return c['name']
            name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            if name:
                return name
    return None

def update_b64_note(zotero_api, note, b64_data):
    """Update an existing b64 note with new image data"""
    try:
        note_html = f'<div><h3>{COVER_NOTE_TITLE}</h3><img src="{b64_data}" alt="Book Cover" style="max-width: 300px; height: auto;" /></div>'
        note['data']['note'] = note_html
        zotero_api.update_item(note)
        return True
    except Exception as e:
        print(f"    ✗ Error updating note: {e}")
        return False

def fetch_books(zot):
    """Fetch target items from one or more collections (comma-separated
    COLLECTION_KEY) or the whole library, deduplicated by key."""
    if COLLECTION_KEY:
        collection_keys = [k for k in re.split(r"[,\s]+", COLLECTION_KEY) if k]
        print(f"Fetching items from collections: {', '.join(collection_keys)}...")
        raw_items = []
        for key in collection_keys:
            raw_items.extend(zot.everything(zot.collection_items(key)))
    else:
        print("Fetching all library items...")
        raw_items = zot.everything(zot.items())

    books = {}
    for item in raw_items:
        if item.get('data', {}).get('itemType') == TARGET_ITEM_TYPE:
            books.setdefault(item['key'], item)
    return list(books.values())

def main():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        print("Error: ZOTERO_API_KEY and ZOTERO_USER_ID must be set.")
        return

    print(f"Connecting to Zotero (User: {ZOTERO_USER_ID})...")
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

    books = fetch_books(zot)
    print(f"Found {len(books)} books.")

    manual_covers = parse_manual_covers()
    if manual_covers:
        print(f"Manual cover overrides for {len(manual_covers)} item(s): {', '.join(manual_covers)}")

    processed = 0
    created = 0
    updated = 0
    removed = 0
    errors = 0
    source_stats = {}

    for book in tqdm(books, desc="Checking covers"):
        processed += 1
        item_key = book['key']
        title = book['data'].get('title', 'Untitled')
        manual_urls = manual_urls_for(book, manual_covers)

        # 1. Check if b64 note exists and if it needs regeneration
        existing_note, needs_regeneration = get_b64_note(zot, item_key)

        # Manual "remove": delete a wrong cover note and move on
        if manual_urls and 'remove' in manual_urls:
            if existing_note:
                print(f"\nRemoving cover note: {title} ({item_key})")
                try:
                    zot.delete_item(existing_note)
                    removed += 1
                except Exception as e:
                    print(f"    ✗ Error deleting note: {e}")
                    errors += 1
            continue

        # A manual mapping always replaces the current cover; otherwise skip
        # items whose note already has valid b64 data.
        if existing_note and not needs_regeneration and not manual_urls:
            continue

        action = "Regenerating" if (needs_regeneration or (existing_note and manual_urls)) else "Processing"
        print(f"\n{action}: {title} ({item_key})")

        # 2. Search the source waterfall and encode the first valid cover
        author = get_book_author(book['data'].get('creators', []))
        isbns = normalize_isbns(book['data'].get('ISBN', ''))
        b64_data, source = find_and_encode_cover(title, author, isbns, manual_urls)

        if not b64_data:
            print("  ⚠ No usable cover found in any source.")
            errors += 1
            time.sleep(SEARCH_DELAY)
            continue

        print(f"  ✓ Cover obtained via {source}")
        source_stats[source] = source_stats.get(source, 0) + 1

        # 3. Create or update note
        if existing_note:
            # Update existing corrupted note
            if update_b64_note(zot, existing_note, b64_data):
                print("  ✓ Regenerated base64 cover note.")
                updated += 1
            else:
                errors += 1
        else:
            # Create new note
            if create_b64_note(zot, item_key, b64_data):
                print("  ✓ Created base64 cover note.")
                created += 1
            else:
                errors += 1

        time.sleep(SEARCH_DELAY)

    print("\n" + "="*50)
    print(f"Processed: {processed}")
    print(f"Created:   {created}")
    print(f"Updated:   {updated}")
    print(f"Removed:   {removed}")
    print(f"Errors:    {errors}")
    if source_stats:
        print("Covers by source:")
        for source, count in sorted(source_stats.items(), key=lambda kv: -kv[1]):
            print(f"  {source}: {count}")
    print("="*50)

if __name__ == "__main__":
    main()
