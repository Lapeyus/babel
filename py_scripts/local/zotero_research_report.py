"""
Generate comprehensive research reports for Zotero items using Google Gemini Deep Research.
"""

import os
import time
import json
from pyzotero import zotero
from google import genai
from dotenv import load_dotenv

# Try importing tqdm for progress bars, with a fallback
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs): return iterable

load_dotenv()

# Zotero Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"
COLLECTION_KEY = os.getenv("COLLECTION_KEY", "").strip() or None
TARGET_ITEM_TYPE = os.getenv("TARGET_ITEM_TYPE", "").strip() or "book"

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DEEP_RESEARCH_MODEL = "deep-research-pro-preview-12-2025"

# Initialize Gemini Client
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    print("Warning: GEMINI_API_KEY is not set.")
    client = None

# State persistence
STATE_FILE = "research_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

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

def generate_research_report(title, author):
    """Use Gemini Deep Research to generate a comprehensive report with streaming and resilience."""
    if not client:
        print("    ✗ Gemini client not initialized.")
        return None

    state = load_state()
    item_key = f"{title}_{author}" # Simple key for state tracking
    interaction_id = state.get(item_key)
    
    prompt = f"""
    Research the book '{title}' by {author}.
    
    Format the output as a comprehensive literary analysis in SPANISH using Markdown with the following structure:
    1. Plot Summary (or Executive Summary for non-fiction)
    2. Key Themes and Motifs
    3. Character Analysis (if applicable)
    4. Narrative Style and Structure
    5. Historical and Literary Context
    6. Critical Reception and Impact
    
    IMPORTANT: 
    - The entire report must be written in Spanish.
    - If comprehensive information is NOT available for all sections, report whatever information IS found rather than failing. 
    - Clearly state any missing information in the relevant sections.
    """

    print(f"    ℹ Starting deep research for: {title}...")

    # State tracking
    last_event_id = None
    is_complete = False
    full_text = ""

    def process_stream(event_stream):
        """Helper to process events from any stream source."""
        nonlocal last_event_id, interaction_id, is_complete, full_text
        for event in event_stream:
            # Capture Interaction ID
            if event.event_type == "interaction.start":
                interaction_id = event.interaction.id
                # print(f"    ℹ Interaction started: {interaction_id}")
                
                # Persist state
                state = load_state()
                state[item_key] = interaction_id
                save_state(state)

            # Capture Event ID for resumption
            if event.event_id:
                last_event_id = event.event_id

            # Process Content
            if event.event_type == "content.delta":
                if event.delta.type == "text":
                    full_text += event.delta.text
                elif event.delta.type == "thought_summary":
                    print(f"    Thinking: {event.delta.content.text}")

            # Check completion
            if event.event_type in ['interaction.complete', 'error']:
                is_complete = True
    
    # Check if we are resuming
    if interaction_id:
        print(f"    ℹ Checking status of existing interaction: {interaction_id}")
        try:
            # Check status first
            existing = client.interactions.get(id=interaction_id)
            if existing.status in ["succeeded", "completed"]:
                 print(f"    ✓ Interaction already completed (Status: {existing.status}). Fetching result...")
                 is_complete = True
                 if existing.outputs:
                     full_text = existing.outputs[-1].text
            elif existing.status == "failed":
                 print(f"    ✗ Interaction marked as failed. Starting new...")
                 interaction_id = None
            else:
                 # Still running/pending, resume streaming
                 print(f"    ℹ Resuming stream for status: {existing.status}")
                 resume_stream = client.interactions.get(id=interaction_id, stream=True)
                 process_stream(resume_stream)

        except Exception as e:
            print(f"    ⚠ Failed to resume existing interaction (starting new): {e}")
            interaction_id = None # Reset to start new

    if not interaction_id:
        # 1. Attempt initial streaming request
        try:
            initial_stream = client.interactions.create(
                input=prompt,
                agent=DEEP_RESEARCH_MODEL,
                background=True,
                stream=True,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": "auto"
                }
            )
            process_stream(initial_stream)
        except Exception as e:
            print(f"    ⚠ Initial connection dropped: {e}")

    # 2. Reconnection Loop
    retry_count = 0
    max_retries = 5
    
    while not is_complete and interaction_id and retry_count < max_retries:
        print(f"    ⚠ Stream interrupted. Resuming from event {last_event_id}...")
        time.sleep(2) 

        try:
            resume_stream = client.interactions.get(
                id=interaction_id,
                stream=True,
                last_event_id=last_event_id
            )
            process_stream(resume_stream)
            retry_count = 0 
        except Exception as e:
            retry_count += 1
            print(f"    ⚠ Reconnection failed ({retry_count}/{max_retries}): {e}")
            time.sleep(5)

    if is_complete:
        print("    ✓ Research completed.")
        
        # Fallback: If streaming didn't capture text, fetch it from the interaction directly
        if not full_text:
            try:
                print("    ℹ Streaming didn't capture text. Fetching final result...")
                # Slight delay to ensure consistency
                time.sleep(2)
                final_interaction = client.interactions.get(id=interaction_id)
                
                print(f"    DEBUG: Interaction Status: {final_interaction.status}")
                if final_interaction.outputs:
                    print(f"    DEBUG: Outputs found: {len(final_interaction.outputs)}")
                    # Inspect the first output structure
                    print(f"    DEBUG: Output[0] content (partial): {str(final_interaction.outputs[0])[:200]}...")
                    full_text = final_interaction.outputs[-1].text
                else:
                    print("    DEBUG: No outputs found in final interaction object.")
                    # Safely check for error
                    try:
                        if hasattr(final_interaction, 'error') and final_interaction.error:
                             print(f"    DEBUG: Interaction Error: {final_interaction.error}")
                    except Exception:
                        pass

            except Exception as e:
                print(f"    ⚠ Could not fetch final result: {e}")
        
        # NOTE: State deletion is now handled in process_items AFTER successful save
        return full_text
    else:
        print("    ✗ Research failed or timed out.")
        # Do NOT clear state here, allowing for retry/follow-up on failure
        return None

