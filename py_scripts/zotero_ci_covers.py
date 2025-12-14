"""
CI-friendly script to find and generate base64 covers for Zotero items.
Does not require local Ollama. Uses DuckDuckGo for search and standard PIL for processing.
"""

import os
import time
import io
import base64
import requests
from requests import exceptions as requests_exceptions
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
# Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
COLLECTION_KEY = os.getenv("COLLECTION_KEY", "").strip() or None

TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE", "").strip() or "book"
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"


COVER_NOTE_TITLE = "Book Cover (b64)"
MAX_SEARCH_RESULTS = 5
REQUEST_TIMEOUT = 10
SEARCH_DELAY = 2
MAX_B64_SIZE = 500000
MAX_IMAGE_WIDTH = 600
JPEG_QUALITY = 85

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

def download_and_encode(url):
    """Download image from URL and convert to base64"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        
        image_data = response.content
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        
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

def validate_image_url(url):
    if not url: return False
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.head(url, timeout=REQUEST_TIMEOUT, headers=headers, allow_redirects=True)
        if response.status_code >= 400: return False
        content_type = response.headers.get("Content-Type", "").lower()
        if "image" in content_type: return True
        return False
    except:
        return False

def find_cover_url(title, author):
    """Search DuckDuckGo for a cover image"""
    query = f"{title} book cover"
    if author:
        query += f" by {author}"
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=MAX_SEARCH_RESULTS))
            for result in results:
                candidate = result.get("image")
                if candidate and validate_image_url(candidate):
                    return candidate
    except Exception as e:
        print(f"    ⚠ Search error: {e}")
    return None

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
            return f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
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

def main():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        print("Error: ZOTERO_API_KEY and ZOTERO_USER_ID must be set.")
        return

    print(f"Connecting to Zotero (User: {ZOTERO_USER_ID})...")
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    
    # Fetch target items
    if COLLECTION_KEY:
        print(f"Fetching items from collection {COLLECTION_KEY}...")
        raw_items = zot.everything(zot.collection_items(COLLECTION_KEY))
    else:
        print("Fetching all library items...")
        raw_items = zot.everything(zot.items())
        
    books = [i for i in raw_items if i.get('data', {}).get('itemType') == TARGET_ITEM_TYPE]
    print(f"Found {len(books)} books.")

    processed = 0
    created = 0
    updated = 0
    errors = 0

    for book in tqdm(books, desc="Checking covers"):
        processed += 1
        item_key = book['key']
        title = book['data'].get('title', 'Untitled')
        
        # 1. Check if b64 note exists and if it needs regeneration
        existing_note, needs_regeneration = get_b64_note(zot, item_key)
        
        if existing_note and not needs_regeneration:
            continue  # Skip - note exists with valid b64 data
            
        action = "Regenerating" if needs_regeneration else "Processing"
        print(f"\n{action}: {title} ({item_key})")
        
        # 2. Find cover URL
        author = get_book_author(book['data'].get('creators', []))
        cover_url = find_cover_url(title, author)
        
        if not cover_url:
            print("  ⚠ No cover found via search.")
            errors += 1
            time.sleep(SEARCH_DELAY)
            continue
            
        print(f"  ✓ Found cover: {cover_url}")
        
        # 3. Download and convert
        b64_data = download_and_encode(cover_url)
        if not b64_data:
            print("  ✗ Failed to download/encode image.")
            errors += 1
            continue
            
        # 4. Create or update note
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
    print(f"Errors:    {errors}")
    print("="*50)

if __name__ == "__main__":
    main()
