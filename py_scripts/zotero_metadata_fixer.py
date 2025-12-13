"""
Script to enrich and correct Zotero book metadata using Web Search and LLM.
"""

import json
import time
from pyzotero import zotero
import requests
from requests import exceptions as requests_exceptions

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs): return iterable

# Configuration
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
TARGET_COLLECTION_KEY = os.getenv("COLLECTION_KEY")

# Search Configuration
MAX_SEARCH_RESULTS = 5
DELAY_BETWEEN_ITEMS = 2

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "minimax-m2:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

def _call_ollama(prompt):
    """Helper to call Ollama API with retry logic."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": OLLAMA_TEMPERATURE},
        "format": "json"
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=payload,
                timeout=OLLAMA_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()
            text_response = (result.get("response") or "").strip()
            return json.loads(text_response)
            
        except requests_exceptions.HTTPError as e:
            if e.response.status_code == 429:
                wait_time = (attempt + 1) * 10
                print(f"  ⚠ Ollama 429 (Too Many Requests). Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            print(f"  ⚠ Ollama extraction failed: {e}")
            return None
            
        except Exception as error:
            print(f"  ⚠ Ollama extraction failed: {error}")
            return None
            
    return None

def search_book_details(title, author):
    """Search for detailed book information."""
    query = f'"{title}" "{author}" book details publisher isbn pages'
    print(f"  Searching for: {title}...")
    
    snippets = []
    try:
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=MAX_SEARCH_RESULTS):
                title_res = result.get("title", "")
                body = result.get("body", "")
                snippets.append(f"Title: {title_res}\nSnippet: {body}")
    except Exception as error:
        print(f"  ⚠ Search failed: {error}")
    
    return snippets

def analyze_metadata(current_item, snippets):
    """Use LLM to correct and enrich metadata."""
    if not snippets:
        return None
    
    # Prepare current metadata for context
    data = current_item["data"]
    current_meta = {
        "title": data.get("title", ""),
        "creators": data.get("creators", []),
        "date": data.get("date", ""),
        "publisher": data.get("publisher", ""),
        "place": data.get("place", ""),
        "numPages": data.get("numPages", ""),
        "ISBN": data.get("ISBN", ""),
        "language": data.get("language", ""),
        "series": data.get("series", ""),
        "abstractNote": data.get("abstractNote", "")
    }
    
    context = "\n".join(f"- {s[:300]}" for s in snippets)
    
    prompt = (
        f"You are a librarian expert. Analyze the book metadata and search results below.\n"
        f"Your task is to CORRECT any errors (spelling, wrong dates) and FILL missing fields.\n"
        f"Pay special attention to AUTHOR NAMES. Correctly split First Names and Last Names (e.g. 'Gabriel García Márquez' -> First: 'Gabriel', Last: 'García Márquez').\n"
        f"Do NOT include explanations, 'likely', 'probably', or parenthetical notes in the values. Just the data.\n"
        f"For 'date', use ONLY YYYY or YYYY-MM-DD format. No text.\n"
        f"Use the search context to verify information.\n\n"
        f"Current Metadata:\n{json.dumps(current_meta, indent=2)}\n\n"
        f"Search Context:\n{context}\n\n"
        f"Respond with a JSON object following this schema (only include fields you are confident about):\n"
        f"{{\n"
        f'  "title": "Corrected Title",\n'
        f'  "creators": [{{ "creatorType": "author", "firstName": "Name", "lastName": "Surname" }}],\n'
        f'  "place": "City, Country",\n'
        f'  "publisher": "Publisher Name",\n'
        f'  "date": "YYYY",\n'
        f'  "numPages": "123",\n'
        f'  "language": "Language",\n'
        f'  "ISBN": "ISBN",\n'
        f'  "series": "Series Name",\n'
        f'  "abstractNote": "Concise summary...",\n'
        f'  "seriesNumber": "Series Number",\n'
        f'  "volume": "Volume",\n'
        f'  "numberOfVolumes": "Total Volumes",\n'
        f'  "edition": "Edition",\n'
        f'  "shortTitle": "Short Title",\n'
        f'  "rights": "Rights/License",\n'
        f'  "archive": "Archive Name",\n'
        f'  "archiveLocation": "Location in Archive",\n'
        f'  "url": "URL to buy or view (Amazon, Goodreads, etc.)",\n'
        f'  "callNumber": "Library Call Number (optional)",\n'
        f'  "libraryCatalog": "Catalog Name (optional)"\n'
        f"}}\n"
    )
    
    return _call_ollama(prompt)

def update_item(zot, item, new_metadata):
    """Update Zotero item with new metadata."""
    if not new_metadata:
        return False
    
    data = item["data"]
    changed = False
    
    # Fields to check
    fields = ["title", "place", "publisher", "date", "numPages", "language", "ISBN", "series", "abstractNote", "callNumber", "libraryCatalog", "url",
              "seriesNumber", "volume", "numberOfVolumes", "edition", "shortTitle", "rights", "archive", "archiveLocation"]
    
    for field in fields:
        new_val = new_metadata.get(field)
        
        # Clean new value
        if isinstance(new_val, str) and new_val.lower() in ["n/a", "not specified", "unknown", "none"]:
            new_val = ""
            
        old_val = data.get(field, "")
        
        # Clean old value if it's garbage (from previous runs)
        if isinstance(old_val, str) and old_val.lower() in ["n/a", "not specified", "unknown", "none"]:
             print(f"    Cleaning garbage value: '{old_val}' -> ''")
             data[field] = ""
             changed = True
             old_val = ""
        
        if new_val and new_val != old_val:
            print(f"    Updating {field}: '{old_val}' -> '{new_val}'")
            data[field] = new_val
            changed = True
            
    # Handle creators
    new_creators = new_metadata.get("creators")
    if new_creators:
        # Simple check: if list length is different or names are different
        # We assume the LLM provides the correct structure
        current_creators = data.get("creators", [])
        # Normalize for comparison (ignore empty fields if not in new)
        if json.dumps(new_creators, sort_keys=True) != json.dumps(current_creators, sort_keys=True):
             print(f"    Updating creators")
             data["creators"] = new_creators
             changed = True
    
    if changed:
        try:
            zot.update_item(item)
            print(f"  ✓ Item updated: {data['title']}")
            return True
        except Exception as e:
            print(f"  ✗ Update failed: {e}")
            return False
    else:
        print(f"  - No changes needed")
        return False

def main():
    print("Connecting to Zotero...")
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    
    try:
        items = zot.collection_items(TARGET_COLLECTION_KEY, limit=100) # Process top 100 for now
        print(f"Found {len(items)} items in collection {TARGET_COLLECTION_KEY}")
    except Exception as e:
        print(f"Error fetching collection: {e}")
        return

    for item in tqdm(items):
        data = item.get("data", {})
        if data.get("itemType") != "book":
            continue
            
        title = data.get("title", "")
        creators = data.get("creators", [])
        author = ""
        if creators:
            author = f"{creators[0].get('firstName', '')} {creators[0].get('lastName', '')}"
            
        print(f"\nProcessing: {title} ({author})")
        
        snippets = search_book_details(title, author)
        new_metadata = analyze_metadata(item, snippets)
        
        update_item(zot, item, new_metadata)
        
        time.sleep(DELAY_BETWEEN_ITEMS)

if __name__ == "__main__":
    main()
