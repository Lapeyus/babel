"""Retrieve original publication dates for classic works and add them to Zotero's Extra field."""

import json
import re
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

# Zotero Configuration
import os
from dotenv import load_dotenv

load_dotenv()

# Zotero Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
COLLECTION_KEY = os.getenv("COLLECTION_KEY")
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE")

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "minimax-m2:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

# Search Configuration
MAX_SEARCH_RESULTS = 3
OVERWRITE_EXISTING_DATES = False  # Set to True to overwrite existing original-date fields


def fetch_target_items(zotero_api, collection_key=None):
    """Return every item matching the target type within the selection."""
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


def search_original_date_context(title, author=None, max_results=MAX_SEARCH_RESULTS):
    """Collect snippets about original publication date using DuckDuckGo."""
    query = f'"{title}" original publication date first published'
    if author:
        query += f' "{author}"'

    print(f"Searching original date for '{title}'...")
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
                if snippet:
                    snippets.append(snippet)
    except Exception as error:  # noqa: BLE001
        print(f"Search failed for '{title}': {error}")

    return snippets


def build_date_extraction_prompt(title, author, snippets):
    """Create the inference prompt for the Ollama model to extract original publication date."""
    header = [
        "You are a librarian expert in bibliographic research.",
        f"Determine the ORIGINAL publication date for the book '{title}'.",
    ]
    if author:
        header.append(f"The author is {author}.")
    
    header.append(
        "Based on the context provided, identify when this book was FIRST published.\n"
        "Respond with ONLY a valid JSON object in this exact format:\n"
        "{\n"
        '  "original_date": "YYYY" or "YYYY-MM-DD",\n'
        '  "confidence": "high" or "medium" or "low",\n'
        '  "notes": "brief explanation of date source"\n'
        "}\n\n"
        "RULES:\n"
        "- Use YYYY format for year only, or YYYY-MM-DD for full date if available\n"
        "- If multiple editions exist, use the FIRST publication date\n"
        "- If you cannot find a reliable date, set original_date to null\n"
        "- Do not include any text outside the JSON block."
    )

    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = " ".join(header) + "\n\nContext:\n" + context_block + "\n\nJSON Response:"
    return prompt


