"""Generate book abstracts in Zotero using search context and the Google Gemini API (CI-friendly)."""

import os
import time
from pyzotero import zotero
import google.generativeai as genai

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs): return iterable

from dotenv import load_dotenv

load_dotenv()

# Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"
COLLECTION_KEY = os.getenv("COLLECTION_KEY", "").strip() or None
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE", "").strip() or "book"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = "gemini-2.0-flash-lite"

# Search Configuration
MAX_SEARCH_RESULTS = 5
MIN_SNIPPET_LENGTH = 60
SEARCH_DELAY = 1

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
else:
    print("Warning: GEMINI_API_KEY is not set.")
    model = None

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
    
    # Deduplicate by key
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
        time.sleep(SEARCH_DELAY)
    except Exception as error:  # noqa: BLE001
        print(f"    ⚠ Search failed for '{title}': {error}")

    return snippets

def call_gemini(prompt):
    """Call Google Gemini API."""
    if not model:
        print("    ✗ Gemini model not configured.")
        return None
        
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"    ✗ Gemini generation failed: {e}")
        try:
            print("    ℹ Listing available models:")
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    print(f"      - {m.name}")
        except Exception as list_err:
            print(f"      (Could not list models: {list_err})")
        return None

def check_and_translate_abstract(existing_abstract, title, author, snippets):
    """Check if abstract is in Spanish, if not rewrite it."""
    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = (
        f"Analyze the following abstract for the book '{title}'.\n"
        f"Current Abstract: {existing_abstract}\n\n"
        f"Task: Check if the Current Abstract is WRITTEN IN SPANISH.\n"
        f"1. If it is already in Spanish, output the text 'ALREADY_SPANISH'.\n"
        f"2. If it is NOT in Spanish, write a new abstract in Spanish based on the Context below.\n"
        f"Output ONLY the final Spanish abstract or 'ALREADY_SPANISH'. Do not add markdown.\n\n"
        f"Context:\n{context_block}\n\n"
    )
    
    result = call_gemini(prompt)
    if result and "ALREADY_SPANISH" in result:
        return existing_abstract
    return result

def generate_abstract_with_gemini(title, author, snippets):
    """Send a prompt to Gemini and return the generated abstract."""
    if not snippets:
        print(f"    ⚠ No context available for '{title}'.")
        return None

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
    
    return call_gemini(prompt)

def update_item_abstract(zotero_api, item, abstract):
    """Update the Zotero item's abstract and persist the change."""
    item_key = item.get("key")
    item["data"]["abstractNote"] = abstract

    try:
        zotero_api.update_item(item)
        print(f"    ✓ Updated abstract for item {item_key}.")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"    ✗ Failed to update abstract for item {item_key}: {error}")
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

    progress = tqdm(book_items, desc="Generating abstracts", unit="item")

    for item in progress:
        data = item["data"]
        title = data.get("title") or "Untitled"
        author = get_book_author(data.get("creators", []))
        existing = (data.get("abstractNote") or "").strip()
        item_key = item.get("key")

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:50])

        print(f"\n📖 Processing '{title}' (Key: {item_key})")

        snippets = search_book_information(title, author)
        
        if existing:
            # Check if existing is Spanish
            new_abstract = check_and_translate_abstract(existing, title, author, snippets)
            if new_abstract and new_abstract != existing:
                print(f"    Found non-Spanish abstract. Rewriting...")
                update_item_abstract(zotero_api, item, new_abstract)
            else:
                print(f"    Abstract already in Spanish (or skipped).")
            continue

        if not snippets:
            print(f"    ⚠ No search snippets found.")
            continue

        abstract = generate_abstract_with_gemini(title, author, snippets)
        if abstract:
            update_item_abstract(zotero_api, item, abstract)

def main():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        print("Error: ZOTERO_API_KEY and ZOTERO_USER_ID must be set.")
        return
        
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY must be set.")
        return

    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    items = fetch_target_items(zot, COLLECTION_KEY)
    process_items(zot, items)
    print("\nAll items processed.")

if __name__ == "__main__":
    main()
