"""Generate tags and relations for Zotero items using search context and the Ollama API."""

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

import os
from dotenv import load_dotenv

load_dotenv()

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
COLLECTION_KEY = os.getenv("COLLECTION_KEY")
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "minimax-m2:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

# Search Configuration
MAX_SEARCH_RESULTS = 5
MIN_SNIPPET_LENGTH = 60
OVERWRITE_EXISTING_TAGS = False

# Relation scoring weights
WEIGHT_SAME_AUTHOR = 5
WEIGHT_SHARED_TAG = 1
WEIGHT_SHARED_GENRE = 2
RELATION_THRESHOLD = 5  # Minimum score to create a relation


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


def build_enrichment_prompt(title, author, snippets):
    """Create the inference prompt for the Ollama model to generate tags and genres."""
    header = [
        "You are a librarian expert in book classification.",
        f"Analyze the book '{title}' based on the context provided.",
    ]
    if author:
        header.append(f"The author is {author}.")
    header.append(
        "Provide the following in strictly valid JSON format:\n"
        "{\n"
        '  "tags": ["tag1", "tag2", ...],  // 5-10 specific subject tags (lowercase)\n'
        '  "genres": ["genre1", "genre2", ...], // 2-4 broad genres\n'
        '  "keywords": ["keyword1", "keyword2", ...] // 3-5 key themes\n'
        "}\n"
        "IMPORTANT: All tags, genres, and keywords MUST be in the original language of the book. "
        "If the book is in Spanish, use Spanish tags. If in English, use English tags.\n"
        "Do not include any text outside the JSON block."
    )

    context_block = "\n".join(f"- {snippet}" for snippet in snippets)
    prompt = " ".join(header) + "\n\nContext:\n" + context_block + "\n\nJSON Response:"
    return prompt


def generate_metadata_with_ollama(title, author, snippets):
    """Send a prompt to Ollama and return the generated metadata dict."""
    if not snippets:
        print(f"No context available to generate metadata for '{title}'.")
        return None

    prompt = build_enrichment_prompt(title, author, snippets)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": OLLAMA_TEMPERATURE},
        "format": "json" # Force JSON mode if supported by the model/Ollama version
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
        metadata = json.loads(text_response)
        return metadata
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


def update_item_tags(zotero_api, item, new_tags):
    """Update the Zotero item's tags."""
    item_key = item.get("key")
    current_tags = item["data"].get("tags", [])
    current_tag_strings = {t["tag"] for t in current_tags}
    
    added_count = 0
    for tag in new_tags:
        if tag not in current_tag_strings:
            item["data"]["tags"].append({"tag": tag})
            added_count += 1
            
    if added_count > 0:
        try:
            zotero_api.update_item(item)
            print(f"Added {added_count} tags to item {item_key}.")
            return True
        except Exception as error:  # noqa: BLE001
            print(f"Failed to update tags for item {item_key}: {error}")
            return False
    else:
        print(f"No new tags to add for item {item_key}.")
        return True


def update_item_relations(zotero_api, item_key, related_item_uris):
    """Update the Zotero item's relations."""
    if not related_item_uris:
        return False
        
    # Re-fetch item to ensure we have the latest version (avoiding 412 Precondition Failed)
    try:
        item = zotero_api.item(item_key)
    except Exception as error:
        print(f"Failed to fetch latest version of item {item_key}: {error}")
        return False

    relations = item["data"].get("relations", {})
    current_relations = relations.get("dc:relation", [])
    
    if isinstance(current_relations, str):
        current_relations = [current_relations]
        
    changed = False
    for uri in related_item_uris:
        if uri not in current_relations:
            current_relations.append(uri)
            changed = True
            
    if changed:
        item["data"]["relations"]["dc:relation"] = current_relations
        try:
            zotero_api.update_item(item)
            print(f"Updated relations for item {item_key} (Added {len(related_item_uris)} relations).")
            return True
        except Exception as error:  # noqa: BLE001
            print(f"Failed to update relations for item {item_key}: {error}")
            return False
    return False


