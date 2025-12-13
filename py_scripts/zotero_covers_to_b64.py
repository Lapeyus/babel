from pyzotero import zotero
import requests
import base64
import time
import io

from PIL import Image

import os
from dotenv import load_dotenv

load_dotenv()

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
COLLECTION_KEY = os.getenv("COLLECTION_KEY")
COVER_ATTACHMENT_TITLE = "Book Cover (Web)"
COVER_NOTE_TITLE = "Book Cover (b64)"
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE")

# Search Configuration
REQUEST_TIMEOUT = 10
DELAY_BETWEEN_ITEMS = 0.5  # Seconds between processing items
MAX_B64_SIZE = 500000  # Maximum base64 size (500KB) to stay under Zotero's note limit
MAX_IMAGE_WIDTH = 600  # Maximum width for cover images
JPEG_QUALITY = 85  # JPEG compression quality

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


def compress_image(image_data, max_size=MAX_B64_SIZE, max_width=MAX_IMAGE_WIDTH):
    """Compress image to ensure base64 output is under the size limit"""
    try:
        img = Image.open(io.BytesIO(image_data))
        
        # Convert to RGB if necessary (for PNG with transparency, etc.)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        # Resize if wider than max_width
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            print(f"    → Resized to {max_width}x{new_height}")
        
        # Try different quality levels until we're under the size limit
        quality = JPEG_QUALITY
        while quality >= 20:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            compressed_data = buffer.getvalue()
            b64_size = len(base64.b64encode(compressed_data))
            
            if b64_size <= max_size:
                print(f"    → Compressed to {b64_size} chars (quality={quality})")
                return compressed_data, 'image/jpeg'
            
            quality -= 10
        
        # If still too large, resize more aggressively
        for scale in [0.75, 0.5, 0.25]:
            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            buffer = io.BytesIO()
            resized.save(buffer, format='JPEG', quality=60, optimize=True)
            compressed_data = buffer.getvalue()
            b64_size = len(base64.b64encode(compressed_data))
            
            if b64_size <= max_size:
                print(f"    → Aggressively resized to {new_width}x{new_height}, {b64_size} chars")
                return compressed_data, 'image/jpeg'
        
        # Last resort: return smallest version
        print(f"    ⚠ Could not compress below limit, using smallest version")
        return compressed_data, 'image/jpeg'
        
    except Exception as e:
        print(f"    ✗ Error compressing image: {e}")
        return None, None


def download_image_as_b64(url):
    """Download an image from URL and return base64 encoded string"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        
        # Get the image data
        image_data = response.content
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        
        # Check if base64 would be too large
        initial_b64_size = len(base64.b64encode(image_data))
        
        if initial_b64_size > MAX_B64_SIZE:
            print(f"    → Image too large ({initial_b64_size} chars), compressing...")
            image_data, content_type = compress_image(image_data)
            if image_data is None:
                return None
        
        # Encode to base64
        b64_data = base64.b64encode(image_data).decode('utf-8')
        
        # Create data URI
        data_uri = f"data:{content_type};base64,{b64_data}"
        
        return data_uri
    except Exception as e:
        print(f"    ✗ Error downloading image: {e}")
        return None


def get_cover_attachment(zotero_api, item_key):
    """Get the 'Book Cover (Web)' attachment for an item, returns (attachment_obj, url)"""
    try:
        attachments = zotero_api.children(item_key, itemType='attachment')
        for attachment in attachments:
            data = attachment.get('data', {})
            title = data.get('title', '')
            if title == COVER_ATTACHMENT_TITLE:
                url = data.get('url')
                if url:
                    return attachment, url
        return None, None
    except Exception as e:
        print(f"    ✗ Error fetching attachments: {e}")
        return None, None


def delete_attachment(zotero_api, attachment):
    """Delete an attachment from Zotero"""
    try:
        attachment_key = attachment['key']
        zotero_api.delete_item(attachment)
        print(f"    🗑 Deleted original '{COVER_ATTACHMENT_TITLE}' attachment")
        return True
    except Exception as e:
        print(f"    ✗ Error deleting attachment: {e}")
        return False


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
        "deleted": 0,
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

        # Get the web cover attachment and URL
        cover_attachment, cover_url = get_cover_attachment(zot, item_key)
        
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
                # Delete the original web cover attachment after successful b64 note creation
                if cover_attachment and delete_attachment(zot, cover_attachment):
                    stats["deleted"] += 1
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
    print(f"Original covers deleted:   {stats['deleted']}")
    print(f"Errors:                    {stats['errors']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
