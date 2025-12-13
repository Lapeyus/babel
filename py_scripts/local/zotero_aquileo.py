"""Search and add Aquileo J. Echeverría Prize winners' books to Zotero collection."""

import json
import re
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
    class _TqdmFallback:
        def __init__(self, iterable, **kwargs):
            self._iterable = iterable

        def __iter__(self):
            for item in self._iterable:
                yield item

        def set_postfix_str(self, *_args, **_kwargs):
            return None

    def tqdm(iterable, **kwargs):
        return _TqdmFallback(iterable, **kwargs)

import os
from dotenv import load_dotenv

load_dotenv()

# Zotero Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
TARGET_COLLECTION_KEY = os.getenv("COLLECTION_KEY")

# Search Configuration
MAX_SEARCH_RESULTS = 5
DELAY_BETWEEN_SEARCHES = 5  # seconds

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "minimax-m2:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

# Raw Data
DATA_NOVELA = """
"""

DATA_CUENTO = """
1962	Desierto
1963	Samuel Rovinski	La hora de los vencidos
1964	José Basileo Acuña	Tres cantares (El ángel que se quedó perdido)
1965	Alberto Cañas
1967	José León Sánchez
1968	Joaquín Gutiérrez
1970	Julieta Pinto
1972	Abel Pacheco
1974	Carlos Luis Sáenz Elizondo
1975	Alfonso Chase
1980	Alberto Cañas
1981	Fernando Durán Ayanegui
1982	Francisco Escobar Abarca
1983	Rodrigo Soto González
1986	Fernando Durán Ayanegui
1988	Fernando Durán Ayanegui
1989	Fernando Durán Ayanegui
1990	Uriel Quesada	El atardecer de los niños
1993	Tatiana Lobo
1994	Carlos Luis Altamirano Vargas
1996	Delfina Collado Aguilar
1997	José Ricardo Chaves
1998	Eduardo Vargas Ugalde
1999	Myriam Bustos Arratia
2000	Ernesto Rivera Casasola
2001	Jacques Sagot Martino
2002	Lara Ríos
2002	Eduardo Vargas Ugalde
2003	Enrique Castillo Barrantes
2004	Myriam Bustos Arratia
2005	Vernor Muñoz Villalobos
2006	Rodrigo Soto González
2007	Sonia Morales Solarte
2010	Carlos Cortés Zúñiga
2010	Rodolfo Arias Formoso
2011	Faustino Desinach Cordero
2011	Virgilio Mora Rodríguez
2012	Carla Pravisani
2013	Guillermo Fernández Álvarez
2014	Karla Sterloff Umaña
2015	Diego Van der Laat Alfaro
2016	Arabella Salaverry
2017	Guillermo Barquero Ureña
2018	Cristopher Montero Corrales
2018	Uriel Quesada	La invención y el olvido
2019	Camila Schumacher
2020	Cristopher Reyes Loaiciga
2021	Ana Lucía Fonseca
2022	Larissa Rú	Monstruos bajo la lluvia[5]
2023	Laura Zúñiga Hernández	Anatomía de la casa
2024	Carlos Regueyra Bonilla	Yeso
"""

def clean_text(text):
    """Remove citation brackets and whitespace."""
    if not text: return None
    return re.sub(r'\[\d+\]', '', text).strip()

def parse_data(raw_data, category):
    """Parse raw tab-separated data."""
    winners = []
    for line in raw_data.strip().split('\n'):
        parts = line.split('\t')
        if len(parts) < 2: continue
        
        year = clean_text(parts[0])
        name = clean_text(parts[1])
        
        if name.lower() == "desierto": continue
        
        title = None
        if len(parts) > 2:
            title = clean_text(parts[2])
            
        winners.append({
            "year": year,
            "name": name,
            "title": title,
            "category": category
        })
    return winners

def get_aquileo_winners():
    return parse_data(DATA_CUENTO, "Cuento")


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


def _perform_search(query):
    """Helper to perform DDG search."""
    results = []
    try:
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=5):
                title = result.get("title", "")
                body = result.get("body", "")
                link = result.get("href", "")
                # Clean title
                title = title.replace("OceanofPDF.com", "").replace("–", "").strip()
                results.append({
                    "title": title,
                    "snippet": body,
                    "link": link
                })
    except Exception as error:
        print(f"  ⚠ Search failed: {error}")
    return results