def save_report_as_note(zotero_api, item, report_content):
    """Save the generated report as a child note of the item."""
    item_key = item['key']
    
    # Dedicated Title/Header for the note
    # This ensures the note is easily identifiable in the Zotero list
    title_header = "# Reporte de Investigación Profunda (Gemini)\n\n"
    full_report_markdown = title_header + report_content
    
    html_content = ""
    try:
        import markdown
        html_content = markdown.markdown(full_report_markdown)
    except ImportError:
        # Fallback
        html_content = f"<h1>Reporte de Investigación Profunda (Gemini)</h1><pre>{report_content}</pre>"

    new_note = {
        "itemType": "note",
        "parentItem": item_key,
        "note": html_content,
        "tags": [{"tag": "deep-research"}]
    }

    try:
        zotero_api.create_items([new_note])
        print(f"    ✓ Saved report as note for item {item_key}.")
        return True
    except Exception as e:
        print(f"    ✗ Failed to save note for item {item_key}: {e}")
        return False

def check_existing_research_note(zotero_api, item):
    """Check if the item already has a 'Deep Research' note to avoid duplication."""
    try:
        children = zotero_api.children(item['key'])
        for child in children:
            if child['data'].get('itemType') == 'note':
                # Check for the specific tag first (most reliable)
                for tag in child['data'].get('tags', []):
                    if tag.get('tag', '').lower() == 'deep-research':
                        return True
                
                # Check by content - multiple patterns for robustness
                note_content = child['data'].get('note', '').lower()
                
                # Check for various possible patterns (HTML or plain text)
                detection_patterns = [
                    'reporte de investigación profunda',
                    'reporte de investigacion profunda',  # without accent
                    '>reporte de investigación profunda<',  # HTML tag content
                    'investigación profunda (gemini)',
                    'investigacion profunda (gemini)',
                    'deep research report',
                ]
                
                for pattern in detection_patterns:
                    if pattern in note_content:
                        return True
                        
    except Exception as e:
        print(f"    ⚠ Error checking children for {item['key']}: {e}")
    return False


def process_items(zotero_api, items):
    """Process items: generate research and save as note."""
    book_items = [
        item for item in items if item.get("data", {}).get("itemType") == TARGET_ITEM_TYPE
    ]
    total_items = len(book_items)
    
    if not total_items:
        print("No matching items to process.")
        return

    progress = tqdm(book_items, desc="Researching items", unit="item")

    for item in progress:
        data = item["data"]
        title = data.get("title") or "Untitled"
        item_key = item['key']
        author = get_book_author(data.get("creators", []))

        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(title[:50])

        print(f"\n📖 Processing '{title}' (Key: {item_key})")

        # Check for existing research note
        if check_existing_research_note(zotero_api, item):
            print("    ℹ Research note already exists. Skipping.")
            continue

        report = generate_research_report(title, author)
        
        if report:
             if save_report_as_note(zotero_api, item, report):
                 # Clear state ONLY after successful save
                 state_key = f"{title}_{author}"
                 state = load_state()
                 if state_key in state:
                     del state[state_key]
                     save_state(state)
        else:
            print(f"    ⚠ Aborted - No report generated for '{title}'.")

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
