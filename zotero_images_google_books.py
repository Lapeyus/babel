from pyzotero import zotero
import requests
import time

ZOTERO_USER_ID = "1595072"
ZOTERO_API_KEY = "mB0Blp4yjVIuX17QBYLsswIM"
LIBRARY_TYPE = "user"
COLLECTION_KEY = "4745YEWB"
COVER_ATTACHMENT_TITLE = "Book Cover (Web)"
TARGET_ITEM_TYPE = "book"
REQUEST_TIMEOUT = 8
SEARCH_DELAY = 1  # Seconds between API calls

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


def find_book_cover_google_books(title, author=None):
    """Search for book cover using Google Books API"""
    query = title
    if author:
        query += f" {author}"
    
    url = "https://www.googleapis.com/books/v1/volumes"
    params = {
        "q": query,
        "maxResults": 5,
        "printType": "books"
    }
    
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        if "items" in data:
            for item in data["items"]:
                volume_info = item.get("volumeInfo", {})
                image_links = volume_info.get("imageLinks", {})
                
                # Try to get the highest quality image available
                for key in ["extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"]:
                    if key in image_links:
                        cover_url = image_links[key]
                        # Convert to HTTPS and get higher resolution
                        cover_url = cover_url.replace("http://", "https://")
                        # Remove zoom parameter to get full size
                        cover_url = cover_url.replace("&zoom=1", "").replace("zoom=1&", "")
                        print(f"  Found via Google Books: {cover_url}")
                        return cover_url
        
        return None
    except Exception as e:
        print(f"  Google Books API error: {e}")
        return None


def add_linked_url_attachment(zotero_api, item_key, url, title=COVER_ATTACHMENT_TITLE):
    """Add a linked URL attachment to a Zotero item"""
    data = {
        "itemType": "attachment",
        "linkMode": "linked_url",
        "parentItem": item_key,
        "title": title,
        "url": url
    }
    try:
        response = zotero_api.create_items([data])
        if response.get('successful'):
            print(f"  ✓ Attachment added to item {item_key}")
            return True
        else:
            print(f"  ✗ Failed to add attachment: {response.get('failed')}")
            return False
    except Exception as e:
        print(f"  ✗ Error adding attachment: {e}")
        return False


def iter_cover_attachments(zotero_api, item_key):
    """Iterate over cover attachments for an item"""
    try:
        attachments = zotero_api.children(item_key, itemType='attachment')
        for attachment in attachments:
            data = attachment.get('data', {})
            title = data.get('title', '')
            if 'cover' in title.lower():
                yield attachment
    except Exception as e:
        print(f"  Error fetching attachments: {e}")


def has_valid_cover(zotero_api, item):
    """Check if item already has a valid cover attachment"""
    item_key = item['key']
    for attachment in iter_cover_attachments(zotero_api, item_key):
        data = attachment.get('data', {})
        url = data.get('url')
        if url:
            return True
    return False


def get_book_author(creators):
    """Extract the first author from creators list"""
    for creator in creators:
        if creator.get('creatorType') == 'author':
            if creator.get('name'):
                return creator['name']
            parts = [creator.get('firstName'), creator.get('lastName')]
            name = " ".join(part for part in parts if part)
            if name:
                return name
    return None


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
    print("Zotero Book Cover Fetcher - Google Books API")
    print("=" * 70 + "\n")
    
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    books = fetch_target_items(zot, COLLECTION_KEY)
    
    progress = tqdm(
        books,
        desc="Processing books",
        unit="book",
        total=len(books),
    )
    
    stats = {"skipped": 0, "found": 0, "not_found": 0, "added": 0}
    
    for book in progress:
        title = book['data'].get('title', 'Unknown Title')
        author = get_book_author(book['data'].get('creators', []))
        item_key = book['key']
        
        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:50])
        
        print(f"\n📖 '{title}' (Key: {item_key})")
        if author:
            print(f"   by {author}")
        
        # Check if already has cover
        if has_valid_cover(zot, book):
            print(f"  ✓ Already has cover, skipping")
            stats["skipped"] += 1
            continue
        
        # Search for cover
        cover_url = find_book_cover_google_books(title, author)
        
        if cover_url:
            stats["found"] += 1
            if add_linked_url_attachment(zot, item_key, cover_url):
                stats["added"] += 1
        else:
            print(f"  ✗ No cover found")
            stats["not_found"] += 1
        
        # Delay to respect API limits
        time.sleep(SEARCH_DELAY)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total books processed: {len(books)}")
    print(f"Already had covers:    {stats['skipped']}")
    print(f"Covers found:          {stats['found']}")
    print(f"Covers added:          {stats['added']}")
    print(f"Not found:             {stats['not_found']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