def search_book_web(author_name, target_title=None, year=None):
    """Search for books by the author, using general web search and OceanOfPDF."""
    
    queries = []
    if target_title:
        # If we have a specific title, prioritize it
        queries.append((f'"{target_title}" "{author_name}" book', "Specific Title (General)"))
        queries.append((f'site:oceanofpdf.com "{target_title}" "{author_name}"', "Specific Title (OceanOfPDF)"))
    else:
        # If no title, search for author and year
        try:
            year_int = int(year)
            prev_year = year_int - 1
            queries.append((f'"{author_name}" book {year}', f"Author + {year}"))
            queries.append((f'"{author_name}" book {prev_year}', f"Author + {prev_year}"))
            queries.append((f'site:oceanofpdf.com "{author_name}" {year}', f"Author + {year} (OceanOfPDF)"))
        except:
            pass
            
        queries.append((f'"{author_name}" book', "Author (General)"))
        queries.append((f'site:oceanofpdf.com "{author_name}"', "Author (OceanOfPDF)"))
    
    for query, label in queries:
        print(f"  Searching (via DDG) for {label}...")
        results = _perform_search(query)
        if results:
            return results
            
    return []


def get_book_fallback(author_name, year, target_title=None):
    """Ask Ollama for book info when no search results found."""
    if target_title:
        prompt = (
            f"You are a literary expert. Provide metadata for the book '{target_title}' "
            f"by {author_name} (Aquileo J. Echeverría Prize {year}).\n"
            f"Respond with ONLY valid JSON:\n"
            f"{{\n"
            f'  "title": "{target_title}",\n'
            f'  "year": YYYY,\n'
            f'  "isbn": "ISBN if available or null"\n'
            f"}}\n"
        )
    else:
        prompt = (
            f"You are a literary expert. Identify the MOST FAMOUS book "
            f"by {author_name} (Aquileo J. Echeverría Prize {year}).\n"
            f"Respond with ONLY valid JSON:\n"
            f"{{\n"
            f'  "title": "Book Title",\n'
            f'  "year": YYYY,\n'
            f'  "isbn": "ISBN if available or null"\n'
            f"}}\n"
        )
    
    return _call_ollama(prompt)


def extract_book_info_with_ollama(author_name, year, search_results, target_title=None):
    """Use Ollama to extract book information from search results."""
    if not search_results:
        return None
    
    context = "\n".join(
        f"- Title: {r['title']}\n  Snippet: {r['snippet']}\n  Link: {r['link']}"
        for r in search_results[:5]
    )
    
    target_instruction = ""
    if target_title:
        target_instruction = f"Look specifically for the book '{target_title}'. "
    else:
        try:
            year_int = int(year)
            target_instruction = f"Identify a short story collection (Cuento) by {author_name} published in {year} or {year_int-1}. "
        except:
            target_instruction = f"Identify a short story collection (Cuento) by {author_name}. "
    
    prompt = (
        f"You are a literary expert. The following are books found on OceanOfPDF for {author_name} (Aquileo J. Echeverría Prize {year}). "
        f"{target_instruction}"
        f"Select the most relevant book from this list. "
        f"If the specific target book is not found, select the most famous available book.\n\n"
        f"Based on the context below, respond with ONLY valid JSON:\n"
        f"{{\n"
        f'  "title": "Book Title",\n'
        f'  "year": YYYY,\n'
        f'  "isbn": "ISBN if available or null",\n'
        f'  "publisher": "Publisher",\n'
        f'  "place": "Place",\n'
        f'  "numPages": "Pages",\n'
        f'  "language": "Language",\n'
        f'  "abstractNote": "Summary",\n'
        f'  "url": "URL to book page (Goodreads, Amazon, etc.)",\n'
        f'  "confidence": "high/medium/low"\n'
        f"}}\n\n"
        f"Context:\n{context}\n\n"
        f"JSON Response:"
    )
    
    return _call_ollama(prompt)


def check_if_book_exists_in_collection(zot, collection_key, title, author):
    """Check if a book with similar title/author already exists in the collection."""
    try:
        items = zot.collection_items(collection_key)
        
        # Normalize for comparison
        title_lower = title.lower()
        author_lower = author.lower()
        
        for item in items:
            data = item.get("data", {})
            if data.get("itemType") != "book":
                continue
            
            item_title = (data.get("title") or "").lower()
            creators = data.get("creators", [])
            
            # Check title similarity
            if title_lower in item_title or item_title in title_lower:
                # Check author
                for creator in creators:
                    creator_name = ""
                    if creator.get("name"):
                        creator_name = creator["name"].lower()
                    else:
                        parts = [creator.get("firstName", ""), creator.get("lastName", "")]
                        creator_name = " ".join(p for p in parts if p).lower()
                    
                    if author_lower in creator_name or creator_name in author_lower:
                        return True
        
        return False
    except Exception as error:
        print(f"  ⚠ Error checking collection: {error}")
        return False


