"""
Auto-save version of Zotero Deep Research follow-up.
Automatically processes and saves all completed research sessions to Zotero.
Based on the working zotero_research_followup.py
"""

import os
import json
import sys
import time
from pyzotero import zotero
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Configuration - exactly matching followup script
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"
STATE_FILE = "research_state.json"

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    print("Error: GEMINI_API_KEY is not set.")
    sys.exit(1)

def get_zotero_client():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        print("Error: ZOTERO_API_KEY and ZOTERO_USER_ID must be set.")
        return None
    return zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)

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

def find_item_in_zotero(zot, title, author):
    """Find a Zotero item by title (and ideally author)."""
    items = zot.items(q=title)
    
    candidates = []
    for item in items:
        data = item.get('data', {})
        if data.get('itemType') not in ['book', 'document']:
            continue
            
        if author and author != "None":
            creators = data.get('creators', [])
            found_author = False
            for c in creators:
                name = c.get('name', '') or f"{c.get('firstName', '')} {c.get('lastName', '')}"
                if author.lower() in name.lower():
                    found_author = True
                    break
            if not found_author:
                continue
        
        candidates.append(item)
    
    if len(candidates) >= 1:
        return candidates[0]
    
    return None

def save_report_as_note(zotero_api, item, report_content):
    """Save the generated report as a child note of the item."""
    item_key = item['key']
    
    title_header = "# Reporte de Investigación Profunda (Gemini)\n\n"
    full_report_markdown = title_header + report_content
    
    html_content = ""
    try:
        import markdown
        html_content = markdown.markdown(full_report_markdown)
    except ImportError:
        html_content = f"<h1>Reporte de Investigación Profunda (Gemini)</h1><pre>{report_content}</pre>"

    new_note = {
        "itemType": "note",
        "parentItem": item_key,
        "note": html_content,
        "tags": [{"tag": "deep-research"}]
    }

    try:
        zotero_api.create_items([new_note])
        return True
    except Exception as e:
        print(f"    ✗ Failed to save note: {e}")
        return False

def autosave_all_completed():
    """Automatically process and save all completed research sessions."""
    zot = get_zotero_client()
    if not zot:
        print("Cannot proceed without Zotero client.")
        return
    
    state = load_state()
    if not state:
        print("No active research sessions found.")
        return
    
    print(f"\n{'='*60}")
    print("AUTO-SAVE: Processing all completed research sessions")
    print(f"{'='*60}")
    print(f"Found {len(state)} session(s) to check.\n")
    
    stats = {
        "checked": 0,
        "completed": 0,
        "saved": 0,
        "still_running": 0,
        "failed": 0,
        "not_found": 0,
        "errors": 0
    }
    
    sessions_to_remove = []
    
    for key, interaction_id in list(state.items()):
        stats["checked"] += 1
        
        try:
            title, author = key.rsplit('_', 1)
            display_name = f"{title} by {author}"
        except ValueError:
            display_name = key
            title = key
            author = ""
        
        print(f"📖 {display_name}")
        
        try:
            # Use the exact same API call as the working followup script
            interaction = client.interactions.get(id=interaction_id)
            status = interaction.status
            
            if status == "succeeded" or status == "completed":
                stats["completed"] += 1
                
                report_content = ""
                if interaction.outputs:
                    report_content = interaction.outputs[-1].text
                
                if report_content:
                    print(f"   ✓ Completed ({len(report_content)} chars)")
                    
                    # Find in Zotero
                    item = find_item_in_zotero(zot, title, author)
                    if item:
                        if save_report_as_note(zot, item, report_content):
                            print(f"   ✓ Saved to Zotero (item {item['key']})")
                            stats["saved"] += 1
                            sessions_to_remove.append(key)
                        else:
                            stats["errors"] += 1
                    else:
                        print(f"   ⚠ Item not found in Zotero")
                        stats["not_found"] += 1
                else:
                    print(f"   ⚠ Completed but no output content")
                    stats["errors"] += 1
                    
            elif status in ["running", "pending", "in_progress"]:
                print(f"   ⏳ Still running ({status})")
                stats["still_running"] += 1
                
            elif status == "failed":
                error_msg = getattr(interaction, 'error', 'Unknown error')
                print(f"   ✗ Failed: {error_msg}")
                stats["failed"] += 1
                sessions_to_remove.append(key)  # Remove failed sessions
                
            else:
                print(f"   ? Unknown status: {status}")
                
        except Exception as e:
            print(f"   ✗ Error: {e}")
            stats["errors"] += 1
        
        print()
        time.sleep(0.5)  # Be nice to the API
    
    # Remove completed/failed sessions from state
    if sessions_to_remove:
        current_state = load_state()
        for key in sessions_to_remove:
            if key in current_state:
                del current_state[key]
        save_state(current_state)
        print(f"Removed {len(sessions_to_remove)} session(s) from state file.")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Sessions checked:     {stats['checked']}")
    print(f"Completed:            {stats['completed']}")
    print(f"Saved to Zotero:      {stats['saved']}")
    print(f"Still running:        {stats['still_running']}")
    print(f"Failed:               {stats['failed']}")
    print(f"Not found in Zotero:  {stats['not_found']}")
    print(f"Errors:               {stats['errors']}")
    print(f"{'='*60}")
    
    remaining = len(load_state())
    if remaining > 0:
        print(f"\n{remaining} session(s) still active. Run again later to check their status.")

if __name__ == "__main__":
    autosave_all_completed()