def extract_original_date(title, author, snippets):
    """Send a prompt to Ollama and return the extracted date information."""
    if not snippets:
        print(f"No context available to extract date for '{title}'.")
        return None

    prompt = build_date_extraction_prompt(title, author, snippets)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": OLLAMA_TEMPERATURE},
        "format": "json"
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
    text_response = (result.get("response") or result.get("text") or "").strip()
    
    try:
        # Try to parse the JSON
        date_info = json.loads(text_response)
        return date_info
    except json.JSONDecodeError:
        print(f"Failed to parse JSON from Ollama response for '{title}': {text_response[:100]}...")
        # Attempt to clean up markdown code blocks if present
        match = re.search(r'```json\s*(\{.*?\})\s*```', text_response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None


def has_original_date_in_extra(extra_field):
    """Check if the Extra field already contains an original-date CSL variable."""
    if not extra_field:
        return False
    # CSL original-date format in Extra field: "original-date: YYYY" or "original-date: YYYY-MM-DD"
    return bool(re.search(r'^\s*original-date:\s*\d{4}', extra_field, re.MULTILINE | re.IGNORECASE))


def update_extra_with_original_date(extra_field, original_date):
    """Add or update the original-date CSL variable in the Extra field."""
    if not extra_field:
        extra_field = ""
    
    # CSL variable format for original date
    csl_line = f"original-date: {original_date}"
    
    # Check if original-date already exists
    if has_original_date_in_extra(extra_field):
        if OVERWRITE_EXISTING_DATES:
            # Replace existing original-date line
            extra_field = re.sub(
                r'^\s*original-date:.*$',
                csl_line,
                extra_field,
                flags=re.MULTILINE | re.IGNORECASE
            )
        else:
            print("  Original date already exists in Extra field, skipping.")
            return extra_field
    else:
        # Add new line
        if extra_field and not extra_field.endswith('\n'):
            extra_field += '\n'
        extra_field += csl_line
    
    return extra_field


def update_item_extra(zotero_api, item, original_date, notes=""):
    """Update the Zotero item's Extra field with the original date."""
    item_key = item.get("key")
    current_extra = item["data"].get("extra", "")
    
    # Check if we should skip
    if has_original_date_in_extra(current_extra) and not OVERWRITE_EXISTING_DATES:
        print(f"  Item {item_key} already has original-date, skipping.")
        return False
    
    # Update Extra field
    new_extra = update_extra_with_original_date(current_extra, original_date)
    
    if new_extra != current_extra:
        item["data"]["extra"] = new_extra
        try:
            zotero_api.update_item(item)
            print(f"  ✓ Updated item {item_key} with original-date: {original_date}")
            if notes:
                print(f"    Notes: {notes}")
            return True
        except Exception as error:  # noqa: BLE001
            print(f"  ✗ Failed to update item {item_key}: {error}")
            return False
    else:
        print(f"  No changes needed for item {item_key}.")
        return False


def process_items(zotero_api, items):
    """Process items and add original dates to their Extra field."""
    book_items = [
        item for item in items if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    total_items = len(book_items)
    if not total_items:
        print("No matching items to process.")
        return

    print(f"\n--- Processing {total_items} items for original dates ---\n")
    
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    progress = tqdm(
        book_items,
        desc="Retrieving original dates",
        unit="item",
        total=total_items,
    )

    for item in progress:
        data = item["data"]
        key = item["key"]
        title = data.get("title") or "Untitled"
        author = get_book_author(data.get("creators", []))
        
        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:50])

        print(f"\n📖 Processing: '{title}' (Key: {key})")
        if author:
            print(f"   Author: {author}")

        # Check if already has original date
        current_extra = data.get("extra", "")
        if has_original_date_in_extra(current_extra) and not OVERWRITE_EXISTING_DATES:
            print("  Already has original-date in Extra field, skipping.")
            skipped_count += 1
            continue

        # Search for original date context
        snippets = search_original_date_context(title, author)
        if not snippets:
            print("  ⚠ No search results found, skipping.")
            failed_count += 1
            continue

        # Extract original date using Ollama
        date_info = extract_original_date(title, author, snippets)
        if not date_info or not date_info.get("original_date"):
            print("  ⚠ Could not extract original date.")
            failed_count += 1
            continue
        
        original_date = date_info.get("original_date")
        confidence = date_info.get("confidence", "unknown")
        notes = date_info.get("notes", "")
        
        print(f"  📅 Found original date: {original_date} (confidence: {confidence})")
        
        # Update the item
        if update_item_extra(zotero_api, item, original_date, notes):
            updated_count += 1
        else:
            failed_count += 1

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  ✓ Updated: {updated_count}")
    print(f"  ⊘ Skipped: {skipped_count}")
    print(f"  ✗ Failed:  {failed_count}")
    print(f"  Total:     {total_items}")
    print(f"{'='*60}\n")


def main():
    """Main entry point."""
    print("="*60)
    print("Zotero Original Date Retrieval Script")
    print("="*60)
    print(f"Library: {LIBRARY_TYPE}/{ZOTERO_USER_ID}")
    print(f"Collection: {COLLECTION_KEY or 'All items'}")
    print(f"Item type: {TARGET_ITEM_TYPE}")
    print(f"Overwrite existing: {OVERWRITE_EXISTING_DATES}")
    print("="*60 + "\n")

    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

    items = fetch_target_items(zot, COLLECTION_KEY)
    
    if not items:
        print("No items found to process.")
        return

    process_items(zot, items)
    print("✓ All items processed.")


if __name__ == "__main__":
    main()
