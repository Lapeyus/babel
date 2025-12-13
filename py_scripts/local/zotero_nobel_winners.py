"""Search and add Nobel Prize in Literature winners' books to Zotero collection."""

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

# Zotero Configuration
import os
from dotenv import load_dotenv

load_dotenv()

# Zotero Configuration
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")
LIBRARY_TYPE = os.getenv("LIBRARY_TYPE")
TARGET_COLLECTION_KEY = os.getenv("COLLECTION_KEY")

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "minimax-m2:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))


# Search Configuration
MAX_SEARCH_RESULTS = 5
DELAY_BETWEEN_SEARCHES = 5  # seconds


# Nobel Prize Winners Data
NOBEL_WINNERS = [
    {"year": 1901, "name": "Sully Prudhomme", "country": "Francia", "language": "Francés"},
    {"year": 1902, "name": "Theodor Mommsen", "country": "Imperio alemán", "language": "Alemán"},
    {"year": 1903, "name": "Bjørnstjerne Bjørnson", "country": "Noruega", "language": "Noruego"},
    {"year": 1904, "name": "Frédéric Mistral", "country": "Francia", "language": "Provenzal"},
    {"year": 1904, "name": "José Echegaray", "country": "España", "language": "Español"},
    {"year": 1905, "name": "Henryk Sienkiewicz", "country": "Polonia", "language": "Polaco"},
    {"year": 1906, "name": "Giosuè Carducci", "country": "Italia", "language": "Italiano"},
    {"year": 1907, "name": "Rudyard Kipling", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1908, "name": "Rudolf Christoph Eucken", "country": "Imperio alemán", "language": "Alemán"},
    {"year": 1909, "name": "Selma Lagerlöf", "country": "Suecia", "language": "Sueco"},
    {"year": 1910, "name": "Paul von Heyse", "country": "Imperio alemán", "language": "Alemán"},
    {"year": 1911, "name": "Maurice Maeterlinck", "country": "Bélgica", "language": "Francés"},
    {"year": 1912, "name": "Gerhart Hauptmann", "country": "Imperio alemán", "language": "Alemán"},
    {"year": 1913, "name": "Rabindranath Tagore", "country": "India", "language": "Bengalí e inglés"},
    {"year": 1915, "name": "Romain Rolland", "country": "Francia", "language": "Francés"},
    {"year": 1916, "name": "Verner von Heidenstam", "country": "Suecia", "language": "Sueco"},
    {"year": 1917, "name": "Karl Adolph Gjellerup", "country": "Dinamarca", "language": "Danés y alemán"},
    {"year": 1917, "name": "Henrik Pontoppidan", "country": "Dinamarca", "language": "Danés"},
    {"year": 1919, "name": "Carl Spitteler", "country": "Suiza", "language": "Alemán"},
    {"year": 1920, "name": "Knut Hamsun", "country": "Noruega", "language": "Noruego"},
    {"year": 1921, "name": "Anatole France", "country": "Francia", "language": "Francés"},
    {"year": 1922, "name": "Jacinto Benavente", "country": "España", "language": "Español"},
    {"year": 1923, "name": "William Butler Yeats", "country": "Irlanda", "language": "Inglés"},
    {"year": 1924, "name": "Władysław Reymont", "country": "Polonia", "language": "Polaco"},
    {"year": 1925, "name": "George Bernard Shaw", "country": "Irlanda", "language": "Inglés"},
    {"year": 1926, "name": "Grazia Deledda", "country": "Italia", "language": "Italiano"},
    {"year": 1927, "name": "Henri Bergson", "country": "Francia", "language": "Francés"},
    {"year": 1928, "name": "Sigrid Undset", "country": "Noruega", "language": "Noruego"},
    {"year": 1929, "name": "Thomas Mann", "country": "Alemania", "language": "Alemán"},
    {"year": 1930, "name": "Sinclair Lewis", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1931, "name": "Erik Axel Karlfeldt", "country": "Suecia", "language": "Sueco"},
    {"year": 1932, "name": "John Galsworthy", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1933, "name": "Iván Bunin", "country": "Francia/Rusia", "language": "Ruso"},
    {"year": 1934, "name": "Luigi Pirandello", "country": "Italia", "language": "Italiano"},
    {"year": 1936, "name": "Eugene O'Neill", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1937, "name": "Roger Martin du Gard", "country": "Francia", "language": "Francés"},
    {"year": 1938, "name": "Pearl S. Buck", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1939, "name": "Frans Eemil Sillanpää", "country": "Finlandia", "language": "Finés"},
    {"year": 1944, "name": "Johannes Vilhelm Jensen", "country": "Dinamarca", "language": "Danés"},
    {"year": 1945, "name": "Gabriela Mistral", "country": "Chile", "language": "Español"},
    {"year": 1946, "name": "Hermann Hesse", "country": "Suiza/Alemania", "language": "Alemán"},
    {"year": 1947, "name": "André Gide", "country": "Francia", "language": "Francés"},
    {"year": 1948, "name": "T. S. Eliot", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1949, "name": "William Faulkner", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1950, "name": "Bertrand Russell", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1951, "name": "Pär Lagerkvist", "country": "Suecia", "language": "Sueco"},
    {"year": 1952, "name": "François Mauriac", "country": "Francia", "language": "Francés"},
    {"year": 1953, "name": "Winston Churchill", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1954, "name": "Ernest Hemingway", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1955, "name": "Halldór Laxness", "country": "Islandia", "language": "Islandés"},
    {"year": 1956, "name": "Juan Ramón Jiménez", "country": "España", "language": "Español"},
    {"year": 1957, "name": "Albert Camus", "country": "Francia", "language": "Francés"},
    {"year": 1958, "name": "Borís Pasternak", "country": "Unión Soviética", "language": "Ruso"},
    {"year": 1959, "name": "Salvatore Quasimodo", "country": "Italia", "language": "Italiano"},
    {"year": 1960, "name": "Saint-John Perse", "country": "Francia", "language": "Francés"},
    {"year": 1961, "name": "Ivo Andrić", "country": "Yugoslavia", "language": "Serbocroata"},
    {"year": 1962, "name": "John Steinbeck", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1963, "name": "Yorgos Seferis", "country": "Grecia", "language": "Griego"},
    {"year": 1964, "name": "Jean-Paul Sartre", "country": "Francia", "language": "Francés"},
    {"year": 1965, "name": "Mijaíl Shólojov", "country": "Unión Soviética", "language": "Ruso"},
    {"year": 1966, "name": "Shmuel Yosef Agnón", "country": "Israel", "language": "Hebreo"},
    {"year": 1966, "name": "Nelly Sachs", "country": "Suecia/Alemania", "language": "Alemán"},
    {"year": 1967, "name": "Miguel Ángel Asturias", "country": "Guatemala", "language": "Español"},
    {"year": 1968, "name": "Yasunari Kawabata", "country": "Japón", "language": "Japonés"},
    {"year": 1969, "name": "Samuel Beckett", "country": "Irlanda", "language": "Francés e inglés"},
    {"year": 1970, "name": "Aleksandr Solzhenitsyn", "country": "Unión Soviética", "language": "Ruso"},
    {"year": 1971, "name": "Pablo Neruda", "country": "Chile", "language": "Español"},
    {"year": 1972, "name": "Heinrich Böll", "country": "Alemania Occidental", "language": "Alemán"},
    {"year": 1973, "name": "Patrick White", "country": "Australia", "language": "Inglés"},
    {"year": 1974, "name": "Eyvind Johnson", "country": "Suecia", "language": "Sueco"},
    {"year": 1974, "name": "Harry Martinson", "country": "Suecia", "language": "Sueco"},
    {"year": 1975, "name": "Eugenio Montale", "country": "Italia", "language": "Italiano"},
    {"year": 1976, "name": "Saul Bellow", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1977, "name": "Vicente Aleixandre", "country": "España", "language": "Español"},
    {"year": 1978, "name": "Isaac Bashevis Singer", "country": "Estados Unidos/Polonia", "language": "Yidis"},
    {"year": 1979, "name": "Odysseas Elytis", "country": "Grecia", "language": "Griego"},
    {"year": 1980, "name": "Czesław Miłosz", "country": "Polonia", "language": "Polaco"},
    {"year": 1981, "name": "Elias Canetti", "country": "Reino Unido/Bulgaria", "language": "Alemán"},
    {"year": 1982, "name": "Gabriel García Márquez", "country": "Colombia", "language": "Español"},
    {"year": 1983, "name": "William Golding", "country": "Reino Unido", "language": "Inglés"},
    {"year": 1984, "name": "Jaroslav Seifert", "country": "Checoslovaquia", "language": "Checo"},
    {"year": 1985, "name": "Claude Simon", "country": "Francia", "language": "Francés"},
    {"year": 1986, "name": "Wole Soyinka", "country": "Nigeria", "language": "Inglés"},
    {"year": 1987, "name": "Joseph Brodsky", "country": "Estados Unidos/URSS", "language": "Ruso"},
    {"year": 1988, "name": "Naguib Mahfuz", "country": "Egipto", "language": "Árabe"},
    {"year": 1989, "name": "Camilo José Cela", "country": "España", "language": "Español"},
    {"year": 1990, "name": "Octavio Paz", "country": "México", "language": "Español"},
    {"year": 1991, "name": "Nadine Gordimer", "country": "Sudáfrica", "language": "Inglés"},
    {"year": 1992, "name": "Derek Walcott", "country": "Santa Lucía", "language": "Inglés"},
    {"year": 1993, "name": "Toni Morrison", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 1994, "name": "Kenzaburō Ōe", "country": "Japón", "language": "Japonés"},
    {"year": 1995, "name": "Seamus Heaney", "country": "Irlanda", "language": "Inglés"},
    {"year": 1996, "name": "Wisława Szymborska", "country": "Polonia", "language": "Polaco"},
    {"year": 1997, "name": "Dario Fo", "country": "Italia", "language": "Italiano"},
    {"year": 1998, "name": "José Saramago", "country": "Portugal", "language": "Portugués"},
    {"year": 1999, "name": "Günter Grass", "country": "Alemania", "language": "Alemán"},
    {"year": 2000, "name": "Gao Xingjian", "country": "Francia/China", "language": "Chino"},
    {"year": 2001, "name": "V. S. Naipaul", "country": "Reino Unido/Trinidad", "language": "Inglés"},
    {"year": 2002, "name": "Imre Kertész", "country": "Hungría", "language": "Húngaro"},
    {"year": 2003, "name": "J. M. Coetzee", "country": "Sudáfrica", "language": "Inglés"},
    {"year": 2004, "name": "Elfriede Jelinek", "country": "Austria", "language": "Alemán"},
    {"year": 2005, "name": "Harold Pinter", "country": "Reino Unido", "language": "Inglés"},
    {"year": 2006, "name": "Orhan Pamuk", "country": "Turquía", "language": "Turco"},
    {"year": 2007, "name": "Doris Lessing", "country": "Reino Unido", "language": "Inglés"},
    {"year": 2008, "name": "Jean-Marie Gustave Le Clézio", "country": "Francia", "language": "Francés"},
    {"year": 2009, "name": "Herta Müller", "country": "Alemania/Rumanía", "language": "Alemán"},
    {"year": 2010, "name": "Mario Vargas Llosa", "country": "Perú", "language": "Español"},
    {"year": 2011, "name": "Tomas Tranströmer", "country": "Suecia", "language": "Sueco"},
    {"year": 2012, "name": "Mo Yan", "country": "China", "language": "Chino"},
    {"year": 2013, "name": "Alice Munro", "country": "Canadá", "language": "Inglés"},
    {"year": 2014, "name": "Patrick Modiano", "country": "Francia", "language": "Francés"},
    {"year": 2015, "name": "Svetlana Aleksiévich", "country": "Bielorrusia", "language": "Ruso"},
    {"year": 2016, "name": "Bob Dylan", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 2017, "name": "Kazuo Ishiguro", "country": "Reino Unido/Japón", "language": "Inglés"},
    {"year": 2018, "name": "Olga Tokarczuk", "country": "Polonia", "language": "Polaco"},
    {"year": 2019, "name": "Peter Handke", "country": "Austria", "language": "Alemán"},
    {"year": 2020, "name": "Louise Glück", "country": "Estados Unidos", "language": "Inglés"},
    {"year": 2021, "name": "Abdulrazak Gurnah", "country": "Tanzania", "language": "Inglés"},
    {"year": 2022, "name": "Annie Ernaux", "country": "Francia", "language": "Francés"},
    {"year": 2023, "name": "Jon Fosse", "country": "Noruega", "language": "Noruego"},
    {"year": 2024, "name": "Han Kang", "country": "Corea del Sur", "language": "Coreano"},
    {"year": 2025, "name": "László Krasznahorkai", "country": "Hungría", "language": "Húngaro"},
]


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

