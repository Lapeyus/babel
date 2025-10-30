"""Assign AI-generated tags to Zotero items using search context and the Ollama API."""

import json
import re

from pyzotero import zotero
import requests
from requests import exceptions as requests_exceptions

try:
    from ddgs import DDGS  # Preferred package name.
except ImportError:  # pragma: no cover - fallback for older installs
    from duckduckgo_search import DDGS
try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback when tqdm is missing
    class _TqdmFallback:
        """Minimal tqdm stand-in that behaves like an iterator."""

        def __init__(self, iterable, **_kwargs):
            self._iterable = iterable

        def __iter__(self):
            for item in self._iterable:
                yield item

        def set_postfix_str(self, *_args, **_kwargs):
            return None

        def set_description_str(self, *_args, **_kwargs):
            return None

    def tqdm(iterable, **kwargs):  # type: ignore[redefined-outer-name]
        return _TqdmFallback(iterable, **kwargs)


ZOTERO_USER_ID = "1595072"
ZOTERO_API_KEY = ""
LIBRARY_TYPE = "user"
COLLECTION_KEY = "H4STB4UH"  # Set to None to process the entire library
TARGET_ITEM_TYPE = "book"

MAX_SEARCH_RESULTS = 5
MIN_SNIPPET_LENGTH = 60
MAX_TAGS = 6
REPLACE_EXISTING_AI_TAGS = True
AI_TAG_PREFIX = "[AI] "
AI_TAG_TYPE = 0  # 0 = manual tag, 1 = automatic

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "minimax-m2:cloud"
OLLAMA_TIMEOUT = 60
OLLAMA_TEMPERATURE = 0.2


def fetch_collection_books(zotero_api, collection_key, item_type=TARGET_ITEM_TYPE):
    """Return every item within the specified collection."""
    return zotero_api.everything(
        zotero_api.collection_items(collection_key, itemType=item_type)
    )


def get_book_author(creators):
    """Return the best author name available for a Zotero item."""
    for creator in creators:
        if creator.get("creatorType") != "author":
            continue
        if creator.get("name"):
            return creator["name"].strip()
        parts = [creator.get("firstName"), creator.get("lastName")]
        name = " ".join(part for part in parts if part)
        if name:
            return name.strip()
    return None


def search_book_information(title, author=None, max_results=MAX_SEARCH_RESULTS):
    """Collect short snippets about a book using DuckDuckGo text search."""
    query = f"{title} book themes"
    if author:
        query += f" by {author}"

    print(f"Searching context for '{title}' with query: {query}")
    snippets = []

    try:
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=max_results):
                parts = []
                for key in ("title", "body"):
                    value = result.get(key)
                    if value:
                        cleaned = " ".join(value.split())
                        if cleaned:
                            parts.append(cleaned)
                snippet = ". ".join(parts)
                if len(snippet) >= MIN_SNIPPET_LENGTH:
                    snippets.append(snippet)
    except Exception as error:  # noqa: BLE001
        print(f"Search failed for '{title}': {error}")

    return snippets


def build_tag_prompt(title, author, snippets):
    """Craft the Ollama prompt to elicit thematic tags."""
    header = [
        "You are cataloguing librarian.",
        f"Suggest concise, meaningful tags for the book '{title}'.",
    ]
    if author:
        header.append(f"The author is {author}.")
    header.append(
        "Provide 3 to 6 short tags (1-3 words) that describe the book's themes, topics, or settings."
    )
    header.append("Return only a JSON array of strings with no extra commentary.")

    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = " ".join(header) + "\n\nContext:\n" + context_block + "\n\nTags:"
    return prompt


def extract_tags_from_text(text):
    """Parse a textual response into a list of candidate tags."""
    text = text.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Attempt to recover by isolating the first JSON array.
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = None

    if parsed is None:
        # Split on newlines or commas as a fallback.
        parts = re.split(r"[\n,;]+", text)
        return [part.strip(" -*•\t") for part in parts if part.strip(" -*•\t")]

    if isinstance(parsed, str):
        return [parsed.strip()]
    if isinstance(parsed, list):
        tags = []
        for item in parsed:
            if isinstance(item, str):
                tags.append(item.strip())
        return tags

    return []


