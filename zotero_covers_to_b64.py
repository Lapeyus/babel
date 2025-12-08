from pyzotero import zotero
import requests
import base64
import time

ZOTERO_USER_ID = "1595072"
ZOTERO_API_KEY = "mB0Blp4yjVIuX17QBYLsswIM"
LIBRARY_TYPE = "user"
COLLECTION_KEY = "F753DWXD"
COVER_ATTACHMENT_TITLE = "Book Cover (Web)"
COVER_NOTE_TITLE = "Book Cover (b64)"
TARGET_ITEM_TYPE = "book"
REQUEST_TIMEOUT = 10
DELAY_BETWEEN_ITEMS = 0.5  # Seconds between processing items

try:
    from tqdm import tqdm
except ImportError:
    class _TqdmFallback:
        def __init__(self, iterable, **_kwargs):
            self._iterable = iterable
        def __iter__(self):
            for item in self._iterable:
                yield item
        def set_postfix_str(self, *_args, **_kwargs):
            return None
    def tqdm(iterable, **kwargs):
        return _TqdmFallback(iterable, **kwargs)


def download_image_as_b64(url):
    """Download an image from URL and return base64 encoded string"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        
        # Get the image data
        image_data = response.content
        
        # Encode to base64
        b64_data = base64.b64encode(image_data).decode('utf-8')
        
        # Determine content type
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        
        # Create data URI
        data_uri = f"data:{content_type};base64,{b64_data}"
        
        return data_uri
    except Exception as e:
        print(f"    ✗ Error downloading image: {e}")
        return None


def get_cover_attachment(zotero_api, item_key):
    """Get the 'Book Cover (Web)' attachment for an item"""
    try:
        attachments = zotero_api.children(item_key, itemType='attachment')
        for attachment in attachments:
            data = attachment.get('data', {})
            title = data.get('title', '')
            if title == COVER_ATTACHMENT_TITLE:
                url = data.get('url')
                if url:
                    return url
        return None
    except Exception as e:
        print(f"    ✗ Error fetching attachments: {e}")
        return None


def get_b64_note(zotero_api, item_key):
    """Check if item already has a 'Book Cover (b64)' note"""
    try:
        notes = zotero_api.children(item_key, itemType='note')
        for note in notes:
            data = note.get('data', {})
            # Check if note has our title in the content
            content = data.get('note', '')
            if COVER_NOTE_TITLE in content:
                return note
        return None
    except Exception as e:
        print(f"    ✗ Error fetching notes: {e}")
        return None


def create_b64_note(zotero_api, item_key, b64_data):
    """Create a note with base64 encoded cover image"""
    try:
        # Create HTML content for the note
        note_html = f'''<div>
<h3>{COVER_NOTE_TITLE}</h3>
<img src="{b64_data}" alt="Book Cover" style="max-width: 300px; height: auto;" />
</div>'''
        
        note_data = {
            "itemType": "note",
            "parentItem": item_key,
            "note": note_html
        }
        
        response = zotero_api.create_items([note_data])
        if response.get('successful'):
            print(f"    ✓ Created b64 note")
            return True
        else:
            print(f"    ✗ Failed to create note: {response.get('failed')}")
            return False
    except Exception as e:
        print(f"    ✗ Error creating note: {e}")
        return False


def update_b64_note(zotero_api, note, b64_data):
    """Update an existing b64 note with new image data"""
    try:
        note_html = f'''<div>
<h3>{COVER_NOTE_TITLE}</h3>
<img src="{b64_data}" alt="Book Cover" style="max-width: 300px; height: auto;" />
</div>'''
        
        note['data']['note'] = note_html
        response = zotero_api.update_item(note)
        print(f"    ✓ Updated existing b64 note")
        return True
    except Exception as e:
        print(f"    ✗ Error updating note: {e}")
        return False


def fetch_target_items(zotero_api, collection_key=None):
    """Fetch all books from Zotero library or collection"""
    if collection_key:
        raw_items = zotero_api.everything(zotero_api.collection_items(collection_key))
        source_label = f"collection {collection_key}"
    else:
        raw_items = zotero_api.everything(zotero_api.items())
        source_label = "library"
    
    target_items = [
        item
        for item in raw_items
        if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    
    print(f"Found {len(target_items)} {TARGET_ITEM_TYPE}s in {source_label}.\n")
    return target_items


def main():
    print("=" * 70)
    print("Zotero Cover Images to Base64 Notes Converter")
    print("=" * 70 + "\n")
    
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    books = fetch_target_items(zot, COLLECTION_KEY)
    
    progress = tqdm(
        books,
        desc="Processing books",
        unit="book",
        total=len(books),
    )
    
    stats = {
        "already_has_b64": 0,
        "no_web_cover": 0,
        "download_failed": 0,
        "created": 0,
        "updated": 0,
        "errors": 0
    }
    
    for book in progress:
        title = book['data'].get('title', 'Unknown Title')
        item_key = book['key']
        
        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:50])
        
        print(f"\n📖 '{title}' (Key: {item_key})")
        
        # Check if already has b64 note
        existing_note = get_b64_note(zot, item_key)
        
        if existing_note:
            print(f"    ✓ Already has b64 note, skipping.")
            stats["already_has_b64"] += 1
            continue

        # Get the web cover URL
        cover_url = get_cover_attachment(zot, item_key)
        
        if not cover_url:
            print(f"    ⚠ No '{COVER_ATTACHMENT_TITLE}' attachment found")
            stats["no_web_cover"] += 1
            continue
        
        print(f"    → Cover URL: {cover_url[:60]}...")
        
        # Download and encode the image
        b64_data = download_image_as_b64(cover_url)
        
        if not b64_data:
            stats["download_failed"] += 1
            continue
        
        print(f"    ✓ Downloaded and encoded ({len(b64_data)} chars)")
        
        # Create or update the note
        if existing_note:
            if update_b64_note(zot, existing_note, b64_data):
                stats["updated"] += 1
            else:
                stats["errors"] += 1
        else:
            if create_b64_note(zot, item_key, b64_data):
                stats["created"] += 1
            else:
                stats["errors"] += 1
        
        # Delay to be nice to servers
        time.sleep(DELAY_BETWEEN_ITEMS)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total books processed:     {len(books)}")
    print(f"Already has b64 note:      {stats['already_has_b64']}")
    print(f"No web cover found:        {stats['no_web_cover']}")
    print(f"Download failed:           {stats['download_failed']}")
    print(f"B64 notes created:         {stats['created']}")
    print(f"B64 notes updated:         {stats['updated']}")
    print(f"Errors:                    {stats['errors']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
