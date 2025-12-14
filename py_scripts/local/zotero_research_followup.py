"""
Interactive follow-up script for Zotero Deep Research.
Allows asking follow-up questions for items with active research sessions.
"""

import os
import json
import sys
import time
from pyzotero import zotero
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE", "").strip() or "user"
STATE_FILE = "research_state.json"
FOLLOW_UP_MODEL = "gemini-2.0-flash-lite" 

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    print("Warning: GEMINI_API_KEY is not set.")
    sys.exit(1)

def get_zotero_client():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        print("Warning: ZOTERO_API_KEY and ZOTERO_USER_ID must be set to save reports.")
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

def list_active_sessions():
    """List items with active/resumable research sessions."""
    state = load_state()
    if not state:
        print("No active research sessions found in research_state.json")
        return []
    
    sessions = []
    print("\nActive Research Sessions:")
    for idx, (key, interaction_id) in enumerate(state.items(), 1):
        try:
            title, author = key.rsplit('_', 1)
            display_name = f"{title} by {author}"
        except ValueError:
            display_name = key
            title = key
            author = ""
            
        print(f"{idx}. {display_name}")
        sessions.append((key, interaction_id, title, author))
        
    return sessions

def find_item_in_zotero(zot, title, author):
    """Find a Zotero item by title (and ideally author)."""
    # Search by title first
    items = zot.items(q=title)
    
    # Filter by item type book if possible, and fuzzy check author
    candidates = []
    for item in items:
        data = item.get('data', {})
        if data.get('itemType') not in ['book', 'document']:
            continue
            
        # Check author if provided
        if author:
            creators = data.get('creators', [])
            found_author = False
            for c in creators:
                name = c.get('name', '') or f"{c.get('firstName', '')} {c.get('lastName', '')}"
                if author.lower() in name.lower():
                    found_author = True
                    break
            if not found_author:
                continue # Skip if author substantially differs
        
        candidates.append(item)
    
    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        print(f"    ⚠ Found multiple Zotero items for '{title}'. Using the first one.")
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
        print(f"    ✓ Saved report as note for item {item_key}.")
        return True
    except Exception as e:
        print(f"    ✗ Failed to save note for item {item_key}: {e}")
        return False

def interactive_follow_up():
    zot = get_zotero_client()
    
    while True:
        sessions = list_active_sessions()
        if not sessions:
            break
            
        try:
            choice = input("\nSelect a session number (or 'q' to quit): ").strip()
            if choice.lower() == 'q':
                break
                
            idx = int(choice) - 1
            if idx < 0 or idx >= len(sessions):
                print("Invalid selection.")
                continue
                
            key, interaction_id, title, author = sessions[idx]
            print(f"\n--- Checking Status for: {key} ---")
            
            try:
                interaction = client.interactions.get(id=interaction_id)
                status = interaction.status
                print(f"Status: {status}")
                
                report_content = ""
                if interaction.outputs:
                    report_content = interaction.outputs[-1].text
                
                # If completed and has content, offer to save
                if status == "succeeded" or (status == "completed" and report_content):
                     if report_content:
                        print(f"\nReport generated ({len(report_content)} chars).")
                        
                        save_choice = input("Do you want to save this report to Zotero? (y/n): ").lower().strip()
                        if save_choice == 'y':
                            if zot:
                                item = find_item_in_zotero(zot, title, author)
                                if item:
                                    if save_report_as_note(zot, item, report_content):
                                        # Offer to clear state
                                        clear_choice = input("Do you want to clear this session from the active list? (y/n): ").lower().strip()
                                        if clear_choice == 'y':
                                            state = load_state()
                                            if key in state:
                                                del state[key]
                                                save_state(state)
                                            print("Session cleared.")
                                            continue # Go back to main menu
                                else:
                                    print(f"    ✗ Could not find corresponding item in Zotero for '{title}'.")
                            else:
                                print("    ✗ Zotero client not configured.")
                     else:
                        print("    ⚠ Interaction succeeded but no output text found.")

                elif status == 'failed':
                    print(f"    ✗ Interaction failed: {interaction.error}")

                # Follow-up Chat Option
                chat_choice = input("\nDo you want to ask follow-up questions? (y/n): ").lower().strip()
                if chat_choice == 'y':
                    while True:
                        user_query = input("\nEnter your follow-up question (or 'back' to menu): ").strip()
                        if user_query.lower() == 'back':
                            break
                        if not user_query:
                            continue

                        print("    Thinking...")
                        try:
                            # Using previous_interaction_id ensures context from the research
                            follow_up = client.interactions.create(
                                input=user_query,
                                model=FOLLOW_UP_MODEL, 
                                previous_interaction_id=interaction_id
                            )
                            if follow_up.outputs:
                                print(f"\nResponse:\n{follow_up.outputs[-1].text}\n")
                        except Exception as e:
                            print(f"    ✗ Error: {e}")

            except Exception as e:
                print(f"    ✗ Error fetching interaction: {e}")

        except ValueError:
            print("Invalid input.")
        except KeyboardInterrupt:
            print("\nExiting.")
            break

if __name__ == "__main__":
    interactive_follow_up()
