import {
  LIBRARY_TYPE,
  LIBRARY_ID,
  API_KEY,
  PAGE_SIZE,
  ATTACHMENT_CONCURRENCY,
  COLLECTION_KEY,
  WEBDAV_BASE_URL,
} from '../config.js';

const API_ROOT = 'https://api.zotero.org';

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
  const data = await response.json();
  return { data, totalResults };
}

async function fetchJSON(url) {
  const { data } = await fetchJSONWithHeaders(url);
  return data;
}

async function fetchPagedItems(path, limit) {
  const baseUrl = buildLibraryUrl(path);
  const results = [];
  const targetCount = Number.isFinite(limit) ? limit : Number.MAX_SAFE_INTEGER;
  const pageSize = Math.min(100, targetCount);

  let start = 0;
  let totalResults = null;

  while (results.length < targetCount) {
    const requestLimit = Math.min(pageSize, targetCount - results.length);
    const url = new URL(baseUrl);
    url.searchParams.set('format', 'json');
    url.searchParams.set('include', 'data');
    url.searchParams.set('limit', String(requestLimit));
    url.searchParams.set('sort', 'title');
    url.searchParams.set('direction', 'asc');
    if (start > 0) {
      url.searchParams.set('start', String(start));
    }

    const { data, totalResults: reportedTotal } = await fetchJSONWithHeaders(
      url.toString()
    );

    const normalized = data
      .filter(
        (item) =>
          item.data?.itemType !== 'attachment' && item.data?.itemType !== 'note'
      )
      .map((item) => ({
        key: item.key,
        title: item.data?.title ?? '',
        creators: item.data?.creators ?? [],
        collections: item.data?.collections ?? [],
        abstractNote: item.data?.abstractNote ?? '',
        tags: item.data?.tags ?? [],
        extra: item.data?.extra ?? '',
        year: item.data?.date ?? '',
        raw: item,
      }));

    results.push(...normalized);

    if (reportedTotal != null) {
      totalResults = reportedTotal;
    }

    const rawCount = Array.isArray(data) ? data.length : 0;
    if (!rawCount || rawCount < requestLimit) {
      break;
    }

    start += rawCount;

    if (totalResults != null && results.length >= totalResults) {
      break;
    }
  }

  if (results.length > targetCount) {
    return results.slice(0, targetCount);
  }
  return results;
}

