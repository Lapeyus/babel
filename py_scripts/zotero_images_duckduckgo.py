from pyzotero import zotero
import requests
from requests import exceptions as requests_exceptions
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
import time

# Custom image type detection (replaces deprecated imghdr)
def detect_image_type(data):
    """Detect image type from bytes by checking magic numbers"""
    if not data:
        return None
    # Check common image formats by magic bytes
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return 'gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
    elif data.startswith(b'BM'):
        return 'bmp'
    return None
try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback cuando tqdm no está disponible
    class _TqdmFallback:
        def __init__(self, iterable, **_kwargs):
            self._iterable = iterable

        def __iter__(self):
            for item in self._iterable:
                yield item

        def set_postfix_str(self, *_args, **_kwargs):
            return None

    def tqdm(iterable, **kwargs):  # type: ignore
        return _TqdmFallback(iterable, **kwargs)

import os
from dotenv import load_dotenv

load_dotenv()

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
COLLECTION_KEY = os.getenv("COLLECTION_KEY")
SEARCH_ENGINE = "duckduckgo"  # "duckduckgo" or "google"
MAX_SEARCH_RESULTS = 5
COVER_ATTACHMENT_TITLE = "Book Cover (Web)"
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE")
REQUEST_TIMEOUT = 8
SEARCH_DELAY = 2  # Seconds to wait between searches to avoid rate limiting


def sanitize_filename(filename):
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename.strip())


def validate_image_url(url):
    if not url:
        return False
    headers = {"User-Agent": "Mozilla/5.0"}
    response = None
    try:
        response = requests.head(
            url, allow_redirects=True, timeout=REQUEST_TIMEOUT, headers=headers
        )
        need_get = False
        if response.status_code == 405 or response.status_code == 403:
            need_get = True
        elif response.status_code >= 400:
            return False
        else:
            content_type = response.headers.get("Content-Type", "").lower()
            if "image" in content_type:
                return True
            need_get = True

        if need_get:
            if response is not None:
                response.close()
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True,
                headers=headers,
            )
            if response.status_code >= 400:
                return False
            content_type = response.headers.get("Content-Type", "").lower()
            if "image" in content_type:
                return True
            try:
                chunk = next(response.iter_content(1024))
            except StopIteration:
                chunk = b""
            if chunk and detect_image_type(chunk):
                return True
            return False
        return False
    except requests_exceptions.RequestException:
        return False
    finally:
        if response is not None:
            response.close()


def find_book_cover(title, author=None, search_engine=SEARCH_ENGINE, max_results=MAX_SEARCH_RESULTS):
    query = f"{title} book cover"
    if author:
        query += f" by {author}"

    try:
        if search_engine == "duckduckgo":
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=max_results))
                for result in results:
                    candidate = result.get("image")
                    if candidate and validate_image_url(candidate):
                        return candidate
        elif search_engine == "google":
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(f"https://www.google.com/search?q={query}&tbm=isch", headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            for img in soup.find_all('img'):
                img_url = img.get('src')
                if img_url and not img_url.startswith('data:') and validate_image_url(img_url):
                    return img_url
    except Exception as e:
        print(f"Search error: {e}")
    return None


def add_linked_url_attachment(zotero_api, item_key, url, title=COVER_ATTACHMENT_TITLE):
    data = {
        "itemType": "attachment",
        "linkMode": "linked_url",
        "parentItem": item_key,
        "title": title,
        "url": url
    }
    response = zotero_api.create_items([data])
    if response.get('successful'):
        print(f"Attachment added to item {item_key}: {url}")
    else:
        print(f"Failed to add attachment to item {item_key}: {response.get('failed')}")


def iter_cover_attachments(zotero_api, item_key):
    attachments = zotero_api.children(item_key, itemType='attachment')
    for attachment in attachments:
        data = attachment.get('data', {})
        title = data.get('title', '')
        if 'cover' in title.lower():
            yield attachment


def remove_attachment(zotero_api, attachment):
    key = attachment.get('key')
    parent = attachment.get('data', {}).get('parentItem')
    try:
        zotero_api.delete_item(attachment)
        print(f"Removed attachment {key} from item {parent}")
        return True
    except Exception as error:
        print(f"Failed to remove attachment {key} from item {parent}: {error}")
        return False


def ensure_valid_cover(zotero_api, item):
    item_key = item['key']
    valid_found = False
    to_remove = []

    for attachment in iter_cover_attachments(zotero_api, item_key):
        data = attachment.get('data', {})
        url = data.get('url') or attachment.get('links', {}).get('enclosure', {}).get('href')
        if url and validate_image_url(url):
            valid_found = True
            print(f"Existing cover valid for '{item['data'].get('title', 'Untitled')}'.")
        else:
            to_remove.append(attachment)

    for attachment in to_remove:
        remove_attachment(zotero_api, attachment)

    return valid_found


def get_book_author(creators):
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

    print(f"Found {len(target_items)} {TARGET_ITEM_TYPE}s in {source_label}.")
    return target_items


def main():
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    books = fetch_target_items(zot, COLLECTION_KEY)

    progress = tqdm(
        books,
        desc="Buscando portadas",
        unit="libro",
        total=len(books),
    )

    for book in progress:
        title = book['data'].get('title', 'Unknown Title')
        author = get_book_author(book['data'].get('creators', []))
        item_key = book['key']

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:60])

        print(f"Processing '{title}' (Key: {item_key})")

        if ensure_valid_cover(zot, book):
            print(f"Valid image already attached to '{title}', skipping search.")
            continue

        cover_url = find_book_cover(title, author)
        if cover_url:
            print(f"Cover found: {cover_url}")
            add_linked_url_attachment(zot, item_key, cover_url)
        else:
            print(f"No cover found for '{title}'.")
        
        # Add delay to avoid rate limiting
        time.sleep(SEARCH_DELAY)

    print("All books processed.")


if __name__ == "__main__":
    main()