def add_book_to_zotero(zot, collection_key, book_info, author_name, year, category):
    """Add a book to Zotero collection."""
    # Parse author name
    name_parts = author_name.split()
    last_name = name_parts[-1] if name_parts else author_name
    first_name = " ".join(name_parts[:-1]) if len(name_parts) > 1 else ""
    
    # Check if already exists
    if check_if_book_exists_in_collection(zot, collection_key, book_info["title"], author_name):
        print(f"  ⊘ Book already exists in collection: {book_info['title']}")
        return False
    
    # Create book item
    template = zot.item_template("book")
    template["title"] = book_info["title"]
    template["creators"] = [{
        "creatorType": "author",
        "firstName": first_name,
        "lastName": last_name
    }]
    
    if book_info.get("year"):
        template["date"] = str(book_info["year"])
    
    if book_info.get("isbn"):
        template["ISBN"] = book_info["isbn"]
        
    template["publisher"] = book_info.get("publisher", "")
    template["place"] = book_info.get("place", "")
    template["numPages"] = book_info.get("numPages", "")
    template["language"] = book_info.get("language", "")
    template["abstractNote"] = book_info.get("abstractNote", "")
    template["url"] = book_info.get("url", "")
    
    # Add note about Prize
    template["extra"] = f"Premio Aquileo J. Echeverría: {category} ({year})"
    template["tags"] = [
        {"tag": "Premio Aquileo J. Echeverría"},
        {"tag": f"Aquileo {year}"},
        {"tag": category}
    ]
    
    try:
        # Create the item
        created_item = zot.create_items([template])
        
        if created_item and len(created_item["successful"]) > 0:
            item_key = created_item["successful"]["0"]["key"]
            
            # Add to collection
            zot.addto_collection(collection_key, created_item["successful"]["0"])
            
            # Add link attachment if available
            link = book_info.get("link")
            if link:
                try:
                    # Use linkMode (API parameter name)
                    att_template = zot.item_template('attachment', linkMode='linked_url')
                    
                    att_template['url'] = link
                    att_template['title'] = "OceanOfPDF Download"
                    att_template['parentItem'] = item_key
                    
                    zot.create_items([att_template])
                    print(f"     Attached link: {link}")
                except Exception as e:
                    print(f"     ⚠ Failed to attach link: {e}")
            
            print(f"  ✓ Added: {book_info['title']} (Key: {item_key})")
            return True
        else:
            print(f"  ✗ Failed to create item")
            return False
            
    except Exception as error:
        print(f"  ✗ Error adding to Zotero: {error}")
        return False


def process_winners(zot):
    """Process all winners and add their books."""
    winners = get_aquileo_winners()
    
    print(f"\n{'='*70}")
    print(f"Premio Aquileo J. Echeverría - Book Collection Builder")
    print(f"Target Collection: {TARGET_COLLECTION_KEY}")
    print(f"Total Winners: {len(winners)}")
    print(f"{'='*70}\n")
    
    added_count = 0
    skipped_count = 0
    failed_count = 0
    
    progress = tqdm(winners, desc="Processing winners", unit="winner")
    
    for winner in progress:
        year = winner["year"]
        name = winner["name"]
        title = winner["title"]
        category = winner["category"]
        
        display_title = f" - {title}" if title else ""
        print(f"\n🏆 {year} ({category}) - {name}{display_title}")
        
        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(f"{name[:30]}")
        
        # Search for the book
        search_results = search_book_web(name, target_title=title, year=year)
        
        if search_results:
            book_info = extract_book_info_with_ollama(name, year, search_results, target_title=title)
        else:
            print(f"  ⚠ No search results found. Using fallback...")
            book_info = get_book_fallback(name, year, target_title=title)
        
        if not book_info or not book_info.get("title"):
            print(f"  ⚠ Could not identify book")
            failed_count += 1
            time.sleep(DELAY_BETWEEN_SEARCHES)
            continue
        
        print(f"  📖 Found: {book_info['title']}")
        if book_info.get("year"):
            print(f"     Published: {book_info['year']}")
        
        # Add to Zotero
        if add_book_to_zotero(zot, TARGET_COLLECTION_KEY, book_info, name, year, category):
            added_count += 1
        else:
            if check_if_book_exists_in_collection(zot, TARGET_COLLECTION_KEY, book_info["title"], name):
                skipped_count += 1
            else:
                failed_count += 1
        
        # Rate limiting
        time.sleep(DELAY_BETWEEN_SEARCHES)
    
    print(f"\n{'='*70}")
    print(f"Summary:")
    print(f"  ✓ Added:   {added_count}")
    print(f"  ⊘ Skipped: {skipped_count}")
    print(f"  ✗ Failed:  {failed_count}")
    print(f"  Total:     {len(winners)}")
    print(f"{'='*70}\n")


def main():
    """Main entry point."""
    print("Connecting to Zotero...")
    zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)
    
    # Verify collection exists
    try:
        collection = zot.collection(TARGET_COLLECTION_KEY)
        print(f"✓ Target collection: {collection['data']['name']}\n")
    except Exception as error:
        print(f"✗ Error: Cannot access collection {TARGET_COLLECTION_KEY}")
        print(f"  {error}")
        return
    
    process_winners(zot)
    print("✓ Processing complete!")


if __name__ == "__main__":
    main()