export async function fetchTopLevelItems(limit = PAGE_SIZE) {
  if (COLLECTION_KEY) {
    const collectionKeys = [COLLECTION_KEY];

    // Fetch subcollections to include their items
    try {
      const subsUrl = new URL(
        buildLibraryUrl(`/collections/${COLLECTION_KEY}/collections`)
      );
      subsUrl.searchParams.set('format', 'json');
      const subsData = await fetchJSON(subsUrl.toString());
      if (Array.isArray(subsData)) {
        collectionKeys.push(...subsData.map((c) => c.key));
      }
    } catch (error) {
      console.warn('Failed to fetch subcollections for item retrieval', error);
    }

    // Fetch items from all relevant collections in parallel
    const resultsArrays = await Promise.all(
      collectionKeys.map((key) =>
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
  if (COLLECTION_KEY) {
    try {
      console.log(`Fetching info for collection ${COLLECTION_KEY}...`);
      // Fetch root collection details
      const rootUrl = new URL(buildLibraryUrl(`/collections/${COLLECTION_KEY}`));
      rootUrl.searchParams.set('format', 'json');
      rootUrl.searchParams.set('include', 'data');

      // Fetch subcollections
      const subsUrl = new URL(
        buildLibraryUrl(`/collections/${COLLECTION_KEY}/collections`)
      );
      subsUrl.searchParams.set('format', 'json');
      subsUrl.searchParams.set('include', 'data');
      subsUrl.searchParams.set('limit', '100');
      subsUrl.searchParams.set('sort', 'title');

      const [rootData, subsData] = await Promise.all([
        fetchJSON(rootUrl.toString()),
        fetchJSON(subsUrl.toString()),
      ]);

      console.log('Root collection data:', rootData);
      console.log('Subcollections data:', subsData);

      const rootCollection = {
        key: rootData.key,
        name: rootData.data?.name ?? 'Untitled',
      };

      const subCollections = (Array.isArray(subsData) ? subsData : []).map(
        (c) => ({
          key: c.key,
          name: c.data?.name ?? 'Untitled',
        })
      );

      console.log('Parsed subcollections:', subCollections);

      // Return root (as "All") + subcollections
      return [rootCollection, ...subCollections];
    } catch (error) {
      console.warn('Failed to load collection metadata', error);
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

async function fetchAttachmentsForItem(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}/children`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemType', 'attachment');
  url.searchParams.set('limit', '50');

  const attachments = await fetchJSON(url.toString());
  return attachments.map((attachment) => {
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
  });
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

function chooseCoverUrl(attachments) {
  const ranked = attachments
    .slice()
    .sort((a, b) => {
      const score = (attachment) => {
        let s = 0;
        const title = attachment.title ?? '';

        // Highest priority: Specific "Book Cover (Web)" title
        if (title.trim() === 'Book Cover (Web)') return 100;

        if (attachment.contentType?.startsWith('image/')) s += 4;
        if (attachment.fileName && isLikelyImageUrl(attachment.fileName)) s += 2;
        if (isLikelyImageUrl(attachment.url)) s += 3;
        if (/cover/i.test(title)) s += 1;
        return s;
      };
      return score(b) - score(a);
    });

  for (const attachment of ranked) {
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
    if (/cover/i.test((attachment.title ?? '') + url)) {
      return url || appendKeyToUrl(enclosure || selfFile);
    }
  }

  return null;
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
    let content = note.content || '';
    // Check for HTML header or Markdown header
    // Matches: <h3>Book Cover (b64)</h3>, # Book Cover (b64), ### Book Cover (b64), etc.
    if (/Book Cover \(b64\)/i.test(content)) {
      // Decode HTML entities that might be present (e.g., &quot; -> ")
      content = content
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'")
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');

      // First, try to extract base64 data URI from the img src
      const match = content.match(/src\s*=\s*["'](data:image\/[^"']+)["']/);
      if (match && match[1]) {
        console.log('[ZoteroClient] Successfully extracted b64 cover from note', { noteKey: note.key, dataLength: match[1].length });
        return match[1];
      }

      // Try alternate pattern without quotes
      const altMatch = content.match(/src\s*=\s*(data:image\/[^\s">]+)/);
      if (altMatch && altMatch[1]) {
        console.log('[ZoteroClient] Extracted b64 cover using alternate pattern', { noteKey: note.key, dataLength: altMatch[1].length });
        return altMatch[1];
      }

      // Zotero 7 converts embedded images to attachment references
      // Look for data-attachment-key="XXXXXXXX" or key="XXXXXXXX" patterns
      // Also handles zapi:key and similar variations
      const attachKeyMatch = content.match(/(?:data-attachment-key|zapi:key|key)\s*=\s*["']([A-Z0-9]{8})["']/i);
      if (attachKeyMatch && attachKeyMatch[1]) {
        const embeddedKey = attachKeyMatch[1];
        console.log('[ZoteroClient] Found embedded attachment key in note', { noteKey: note.key, embeddedKey });

        // Look up this attachment in the fetched attachments
        const embeddedAttachment = attachments.find(att => att.key === embeddedKey);
        if (embeddedAttachment) {
          // Build URL to fetch this embedded image
          const imageUrl = embeddedAttachment.resolvedUrl ||
            appendKeyToUrl(embeddedAttachment.links?.enclosure?.href ||
              (embeddedAttachment.links?.self?.href ? `${embeddedAttachment.links.self.href}/file` : ''));

          if (imageUrl) {
            console.log('[ZoteroClient] Resolved embedded attachment to URL', { embeddedKey, imageUrl: imageUrl.substring(0, 80) + '...' });
            return imageUrl;
          }
        } else {
          console.warn('[ZoteroClient] Embedded attachment key not found in attachments list', { embeddedKey, availableKeys: attachments.map(a => a.key) });
        }
      }

      // Last resort: look for ANY 8-character key in a p tag that also has width/height (image placeholder)
      // Pattern: <p ... key="XXXXXXXX" ... width="..." height="...">
      const imgPlaceholderMatch = content.match(/<p[^>]*?["']([A-Z0-9]{8})["'][^>]*(?:width|height)\s*=/i);
      if (imgPlaceholderMatch && imgPlaceholderMatch[1]) {
        const embeddedKey = imgPlaceholderMatch[1];
        console.log('[ZoteroClient] Found key in image placeholder p tag', { noteKey: note.key, embeddedKey });

        const embeddedAttachment = attachments.find(att => att.key === embeddedKey);
        if (embeddedAttachment) {
          const imageUrl = embeddedAttachment.resolvedUrl ||
            appendKeyToUrl(embeddedAttachment.links?.enclosure?.href ||
              (embeddedAttachment.links?.self?.href ? `${embeddedAttachment.links.self.href}/file` : ''));

          if (imageUrl) {
            console.log('[ZoteroClient] Resolved image placeholder to URL', { embeddedKey, imageUrl: imageUrl.substring(0, 80) + '...' });
            return imageUrl;
          }
        } else {
          console.warn('[ZoteroClient] Image placeholder key not found in attachments', { embeddedKey, availableKeys: attachments.map(a => a.key) });
        }
      }

      // Debug: log full content to help diagnose format issues
      console.warn('[ZoteroClient] Found "Book Cover (b64)" note but failed to extract image.', {
        noteKey: note.key,
        contentLength: content.length,
        fullContent: content,  // Log the FULL content for debugging
        hasDataImage: content.includes('data:image'),
        hasSrcAttr: content.includes('src='),
        hasKeyEquals: content.includes('key='),
        has8CharPattern: /[A-Z0-9]{8}/i.test(content)
      });
    }
  }
  return null;
}

export async function attachCoverImages(items) {
  const chunks = [];
  for (let i = 0; i < items.length; i += ATTACHMENT_CONCURRENCY) {
    chunks.push(items.slice(i, i + ATTACHMENT_CONCURRENCY));
  }

  const withCovers = [];

  for (const chunk of chunks) {
    const results = await Promise.all(
      chunk.map(async (item) => {
        // Fetch both attachments and notes in parallel
        const [attachments, notes] = await Promise.all([
          fetchAttachmentsForItem(item.key),
          fetchNotesForItem(item.key),
        ]);

        // Try to get b64 cover from notes first (pass attachments for embedded image lookup)
        const b64Cover = extractB64CoverFromNotes(notes, attachments);

        // Fall back to web attachment if no b64 cover
        const webCoverUrl = chooseCoverUrl(attachments);

        // Prefer b64 cover over web URL
        const coverUrl = b64Cover || webCoverUrl;

        return {
          ...item,
          attachments,
          coverUrl,
          isB64Cover: !!b64Cover,
        };
      })
    );
    withCovers.push(...results);
  }

  return withCovers;
}

async function fetchNotesForItem(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}/children`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemType', 'note');
  url.searchParams.set('limit', '50');

  const notes = await fetchJSON(url.toString());
  return notes.map((note) => ({
    key: note.key,
    title: note.data?.title ?? '',
    content: note.data?.note ?? '',
    dateModified: note.data?.dateModified ?? '',
  }));
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
  const item = await fetchItemDetails(itemKey);
  const [attachments, notes] = await Promise.all([
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
