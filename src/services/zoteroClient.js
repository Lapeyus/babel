import {
  LIBRARY_TYPE,
  LIBRARY_ID,
  API_KEY,
  PAGE_SIZE,

  ALLOWED_COLLECTIONS,
  WEBDAV_BASE_URL,
} from '../config.js';

const API_ROOT = 'https://api.zotero.org';
const MAX_PAGE_SIZE = 100;

const REQUIRED_MSG =
  'Set LIBRARY_ID (and optionally API_KEY) in src/config.js before loading the app.';

function ensureLibraryConfig() {
  if (!LIBRARY_TYPE || !LIBRARY_ID) {
    throw new Error(REQUIRED_MSG);
  }
}

function buildAuthHeaders() {
  const headers = {
    Accept: 'application/json',
  };
  if (API_KEY) {
    headers['Zotero-API-Key'] = API_KEY;
  }
  return headers;
}

function buildLibraryUrl(path) {
  ensureLibraryConfig();
  return `${API_ROOT}/${LIBRARY_TYPE}/${LIBRARY_ID}${path}`;
}

async function fetchJSONWithHeaders(url) {
  const response = await fetch(url, {
    headers: buildAuthHeaders(),
    mode: 'cors',
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Zotero API error ${response.status}: ${text}`);
  }

  const totalResultsHeader = response.headers.get('Total-Results');
  const totalResults = totalResultsHeader ? Number(totalResultsHeader) : null;
  const versionHeader = response.headers.get('Last-Modified-Version');
  const lastModifiedVersion = versionHeader ? Number(versionHeader) : null;
  const data = await response.json();
  return { data, totalResults, lastModifiedVersion };
}

async function fetchJSON(url) {
  const { data } = await fetchJSONWithHeaders(url);
  return data;
}

/**
 * Fetch every page of a listing endpoint (100 items per request, the API max)
 * until `Total-Results` is exhausted or an optional target count is reached.
 */
async function fetchAllPages(baseUrl, params = {}, targetCount = Number.MAX_SAFE_INTEGER) {
  const results = [];
  let start = 0;
  let totalResults = null;

  while (results.length < targetCount) {
    const requestLimit = Math.min(MAX_PAGE_SIZE, targetCount - results.length);
    const url = new URL(baseUrl);
    url.searchParams.set('format', 'json');
    url.searchParams.set('include', 'data');
    url.searchParams.set('limit', String(requestLimit));
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.set(key, value);
    });
    if (start > 0) {
      url.searchParams.set('start', String(start));
    }

    const { data, totalResults: reportedTotal } = await fetchJSONWithHeaders(
      url.toString()
    );

    const batch = Array.isArray(data) ? data : [];
    results.push(...batch);

    if (reportedTotal != null) {
      totalResults = reportedTotal;
    }

    if (!batch.length || batch.length < requestLimit) {
      break;
    }

    start += batch.length;

    if (totalResults != null && results.length >= totalResults) {
      break;
    }
  }

  return results.length > targetCount ? results.slice(0, targetCount) : results;
}

function normalizeItem(item) {
  return {
    key: item.key,
    title: item.data?.title ?? '',
    creators: item.data?.creators ?? [],
    collections: item.data?.collections ?? [],
    abstractNote: item.data?.abstractNote ?? '',
    tags: item.data?.tags ?? [],
    extra: item.data?.extra ?? '',
    year: item.data?.date ?? '',
    raw: item,
  };
}

async function fetchPagedItems(path, limit) {
  const targetCount = Number.isFinite(limit) ? limit : Number.MAX_SAFE_INTEGER;
  const raw = await fetchAllPages(
    buildLibraryUrl(path),
    { sort: 'title', direction: 'asc' },
    targetCount
  );

  return raw
    .filter(
      (item) =>
        item.data?.itemType !== 'attachment' && item.data?.itemType !== 'note'
    )
    .map(normalizeItem);
}

export async function fetchTopLevelItems(limit = PAGE_SIZE) {
  if (ALLOWED_COLLECTIONS && ALLOWED_COLLECTIONS.length > 0) {
    // Fetch items from all allowed collections in parallel
    const resultsArrays = await Promise.all(
      ALLOWED_COLLECTIONS.map((key) =>
        fetchPagedItems(`/collections/${key}/items/top`, limit)
      )
    );

    // Deduplicate items by key
    const uniqueItems = new Map();
    resultsArrays.flat().forEach((item) => {
      if (!uniqueItems.has(item.key)) {
        uniqueItems.set(item.key, item);
      }
    });

    return Array.from(uniqueItems.values());
  }

  return fetchPagedItems('/items/top', limit);
}

async function getCollectionItemCount(collectionKey) {
  const url = new URL(buildLibraryUrl(`/collections/${collectionKey}/items/top`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('limit', '1');

  try {
    const { totalResults } = await fetchJSONWithHeaders(url.toString());
    return totalResults ?? 0;
  } catch (error) {
    console.warn(`Failed to get item count for collection ${collectionKey}`, error);
    return 0;
  }
}

export async function fetchCollections() {
  if (ALLOWED_COLLECTIONS && ALLOWED_COLLECTIONS.length > 0) {
    try {
      // Fetch details for each allowed collection
      const collections = await Promise.all(
        ALLOWED_COLLECTIONS.map(async (key) => {
          try {
            const url = new URL(buildLibraryUrl(`/collections/${key}`));
            const data = await fetchJSON(url.toString());
            return {
              key: data.key,
              name: data.data?.name ?? 'Untitled',
            };
          } catch (err) {
            console.warn(`Failed to fetch collection ${key}`, err);
            return null;
          }
        })
      );
      return collections.filter(Boolean);
    } catch (error) {
      console.warn('Failed to load allowed collections', error);
      return [];
    }
  }

  const url = new URL(buildLibraryUrl('/collections/top'));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('limit', '200');
  url.searchParams.set('sort', 'title');

  const collections = await fetchJSON(url.toString());
  return collections.map((collection) => ({
    key: collection.key,
    name: collection.data?.name ?? 'Untitled',
  }));
}

export async function findFirstNonEmptyCollection(collections) {
  if (!collections.length) return null;

  // Check root collection first (collections[0])
  const rootCount = await getCollectionItemCount(collections[0].key);
  if (rootCount > 0) {
    return collections[0].key;
  }

  // If root is empty, find first non-empty sub-collection
  for (let i = 1; i < collections.length; i++) {
    const count = await getCollectionItemCount(collections[i].key);
    if (count > 0) {
      console.log(`Found non-empty collection: ${collections[i].name} (${count} items)`);
      return collections[i].key;
    }
  }

  // If all are empty, return root
  return collections[0]?.key ?? null;
}

function normalizeAttachment(attachment) {
  const attachmentKey = attachment.key;
  const fileName = attachment.data?.filename ?? '';
  const linkMode = attachment.data?.linkMode ?? '';

  // Build the resolved URL based on configuration
  let resolvedUrl = '';

  // If WebDAV is configured and this is a stored file (not a linked URL)
  if (WEBDAV_BASE_URL && linkMode !== 'linked_url') {
    // Zotero stores files on WebDAV as {key}.zip
    const webdavBase = WEBDAV_BASE_URL.endsWith('/')
      ? WEBDAV_BASE_URL
      : `${WEBDAV_BASE_URL}/`;
    resolvedUrl = `${webdavBase}${attachmentKey}.zip`;
  } else {
    // Fall back to Zotero API or linked URL
    resolvedUrl = appendKeyToUrl(
      attachment.links?.enclosure?.href ??
      (attachment.links?.self?.href
        ? `${attachment.links.self.href}/file`
        : '')
    ) || attachment.data?.url || '';
  }

  return {
    key: attachmentKey,
    parentItem: attachment.data?.parentItem ?? '',
    contentType: attachment.data?.contentType ?? '',
    fileName,
    title: attachment.data?.title ?? '',
    url: attachment.data?.url ?? '',
    linkMode,
    links: attachment.links ?? {},
    resolvedUrl,
  };
}

function normalizeNote(note) {
  return {
    key: note.key,
    title: note.data?.title ?? '',
    content: note.data?.note ?? '',
    dateModified: note.data?.dateModified ?? '',
  };
}

async function fetchAttachmentsForItem(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}/children`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemType', 'attachment');
  url.searchParams.set('limit', '50');

  const attachments = await fetchJSON(url.toString());
  return attachments.map(normalizeAttachment);
}

async function fetchNotesForItem(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}/children`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemType', 'note');
  url.searchParams.set('limit', '50');

  const notes = await fetchJSON(url.toString());
  return notes.map(normalizeNote);
}

/**
 * Fetch every attachment and note in the library in a single paginated sweep
 * (ceil(children / 100) requests) and group them by parent item key.
 * Replaces the previous approach of 2 requests per item.
 */
async function fetchLibraryChildren() {
  const raw = await fetchAllPages(buildLibraryUrl('/items'), {
    itemType: 'attachment || note',
  });

  const attachmentsByParent = new Map();
  const notesByParent = new Map();

  for (const child of raw) {
    const parentKey = child.data?.parentItem;
    if (!parentKey) continue;

    if (child.data?.itemType === 'attachment') {
      if (!attachmentsByParent.has(parentKey)) {
        attachmentsByParent.set(parentKey, []);
      }
      attachmentsByParent.get(parentKey).push(normalizeAttachment(child));
    } else if (child.data?.itemType === 'note') {
      if (!notesByParent.has(parentKey)) {
        notesByParent.set(parentKey, []);
      }
      notesByParent.get(parentKey).push(normalizeNote(child));
    }
  }

  return { attachmentsByParent, notesByParent };
}

function appendKeyToUrl(href) {
  if (!href) return href;
  try {
    const url = new URL(href);
    if (API_KEY && url.hostname.endsWith('zotero.org')) {
      url.searchParams.set('key', API_KEY);
      return url.toString();
    }
    return href;
  } catch (error) {
    return href;
  }
}

function isLikelyImageUrl(url) {
  if (!url) return false;
  try {
    const { pathname } = new URL(url, 'http://local.test');
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(pathname);
  } catch {
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(url);
  }
}

function scoreAttachmentAsCover(attachment) {
  const title = (attachment.title ?? '').trim();

  // Highest priority: specific "Book Cover (Web)" title
  if (title === 'Book Cover (Web)') return 100;

  let score = 0;
  if (attachment.contentType?.startsWith('image/')) score += 4;
  if (isLikelyImageUrl(attachment.url)) score += 3;
  if (attachment.fileName && isLikelyImageUrl(attachment.fileName)) score += 2;
  if (/cover|portada/i.test(`${title} ${attachment.fileName ?? ''}`)) score += 1;
  return score;
}

function chooseCoverUrl(attachments) {
  const ranked = attachments
    .map((attachment) => ({ attachment, score: scoreAttachmentAsCover(attachment) }))
    .sort((a, b) => b.score - a.score);

  for (const { attachment } of ranked) {
    const { links, url, title } = attachment;

    // If it's the specific Web cover, return its URL immediately
    if (title?.trim() === 'Book Cover (Web)') {
      return url || appendKeyToUrl(links?.enclosure?.href || (links?.self?.href ? `${links.self.href}/file` : ''));
    }

    const enclosure = links?.enclosure?.href ?? '';
    const selfFile = links?.self?.href ? `${links.self.href}/file` : '';

    if (attachment.contentType?.startsWith('image/')) {
      const target = enclosure || selfFile || url;
      if (target) return appendKeyToUrl(target);
    }

    if (isLikelyImageUrl(enclosure)) return appendKeyToUrl(enclosure);
    if (isLikelyImageUrl(selfFile)) return appendKeyToUrl(selfFile);
    if (isLikelyImageUrl(url)) return url;
    if (/cover|portada/i.test((attachment.title ?? '') + url)) {
      return url || appendKeyToUrl(enclosure || selfFile);
    }
  }

  return null;
}

function resolveAttachmentFileUrl(attachment) {
  if (!attachment) return null;
  return (
    attachment.resolvedUrl ||
    appendKeyToUrl(
      attachment.links?.enclosure?.href ||
      (attachment.links?.self?.href ? `${attachment.links.self.href}/file` : '')
    ) ||
    null
  );
}

function resolveEmbeddedAttachment(embeddedKey, attachments, noteKey, source) {
  const embedded = attachments.find((att) => att.key === embeddedKey);
  if (!embedded) {
    console.warn(`[ZoteroClient] ${source}: embedded attachment key not found`, {
      noteKey,
      embeddedKey,
      availableKeys: attachments.map((a) => a.key),
    });
    return null;
  }
  return resolveAttachmentFileUrl(embedded);
}

// Patterns for extracting a cover from a "Book Cover (b64)" note, in priority order.
const B64_SRC_PATTERNS = [
  // Base64 data URI in an img src (quoted, then unquoted)
  /src\s*=\s*["'](data:image\/[^"']+)["']/,
  /src\s*=\s*(data:image\/[^\s">]+)/,
];

const EMBEDDED_KEY_PATTERNS = [
  // Zotero 7 converts embedded images to attachment references
  /(?:data-attachment-key|zapi:key|key)\s*=\s*["']([A-Z0-9]{8})["']/i,
  // Last resort: any 8-char key inside a p tag that also carries width/height
  /<p[^>]*?["']([A-Z0-9]{8})["'][^>]*(?:width|height)\s*=/i,
];

function decodeHtmlEntities(content) {
  return content
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

/**
 * Extract cover image from notes.
 * Handles both:
 * 1. Direct base64 data URIs in img src
 * 2. Zotero 7's embedded attachment references (data-attachment-key)
 *
 * @param {Array} notes - Array of note objects with content
 * @param {Array} attachments - Array of attachment objects to search for embedded refs
 * @returns {string|null} - Data URI, attachment URL, or null
 */
function extractB64CoverFromNotes(notes, attachments = []) {
  // Look for a note with title/header "Book Cover (b64)"
  for (const note of notes) {
    if (!/Book Cover \(b64\)/i.test(note.content || '')) continue;

    // Decode HTML entities that might be present (e.g., &quot; -> ")
    const content = decodeHtmlEntities(note.content || '');

    for (const pattern of B64_SRC_PATTERNS) {
      const match = content.match(pattern);
      if (match?.[1]) {
        return match[1];
      }
    }

    for (const pattern of EMBEDDED_KEY_PATTERNS) {
      const match = content.match(pattern);
      if (match?.[1]) {
        const imageUrl = resolveEmbeddedAttachment(
          match[1],
          attachments,
          note.key,
          'note cover'
        );
        if (imageUrl) return imageUrl;
      }
    }

    console.warn('[ZoteroClient] Found "Book Cover (b64)" note but failed to extract image.', {
      noteKey: note.key,
      contentLength: content.length,
      contentPreview: content.slice(0, 500),
      hasDataImage: content.includes('data:image'),
      hasSrcAttr: content.includes('src='),
      hasKeyEquals: content.includes('key='),
    });
  }
  return null;
}

/**
 * Build an Open Library cover URL from the item's ISBN, as a last-resort
 * fallback when the Zotero item has no usable cover attachment or note.
 * No extra API request is made here; the browser simply loads the image
 * (and `default=false` makes missing covers 404 so the UI can fall back).
 */
function buildIsbnCoverUrl(item) {
  const isbnField = item.raw?.data?.ISBN ?? '';
  if (!isbnField) return null;

  const first = isbnField.split(/[,;\s]+/)[0]?.replace(/[^0-9Xx]/g, '') ?? '';
  if (first.length !== 10 && first.length !== 13) return null;

  return `https://covers.openlibrary.org/b/isbn/${first}-M.jpg?default=false`;
}

export async function attachCoverImages(items) {
  const { attachmentsByParent, notesByParent } = await fetchLibraryChildren();

  return items.map((item) => {
    const attachments = attachmentsByParent.get(item.key) ?? [];
    const notes = notesByParent.get(item.key) ?? [];

    // Prefer b64 cover from notes, then a cover-looking attachment,
    // then an Open Library cover derived from the ISBN.
    const b64Cover = extractB64CoverFromNotes(notes, attachments);
    const coverUrl =
      b64Cover || chooseCoverUrl(attachments) || buildIsbnCoverUrl(item);

    return {
      ...item,
      attachments,
      coverUrl,
      isB64Cover: !!b64Cover,
    };
  });
}

async function fetchLibraryVersion() {
  const url = new URL(buildLibraryUrl('/items'));
  url.searchParams.set('format', 'json');
  url.searchParams.set('limit', '1');
  const { lastModifiedVersion } = await fetchJSONWithHeaders(url.toString());
  return lastModifiedVersion;
}

const CACHE_KEY = [
  'babel-library-cache:v1',
  `${LIBRARY_TYPE}/${LIBRARY_ID}`,
  (ALLOWED_COLLECTIONS ?? []).join(','),
  String(PAGE_SIZE),
].join(':');

function readLibraryCache() {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.version !== 'number' || !Array.isArray(parsed.items)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeLibraryCache(payload) {
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch (error) {
    // Quota exceeded (b64 covers can be large) or storage unavailable —
    // drop the cache and carry on; the app just refetches next load.
    try {
      window.localStorage.removeItem(CACHE_KEY);
    } catch {
      /* ignore */
    }
    console.warn('[ZoteroClient] Could not persist library cache', error);
  }
}

/**
 * Load items (with covers) and collections, using a localStorage cache
 * validated against the library's Last-Modified-Version. When the library
 * has not changed since the last visit, this costs a single API request.
 */
export async function loadLibrary() {
  const cached = readLibraryCache();

  let currentVersion = null;
  try {
    currentVersion = await fetchLibraryVersion();
  } catch (error) {
    console.warn('[ZoteroClient] Could not fetch library version', error);
  }

  if (cached && currentVersion != null && cached.version === currentVersion) {
    return {
      items: cached.items,
      collections: cached.collections ?? [],
      fromCache: true,
    };
  }

  const [items, collections] = await Promise.all([
    fetchTopLevelItems(),
    fetchCollections().catch(() => []),
  ]);

  const itemsWithCovers = await attachCoverImages(items);

  if (currentVersion != null) {
    writeLibraryCache({
      version: currentVersion,
      items: itemsWithCovers,
      collections,
    });
  }

  return { items: itemsWithCovers, collections, fromCache: false };
}

function extractRelatedKeys(relations = {}) {
  const values = Object.values(relations);
  const keys = [];
  values.forEach((value) => {
    if (typeof value === 'string') {
      const key = value.match(/items\/([A-Z0-9]{8})/i)?.[1];
      if (key) keys.push(key);
    } else if (Array.isArray(value)) {
      value.forEach((entry) => {
        const key = entry.match(/items\/([A-Z0-9]{8})/i)?.[1];
        if (key) keys.push(key);
      });
    }
  });
  return [...new Set(keys)];
}

async function fetchItemsByKeys(keys = []) {
  if (!keys.length) return [];
  const url = new URL(buildLibraryUrl('/items'));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemKey', keys.join(','));

  const items = await fetchJSON(url.toString());
  return items.map((item) => ({
    key: item.key,
    title: item.data?.title ?? 'Untitled',
    creators: item.data?.creators ?? [],
    data: item.data,
  }));
}

export async function fetchItemDetails(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  return fetchJSON(url.toString());
}

export async function fetchItemBundle(itemKey) {
  const [item, attachments, notes] = await Promise.all([
    fetchItemDetails(itemKey),
    fetchAttachmentsForItem(itemKey),
    fetchNotesForItem(itemKey),
  ]);

  const relatedKeys = extractRelatedKeys(item.data?.relations ?? {});
  const relatedItems = await fetchItemsByKeys(relatedKeys);

  return {
    item,
    attachments,
    notes,
    relatedItems,
  };
}