def generate_tags_with_ollama(title, author, snippets):
    """Send context to Ollama and return a list of tag strings."""
    if not snippets:
        print(f"No context available to generate tags for '{title}'.")
        return []

    prompt = build_tag_prompt(title, author, snippets)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": OLLAMA_TEMPERATURE},
    }

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
    except requests_exceptions.RequestException as error:
        print(f"Ollama request failed for '{title}': {describe_ollama_error(error)}")
        return []

    result = response.json()
    text = (result.get("response") or result.get("text") or "").strip()
    tags = extract_tags_from_text(text)

    filtered = []
    for tag in tags:
        cleaned = " ".join(tag.split())
        if cleaned:
            filtered.append(cleaned)
        if len(filtered) >= MAX_TAGS:
            break

    return filtered


def normalize_tag(tag):
    return tag.strip().lower()


def describe_ollama_error(error):
    """Return a more actionable message for Ollama HTTP errors."""
    response = getattr(error, "response", None)
    if not response:
        return str(error)

    details = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        details = payload.get("error") or payload.get("message") or ""

    if not details:
        text = response.text.strip()
        if text:
            details = text

    if response.status_code == 404 and not details:
        details = (
            "Model not found on Ollama. Ensure the model exists "
            f"(e.g. run 'ollama pull {OLLAMA_MODEL}')."
        )

    if details:
        return f"{error} -> {details}"
    return str(error)


def update_item_tags(zotero_api, item, tags):
    """Merge AI tags into the item's existing tag list."""
    if not tags:
        return False

    data = item.get("data", {})
    existing_tags = data.get("tags") or []

    if REPLACE_EXISTING_AI_TAGS:
        existing_tags = [
            tag_entry
            for tag_entry in existing_tags
            if not tag_entry.get("tag", "").startswith(AI_TAG_PREFIX)
        ]

    existing_lookup = {
        normalize_tag(entry.get("tag", "")) for entry in existing_tags if entry.get("tag")
    }

    added_any = False
    for tag in tags:
        ai_tag = f"{AI_TAG_PREFIX}{tag}"
        if normalize_tag(ai_tag) in existing_lookup:
            continue
        existing_tags.append({"tag": ai_tag, "type": AI_TAG_TYPE})
        existing_lookup.add(normalize_tag(ai_tag))
        added_any = True

    if not added_any:
        print(f"No new tags added for item {item.get('key')}.")
        return False

    data["tags"] = existing_tags

    try:
        zotero_api.update_item(item)
        print(f"Updated tags for item {item.get('key')}.")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"Failed to update tags for item {item.get('key')}: {error}")
        return False


def process_items(zotero_api, items):
    """Generate and apply AI tags for matching items."""
    book_items = [
        item for item in items if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    total_items = len(book_items)
    if not total_items:
        print("No matching items to process.")
        return

    progress = tqdm(
        book_items,
        desc="Tagging items",
        unit="item",
        total=total_items,
    )

    for item in progress:
        data = item["data"]
        title = data.get("title") or "Untitled"
        author = get_book_author(data.get("creators", []))

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:60])

        print(f"Processing '{title}' (Key: {item.get('key')})")

        snippets = search_book_information(title, author)
        if not snippets:
            print(f"No search snippets for '{title}', skipping.")
            continue

        tags = generate_tags_with_ollama(title, author, snippets)
        if not tags:
            print(f"Failed to generate tags for '{title}'.")
            continue

        update_item_tags(zotero_api, item, tags)


def main():
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

    if COLLECTION_KEY:
        items = fetch_collection_books(zot, COLLECTION_KEY)
        print(f"Found {len(items)} items in collection {COLLECTION_KEY}.")
    else:
        items = zot.everything(zot.items(itemType=TARGET_ITEM_TYPE))
        print(f"Found {len(items)} {TARGET_ITEM_TYPE}s in library.")

    process_items(zot, items)
    print("All items processed.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTag generation interrupted by user.")