def calculate_relations(items_metadata):
    """
    Calculate relations between items based on metadata similarity.
    items_metadata: dict of item_key -> {'author': str, 'tags': set, 'genres': set, 'keywords': set}
    Returns: dict of item_key -> list of related_item_uris
    """
    relations_map = {key: set() for key in items_metadata}
    keys = list(items_metadata.keys())
    
    print("Calculating relations...")
    for i in range(len(keys)):
        key_a = keys[i]
        meta_a = items_metadata[key_a]
        
        for j in range(i + 1, len(keys)):
            key_b = keys[j]
            meta_b = items_metadata[key_b]
            
            score = 0
            
            # Author match
            if meta_a['author'] and meta_b['author'] and meta_a['author'].lower() == meta_b['author'].lower():
                score += WEIGHT_SAME_AUTHOR
                
            # Tag overlap
            common_tags = meta_a['tags'].intersection(meta_b['tags'])
            score += len(common_tags) * WEIGHT_SHARED_TAG
            
            # Genre overlap
            common_genres = meta_a['genres'].intersection(meta_b['genres'])
            score += len(common_genres) * WEIGHT_SHARED_GENRE
            
            # Keyword overlap
            common_keywords = meta_a['keywords'].intersection(meta_b['keywords'])
            score += len(common_keywords) * WEIGHT_SHARED_TAG # Treat keywords like tags
            
            if score >= RELATION_THRESHOLD:
                # Add bidirectional relation
                # Construct URI: http://zotero.org/users/{userID}/items/{itemKey}
                # Note: Assuming user library. If group, it would be groups/{groupID}
                uri_base = f"http://zotero.org/users/{ZOTERO_USER_ID}/items"
                
                relations_map[key_a].add(f"{uri_base}/{key_b}")
                relations_map[key_b].add(f"{uri_base}/{key_a}")
                
    return relations_map


def process_items(zotero_api, items):
    """Generate tags and relations for items."""
    book_items = [
        item for item in items if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    total_items = len(book_items)
    if not total_items:
        print("No matching items to process.")
        return

    # Store metadata for relation calculation
    # key -> {author, tags, genres, keywords}
    items_metadata_cache = {}

    print("--- Phase 1: Generating Metadata and Tags ---")
    progress = tqdm(
        book_items,
        desc="Enriching items",
        unit="item",
        total=total_items,
    )

    for item in progress:
        data = item["data"]
        key = item["key"]
        title = data.get("title") or "Untitled"
        author = get_book_author(data.get("creators", []))
        
        # Initialize metadata cache for this item with existing data
        existing_tags = {t["tag"] for t in data.get("tags", [])}
        items_metadata_cache[key] = {
            "author": author,
            "tags": existing_tags,
            "genres": set(),
            "keywords": set()
        }

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:60])

        print(f"Processing '{title}' (Key: {key})")

        snippets = search_book_information(title, author)
        if not snippets:
            print(f"No search snippets for '{title}', using existing metadata only.")
            continue

        metadata = generate_metadata_with_ollama(title, author, snippets)
        if not metadata:
            print(f"Failed to generate metadata for '{title}'.")
            continue
            
        # Extract new metadata
        new_tags = metadata.get("tags", [])
        new_genres = metadata.get("genres", [])
        new_keywords = metadata.get("keywords", [])
        
        # Update Zotero with tags (combine tags, genres, keywords as Zotero tags?)
        # User asked to "create tags on items". Usually genres and keywords are also useful as tags.
        # Let's add them all as tags in Zotero, but keep them separate for relation logic if possible.
        # For simplicity and utility, we'll add all generated lists to Zotero tags.
        
        all_new_tags = list(set(new_tags + new_genres + new_keywords))
        update_item_tags(zotero_api, item, all_new_tags)
        
        # Update cache for relation calculation
        items_metadata_cache[key]["tags"].update(new_tags)
        items_metadata_cache[key]["genres"].update(new_genres)
        items_metadata_cache[key]["keywords"].update(new_keywords)

    print("\n--- Phase 2: Calculating and Creating Relations ---")
    relations_map = calculate_relations(items_metadata_cache)
    
    # Apply relations
    for item in book_items:
        key = item["key"]
        related_uris = relations_map.get(key)
        if related_uris:
            print(f"Linking {len(related_uris)} items to '{item['data']['title']}'")
            # Pass key instead of item object to force re-fetch
            update_item_relations(zotero_api, key, list(related_uris))


def main():
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

    items = fetch_target_items(zot, COLLECTION_KEY)

    process_items(zot, items)
    print("All items processed.")


if __name__ == "__main__":
    main()
