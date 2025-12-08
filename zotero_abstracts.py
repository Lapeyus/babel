"""Generate book abstracts in Zotero using search context and the Ollama API."""

from pyzotero import zotero
import requests
from requests import exceptions as requests_exceptions
try:
    from ddgs import DDGS  # Prefer the renamed package to avoid runtime warnings.
except ImportError:
    from duckduckgo_search import DDGS
try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback when tqdm is missing
    class _TqdmFallback:
        """Minimal tqdm stand-in that behaves like an iterator."""

        def __init__(self, iterable, **kwargs):
            self._iterable = iterable

        def __iter__(self):
            for item in self._iterable:
                yield item

        def set_postfix_str(self, *_args, **_kwargs):
            return None

        def set_description_str(self, *_args, **_kwargs):
            return None

    def tqdm(iterable, **kwargs):
        return _TqdmFallback(iterable, **kwargs)

ZOTERO_USER_ID = "1595072"
ZOTERO_API_KEY = "mB0Blp4yjVIuX17QBYLsswIM"
LIBRARY_TYPE = "user"
COLLECTION_KEY = "6B5XG7TP"  # Leave empty to process the entire library
TARGET_ITEM_TYPE = "book"

MAX_SEARCH_RESULTS = 5
MIN_SNIPPET_LENGTH = 60
OVERWRITE_EXISTING_ABSTRACTS = False

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "minimax-m2:cloud"
OLLAMA_TIMEOUT = 60
OLLAMA_TEMPERATURE = 0.3


def fetch_items_recursively(zotero_api, collection_key):
    """Fetch items from a collection and its subcollections."""
    all_items = []
    
    # Fetch items in current collection
    try:
        items = zotero_api.everything(zotero_api.collection_items(collection_key))
        all_items.extend(items)
    except Exception as e:
        print(f"Error fetching items from {collection_key}: {e}")

    # Fetch subcollections
    try:
        subcollections = zotero_api.collections_sub(collection_key)
        for sub in subcollections:
            sub_key = sub['key']
            # print(f"Found subcollection: {sub['data']['name']} ({sub_key})")
            all_items.extend(fetch_items_recursively(zotero_api, sub_key))
    except Exception as e:
        print(f"Error fetching subcollections from {collection_key}: {e}")
        
    return all_items

def fetch_target_items(zotero_api, collection_key=None):
    """Return every item matching the target type within the selection (recursive)."""
    if collection_key:
        raw_items = fetch_items_recursively(zotero_api, collection_key)
        source_label = f"collection {collection_key} and subcollections"
    else:
        raw_items = zotero_api.everything(zotero_api.items())
        source_label = "library"

    target_items = [
        item
        for item in raw_items
        if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    
    # Deduplicate by key (in case item is in multiple subcollections?)
    # Zotero items have unique keys.
    seen_keys = set()
    unique_items = []
    for item in target_items:
        key = item['key']
        if key not in seen_keys:
            seen_keys.add(key)
            unique_items.append(item)

    print(f"Found {len(unique_items)} {TARGET_ITEM_TYPE}s in {source_label}.")
    return unique_items


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
    query = f"{title} book summary"
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


def build_abstract_prompt(title, author, snippets):
    """Create the inference prompt for the Ollama model."""
    header = [
        "You are a research assistant creating an academic-style abstract.",
        f"Write an abstract in SPANISH for the book '{title}'.",
    ]
    if author:
        header.append(f"The author is {author}.")
    header.append(
        "Use only the supplied context, highlight key themes, and keep the abstract under 160 words. Do not fabricate information. Do not reference the context directly. Do not add titles like 'Abstract' or 'Summary'. Output ONLY the Spanish text."
    )

    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = " ".join(header) + "\n\nContext:\n" + context_block + "\n\nAbstract (in Spanish):"
    return prompt


def check_and_translate_abstract(existing_abstract, title, author, snippets):
    """Check if abstract is in Spanish, if not rewrite it."""
    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = (
        f"Analyze the following abstract for the book '{title}'.\n"
        f"Current Abstract: {existing_abstract}\n\n"
        f"Task: Check if the Current Abstract is in Spanish.\n"
        f"1. If it is in Spanish, return it exactly as is.\n"
        f"2. If it is NOT in Spanish, write a new abstract in Spanish based on the Context below.\n"
        f"Output ONLY the final Spanish abstract.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Spanish Abstract:"
    )
    
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
        result = response.json()
        abstract = (result.get("response") or result.get("text") or "").strip()
        return abstract
    except Exception as e:
        print(f"Translation check failed: {e}")
        return existing_abstract


def generate_abstract_with_ollama(title, author, snippets):
    """Send a prompt to Ollama and return the generated abstract."""
    if not snippets:
        print(f"No context available to generate abstract for '{title}'.")
        return None

    prompt = build_abstract_prompt(title, author, snippets)
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
        print(f"Ollama request failed for '{title}': {error}")
        return None

    result = response.json()
    abstract = (result.get("response") or result.get("text") or "").strip()
    if abstract:
        return abstract

    print(f"Unexpected Ollama response for '{title}': {result}")
    return None


def update_item_abstract(zotero_api, item, abstract):
    """Update the Zotero item's abstract and persist the change."""
    item_key = item.get("key")
    item["data"]["abstractNote"] = abstract

    try:
        zotero_api.update_item(item)
        print(f"Updated abstract for item {item_key}.")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"Failed to update abstract for item {item_key}: {error}")
        return False


def process_items(zotero_api, items):
    """Generate and store abstracts for each item in the provided iterable."""
    book_items = [
        item for item in items if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    total_items = len(book_items)
    if not total_items:
        print("No matching items to process.")
        return

    progress = tqdm(
        book_items,
        desc="Generating abstracts",
        unit="item",
        total=total_items,
    )

    for item in progress:
        data = item["data"]
        title = data.get("title") or "Untitled"
        author = get_book_author(data.get("creators", []))
        existing = (data.get("abstractNote") or "").strip()

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:60])

        print(f"Processing '{title}' (Key: {item.get('key')})")

        snippets = search_book_information(title, author)
        
        if existing:
            if not snippets:
                print(f"No snippets to verify language for '{title}', skipping.")
                continue
            
            # Check if existing is Spanish
            new_abstract = check_and_translate_abstract(existing, title, author, snippets)
            if new_abstract and new_abstract != existing:
                print(f"Rewriting abstract in Spanish for '{title}'.")
                update_item_abstract(zotero_api, item, new_abstract)
            else:
                print(f"Abstract already in Spanish (or check failed) for '{title}'.")
            continue

        if not snippets:
            print(f"No search snippets for '{title}', skipping.")
            continue

        abstract = generate_abstract_with_ollama(title, author, snippets)
        if not abstract:
            print(f"Failed to generate abstract for '{title}'.")
            continue

        update_item_abstract(zotero_api, item, abstract)


def main():
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

    items = fetch_target_items(zot, COLLECTION_KEY)

    process_items(zot, items)
    print("All items processed.")


if __name__ == "__main__":
    main()
