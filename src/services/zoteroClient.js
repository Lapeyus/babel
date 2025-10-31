import {
  LIBRARY_TYPE,
  LIBRARY_ID,
  API_KEY,
  PAGE_SIZE,
  ATTACHMENT_CONCURRENCY,
  COLLECTION_KEY,
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

export async function fetchTopLevelItems(limit = PAGE_SIZE) {
  const path = COLLECTION_KEY
    ? `/collections/${COLLECTION_KEY}/items/top`
    : '/items/top';
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

export async function fetchCollections() {
  if (COLLECTION_KEY) {
    try {
      const url = new URL(buildLibraryUrl(`/collections/${COLLECTION_KEY}`));
      url.searchParams.set('format', 'json');
      url.searchParams.set('include', 'data');
      const collection = await fetchJSON(url.toString());
      return [
        {
          key: collection.key,
          name: collection.data?.name ?? 'Untitled',
        },
      ];
    } catch (error) {
      console.warn('Failed to load collection metadata', error);
      return [];
    }
  }

  const url = new URL(buildLibraryUrl('/collections/top'));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('limit', '200');

  const collections = await fetchJSON(url.toString());
  return collections.map((collection) => ({
    key: collection.key,
    name: collection.data?.name ?? 'Untitled',
  }));
}

async function fetchAttachmentsForItem(itemKey) {
  const url = new URL(buildLibraryUrl(`/items/${itemKey}/children`));
  url.searchParams.set('format', 'json');
  url.searchParams.set('include', 'data');
  url.searchParams.set('itemType', 'attachment');
  url.searchParams.set('limit', '50');

  const attachments = await fetchJSON(url.toString());
  return attachments.map((attachment) => ({
    key: attachment.key,
    parentItem: attachment.data?.parentItem ?? '',
    contentType: attachment.data?.contentType ?? '',
    fileName: attachment.data?.filename ?? '',
    title: attachment.data?.title ?? '',
    url: attachment.data?.url ?? '',
    linkMode: attachment.data?.linkMode ?? '',
    links: attachment.links ?? {},
    resolvedUrl:
      appendKeyToUrl(
        attachment.links?.enclosure?.href ??
          (attachment.links?.self?.href
            ? `${attachment.links.self.href}/file`
            : '')
      ) || attachment.data?.url || '',
  }));
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
        if (attachment.contentType?.startsWith('image/')) s += 4;
        if (attachment.fileName && isLikelyImageUrl(attachment.fileName)) s += 2;
        if (isLikelyImageUrl(attachment.url)) s += 3;
        if (/cover/i.test(attachment.title ?? '')) s += 1;
        return s;
      };
      return score(b) - score(a);
    });

  for (const attachment of ranked) {
    const { links, url } = attachment;
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

export async function attachCoverImages(items) {
  const chunks = [];
  for (let i = 0; i < items.length; i += ATTACHMENT_CONCURRENCY) {
    chunks.push(items.slice(i, i + ATTACHMENT_CONCURRENCY));
  }

  const withCovers = [];

  for (const chunk of chunks) {
    const results = await Promise.all(
      chunk.map(async (item) => {
        const attachments = await fetchAttachmentsForItem(item.key);
        const coverUrl = chooseCoverUrl(attachments);
        return {
          ...item,
          attachments,
          coverUrl,
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