def search_oceanofpdf(author_name):
    """Search OceanOfPDF for books by the author, prioritizing Spanish."""
    
    # 1. Try Spanish first
    query_es = f'site:oceanofpdf.com "{author_name}" español'
    print(f"  Searching OceanOfPDF (via DDG) for {author_name} (Spanish)...")
    results = _perform_search(query_es)
    
    if results:
        return results
        
    # 2. Fallback to general search (English/Any)
    print(f"  ⚠ No Spanish results. Searching OceanOfPDF for {author_name} (General)...")
    query_gen = f'site:oceanofpdf.com "{author_name}"'
    results = _perform_search(query_gen)
    
    return results


def get_famous_book_fallback(author_name, year):
    """Ask Ollama for the most famous book when no search results found."""
    prompt = (
        f"You are a literary expert. Identify the MOST FAMOUS or NOBEL PRIZE-WINNING book "
        f"by {author_name} (Nobel Prize {year}).\n"
        f"Respond with ONLY valid JSON:\n"
        f"{{\n"
        f'  "title": "Book Title",\n'
        f'  "year": YYYY,\n'
        f'  "isbn": "ISBN if available or null"\n'
        f"}}\n"
    )
    
    return _call_ollama(prompt)


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


def extract_book_info_with_ollama(author_name, year, search_results):
    """Use Ollama to extract book information from search results."""
    if not search_results:
        return None
    
    context = "\n".join(
        f"- Title: {r['title']}\n  Snippet: {r['snippet']}\n  Link: {r['link']}"
        for r in search_results[:5]
    )
    
    prompt = (
        f"You are a literary expert. The following are books found on OceanOfPDF for {author_name} (Nobel Prize {year}). "
        f"Select the most famous or representative work from this list. "
        f"If no famous work is obvious, select the most relevant book available.\n\n"
        f"Based on the context below, respond with ONLY valid JSON:\n"
        f"{{\n"
        f'  "title": "Book Title",\n'
        f'  "year": YYYY,\n'
        f'  "isbn": "ISBN if available or null",\n'
        f'  "link": "URL from context or null",\n'
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


def add_book_to_zotero(zot, collection_key, book_info, author_name, nobel_year):
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
    
    # Add note about Nobel Prize
    template["extra"] = f"Nobel Prize in Literature: {nobel_year}"
    template["tags"] = [
        {"tag": "Nobel Prize in Literature"},
        {"tag": f"Nobel {nobel_year}"}
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


def process_nobel_winners(zot):
    """Process all Nobel Prize winners and add their books."""
    print(f"\n{'='*70}")
    print(f"Nobel Prize in Literature - Book Collection Builder")
    print(f"Target Collection: {TARGET_COLLECTION_KEY}")
    print(f"Total Winners: {len(NOBEL_WINNERS)}")
    print(f"{'='*70}\n")
    
    added_count = 0
    skipped_count = 0
    failed_count = 0
    
    progress = tqdm(NOBEL_WINNERS, desc="Processing winners", unit="winner")
    
    for winner in progress:
        year = winner["year"]
        name = winner["name"]
        
        print(f"\n🏆 {year} - {name}")
        
        if hasattr(progress, "set_postfix_str"):
            progress.set_postfix_str(f"{name[:30]}")
        
        # Search for the book
        search_results = search_oceanofpdf(name)
        
        if search_results:
            book_info = extract_book_info_with_ollama(name, year, search_results)
        else:
            print(f"  ⚠ No search results found. Using fallback...")
            book_info = get_famous_book_fallback(name, year)
        
        if not book_info or not book_info.get("title"):
            print(f"  ⚠ Could not identify book")
            failed_count += 1
            time.sleep(DELAY_BETWEEN_SEARCHES)
            continue
        
        print(f"  📖 Found: {book_info['title']}")
        if book_info.get("year"):
            print(f"     Published: {book_info['year']}")
        
        # Add to Zotero
        if add_book_to_zotero(zot, TARGET_COLLECTION_KEY, book_info, name, year):
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
    print(f"  Total:     {len(NOBEL_WINNERS)}")
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
    
    process_nobel_winners(zot)
    print("✓ Processing complete!")


if __name__ == "__main__":
    main()
