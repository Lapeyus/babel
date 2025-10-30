import {
  fetchTopLevelItems,
  fetchCollections,
  attachCoverImages,
  fetchItemBundle,
} from './services/zoteroClient.js';
import { COLLECTION_KEY } from './config.js';

const grid = document.getElementById('libraryGrid');
const searchInput = document.getElementById('searchInput');
const collectionFilter = document.getElementById('collectionFilter');
const itemTemplate = document.getElementById('itemCardTemplate');
const toastEl = document.getElementById('toast');
const detailOverlay = document.getElementById('itemDetail');
const detailTitleEl = detailOverlay?.querySelector('#detailTitle');
const detailCreatorsEl = detailOverlay?.querySelector('.detail-creators');
const detailItemTypeEl = detailOverlay?.querySelector('.detail-item-type');
const detailMetaEl = detailOverlay?.querySelector('.detail-meta');
const detailLoadingEl = detailOverlay?.querySelector('.detail-loading');
const detailSections = {
  info: detailOverlay?.querySelector('[data-panel="info"]'),
  notes: detailOverlay?.querySelector('[data-panel="notes"]'),
  tags: detailOverlay?.querySelector('[data-panel="tags"]'),
  attachments: detailOverlay?.querySelector('[data-panel="attachments"]'),
  related: detailOverlay?.querySelector('[data-panel="related"]'),
};
const detailTabButtons = Array.from(
  detailOverlay?.querySelectorAll('.detail-tabs button') ?? []
);
const detailCloseEls = Array.from(
  detailOverlay?.querySelectorAll('[data-close]') ?? []
);

const state = {
  items: [],
  filtered: [],
  collections: [],
  filters: {
    q: '',
    collection: COLLECTION_KEY || 'all',
  },
};

const detailState = {
  currentTab: 'info',
  activeItemKey: null,
  data: null,
  lastFocus: null,
};

function showToast(message, timeout = 3600) {
  if (!toastEl) return;
  toastEl.textContent = message;
  toastEl.classList.add('show');
  window.setTimeout(() => toastEl.classList.remove('show'), timeout);
}

function formatCreators(creators = []) {
  if (!creators.length) return 'Unknown creator';
  const names = creators.map((creator) => {
    if (creator.name) return creator.name;
    const parts = [creator.firstName, creator.lastName]
      .filter(Boolean)
      .join(' ');
    return parts || 'Unknown creator';
  });
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} & ${names[1]}`;
  return `${names[0]} et al.`;
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function prettifyItemType(value = '') {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

const INFO_FIELD_DEFS = [
  {
    key: 'itemType',
    label: 'Item Type',
    formatter: (value) => prettifyItemType(value),
  },
  { key: 'title', label: 'Title' },
  {
    key: 'creators',
    label: 'Author',
    formatter: (_, data) => formatCreators(data.creators),
  },
  { key: 'series', label: 'Series' },
  { key: 'seriesNumber', label: 'Series Number' },
  { key: 'volume', label: 'Volume' },
  { key: 'numVolumes', label: '# of Volumes' },
  { key: 'edition', label: 'Edition' },
  { key: 'place', label: 'Place' },
  { key: 'publisher', label: 'Publisher' },
  { key: 'date', label: 'Date' },
  { key: 'numPages', label: '# of Pages' },
  {
    key: 'language',
    label: 'Language',
    formatter: (value) => value?.toUpperCase?.() ?? value,
  },
  { key: 'ISBN', label: 'ISBN' },
  { key: 'shortTitle', label: 'Short Title' },
  {
    key: 'url',
    label: 'URL',
    formatter: (value) => value,
    type: 'link',
  },
  { key: 'accessDate', label: 'Accessed', formatter: formatDateTime },
  { key: 'archive', label: 'Archive' },
  { key: 'archiveLocation', label: 'Loc. in Archive' },
  { key: 'libraryCatalog', label: 'Library Catalog' },
  { key: 'callNumber', label: 'Call Number' },
  { key: 'rights', label: 'Rights' },
  { key: 'extra', label: 'Extra' },
  { key: 'dateAdded', label: 'Date Added', formatter: formatDateTime },
  { key: 'dateModified', label: 'Date Modified', formatter: formatDateTime },
];

function buildMetaString(data = {}) {
  const parts = [];
  const added = formatDateTime(data.dateAdded);
  const modified = formatDateTime(data.dateModified);
  if (added) parts.push(`Added ${added}`);
  if (modified) parts.push(`Updated ${modified}`);
  if (data.libraryCatalog) parts.push(data.libraryCatalog);
  if (data.callNumber) parts.push(`Call # ${data.callNumber}`);
  return parts.join(' • ');
}

function createCard(item) {
  const fragment = itemTemplate.content.cloneNode(true);
  const titleButton = fragment.querySelector('.title-link');
  const creatorEl = fragment.querySelector('.creator');
  const coverWrapper = fragment.querySelector('.cover-wrapper');
  const coverImg = fragment.querySelector('.cover');
  const coverFallback = fragment.querySelector('.cover-fallback');
  const abstractEl = fragment.querySelector('.cover-abstract');

  titleButton.textContent = item.title || 'Untitled';
  titleButton.setAttribute(
    'aria-label',
    `Open details for ${item.title || 'Untitled'}`
  );
  titleButton.addEventListener('click', () => openItemDetail(item));

  creatorEl.textContent = formatCreators(item.creators);
  const abstractText = item.abstractNote?.trim();
  abstractEl.textContent = abstractText || 'No abstract available.';

  if (item.coverUrl) {
    coverImg.src = item.coverUrl;
    coverImg.alt = `Cover of ${item.title}`;
    coverFallback.textContent = '';
    coverWrapper.classList.remove('no-cover');
  } else {
    coverWrapper.classList.add('no-cover');
    if (coverImg) {
      coverImg.remove();
    }
    const fallbackLetter = (item.title?.trim().charAt(0) || '?').toUpperCase();
    coverFallback.textContent = fallbackLetter;
  }

  const toggleFlip = () => {
    const flipped = coverWrapper.classList.toggle('is-flipped');
    coverWrapper.setAttribute('aria-pressed', flipped ? 'true' : 'false');
  };

  coverWrapper.addEventListener('click', toggleFlip);
  coverWrapper.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleFlip();
    }
  });

  return fragment;
}

function renderEmptyState() {
  grid.innerHTML =
    '<p class="empty-state">No items match the current filters.</p>';
}

function renderItems(items) {
  if (!items.length) {
    renderEmptyState();
    return;
  }

  grid.innerHTML = '';
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    fragment.appendChild(createCard(item));
  });
  grid.appendChild(fragment);
}

function activateDetailTab(tabId = 'info') {
  detailState.currentTab = tabId;
  detailTabButtons.forEach((button) => {
    const isActive = button.dataset.tab === tabId;
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    button.tabIndex = isActive ? 0 : -1;
  });
  Object.entries(detailSections).forEach(([key, section]) => {
    if (!section) return;
    section.hidden = key !== tabId;
  });
}

function clearDetailSections() {
  Object.values(detailSections).forEach((section) => {
    if (section) section.innerHTML = '';
  });
}

function setDetailLoading(isLoading) {
  if (!detailLoadingEl) return;
  detailLoadingEl.style.display = isLoading ? 'block' : 'none';
}

function renderInfoSection(data = {}) {
  const section = detailSections.info;
  if (!section) return;

  section.innerHTML = '';
  const grid = document.createElement('div');
  grid.className = 'info-grid';

  INFO_FIELD_DEFS.forEach((field) => {
    const rawValue = field.key === 'creators' ? data.creators : data[field.key];
    let value = field.formatter ? field.formatter(rawValue, data) : rawValue;
    if (!value) return;

    if (Array.isArray(value)) {
      value = value.join(', ');
    }

    const fieldEl = document.createElement('div');
    fieldEl.className = 'info-field';

    const labelEl = document.createElement('span');
    labelEl.className = 'info-label';
    labelEl.textContent = field.label;
    fieldEl.appendChild(labelEl);

    if (field.type === 'link') {
      const linkEl = document.createElement('a');
      linkEl.className = 'info-value';
      linkEl.href = value;
      linkEl.textContent = value;
      linkEl.target = '_blank';
      linkEl.rel = 'noopener';
      fieldEl.appendChild(linkEl);
    } else {
      const valueEl = document.createElement('span');
      valueEl.className = 'info-value';
      valueEl.textContent = value;
      fieldEl.appendChild(valueEl);
    }

    grid.appendChild(fieldEl);
  });

  if (grid.children.length) {
    section.appendChild(grid);
  } else {
    section.innerHTML =
      '<p class="empty-panel">No descriptive fields available.</p>';
  }

  if (data.abstractNote) {
    const abstractWrapper = document.createElement('div');
    abstractWrapper.className = 'info-abstract';
    const heading = document.createElement('h3');
    heading.textContent = 'Abstract';
    const body = document.createElement('p');
    body.textContent = data.abstractNote;
    abstractWrapper.append(heading, body);
    section.appendChild(abstractWrapper);
  }
}

function renderNotesSection(notes = []) {
  const section = detailSections.notes;
  if (!section) return;

  section.innerHTML = '';
  if (!notes.length) {
    section.innerHTML = '<p class="empty-panel">No notes yet.</p>';
    return;
  }

  const list = document.createElement('ul');
  list.className = 'notes-list';

  notes.forEach((note, index) => {
    const li = document.createElement('li');
    li.className = 'note-card';

    const heading = document.createElement('h4');
    heading.textContent = note.title || `Note ${index + 1}`;
    li.appendChild(heading);

    const content = document.createElement('div');
    content.className = 'note-content';
    content.innerHTML = note.content;
    li.appendChild(content);

    if (note.dateModified) {
      const meta = document.createElement('div');
      meta.className = 'attachment-meta';
      meta.textContent = `Updated ${formatDateTime(note.dateModified)}`;
      li.appendChild(meta);
    }

    list.appendChild(li);
  });

  section.appendChild(list);
}

function renderTagsSection(tags = []) {
  const section = detailSections.tags;
  if (!section) return;

  section.innerHTML = '';
  const values = tags
    .map((tag) => (typeof tag === 'string' ? tag : tag.tag))
    .filter(Boolean);

  if (!values.length) {
    section.innerHTML = '<p class="empty-panel">No tags added yet.</p>';
    return;
  }

  const list = document.createElement('ul');
  list.className = 'tags-list';

  values.forEach((tag) => {
    const li = document.createElement('li');
    li.textContent = tag;
    list.appendChild(li);
  });

  section.appendChild(list);
}

function renderAttachmentsSection(attachments = []) {
  const section = detailSections.attachments;
  if (!section) return;

  section.innerHTML = '';
  if (!attachments.length) {
    section.innerHTML = '<p class="empty-panel">No attachments found.</p>';
    return;
  }

  const list = document.createElement('ul');
  list.className = 'attachments-list';

  attachments.forEach((attachment) => {
    const li = document.createElement('li');

    const info = document.createElement('div');
    const title = document.createElement('p');
    title.className = 'attachment-title';
    title.textContent =
      attachment.title ||
      attachment.fileName ||
      attachment.contentType ||
      'Attachment';
    info.appendChild(title);

    const metaText = [attachment.contentType, attachment.fileName]
      .filter(Boolean)
      .join(' • ');
    if (metaText) {
      const meta = document.createElement('span');
      meta.className = 'attachment-meta';
      meta.textContent = metaText;
      info.appendChild(meta);
    }

    const actions = document.createElement('div');
    const url = attachment.resolvedUrl || attachment.url;
    if (url) {
      const link = document.createElement('a');
      link.className = 'detail-link';
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Open';
      actions.appendChild(link);
    } else {
      const span = document.createElement('span');
      span.className = 'attachment-meta';
      span.textContent = 'No public link available';
      actions.appendChild(span);
    }

    li.append(info, actions);
    list.appendChild(li);
  });

  section.appendChild(list);
}

function renderRelatedSection(items = []) {
  const section = detailSections.related;
  if (!section) return;

  section.innerHTML = '';
  if (!items.length) {
    section.innerHTML = '<p class="empty-panel">No related items linked.</p>';
    return;
  }

  const list = document.createElement('ul');
  list.className = 'related-list';

  items.forEach((related) => {
    const li = document.createElement('li');

    const info = document.createElement('div');
    const title = document.createElement('p');
    title.className = 'related-title';
    title.textContent = related.title || 'Untitled';
    info.appendChild(title);

    const metaText = formatCreators(related.creators || []);
    if (metaText) {
      const meta = document.createElement('span');
      meta.className = 'attachment-meta';
      meta.textContent = metaText;
      info.appendChild(meta);
    }

    const actions = document.createElement('div');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'detail-link';
    button.textContent = 'View';
    button.addEventListener('click', () => {
      const existing =
        state.items.find((item) => item.key === related.key) ?? {
          key: related.key,
          title: related.title,
          creators: related.creators ?? [],
          abstractNote: related.data?.abstractNote ?? '',
          collections: related.data?.collections ?? [],
        };
      openItemDetail(existing);
    });
    actions.appendChild(button);

    li.append(info, actions);
    list.appendChild(li);
  });

  section.appendChild(list);
}

function handleDetailTabKeydown(event) {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  const currentIndex = detailTabButtons.indexOf(event.currentTarget);
  if (currentIndex === -1) return;

  let nextIndex = currentIndex;
  if (event.key === 'ArrowRight') {
    nextIndex = (currentIndex + 1) % detailTabButtons.length;
  } else if (event.key === 'ArrowLeft') {
    nextIndex =
      (currentIndex - 1 + detailTabButtons.length) % detailTabButtons.length;
  } else if (event.key === 'Home') {
    nextIndex = 0;
  } else if (event.key === 'End') {
    nextIndex = detailTabButtons.length - 1;
  }

  const nextButton = detailTabButtons[nextIndex];
  if (!nextButton) return;
  activateDetailTab(nextButton.dataset.tab);
  nextButton.focus();
}

function renderDetailBundle(bundle, fallbackItem) {
  detailState.data = bundle;
  const baseData = fallbackItem?.raw?.data ?? {};
  const mergedData = bundle?.item?.data
    ? { ...baseData, ...bundle.item.data }
    : { ...baseData };

  if (!mergedData.title) mergedData.title = fallbackItem?.title ?? 'Untitled';
  if (!mergedData.creators) mergedData.creators = fallbackItem?.creators ?? [];
  if (!mergedData.abstractNote)
    mergedData.abstractNote = fallbackItem?.abstractNote ?? '';

  if (detailTitleEl) {
    detailTitleEl.textContent = mergedData.title || 'Untitled';
  }
  if (detailCreatorsEl) {
    detailCreatorsEl.textContent = formatCreators(mergedData.creators);
  }
  if (detailItemTypeEl) {
    detailItemTypeEl.textContent = prettifyItemType(
      mergedData.itemType || fallbackItem?.raw?.data?.itemType || ''
    );
  }
  if (detailMetaEl) {
    detailMetaEl.textContent = buildMetaString(mergedData);
  }

  renderInfoSection(mergedData);
  renderNotesSection(bundle.notes);
  renderTagsSection(mergedData.tags ?? []);
  renderAttachmentsSection(bundle.attachments);
  renderRelatedSection(bundle.relatedItems);

  setDetailLoading(false);
  activateDetailTab('info');
}

async function openItemDetail(item) {
  if (!detailOverlay) return;

  detailState.lastFocus = document.activeElement;
  detailState.activeItemKey = item.key;
  detailState.currentTab = 'info';

  detailOverlay.classList.remove('hidden');
  detailOverlay.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';

  clearDetailSections();
  activateDetailTab('info');
  setDetailLoading(true);

  if (detailTitleEl) detailTitleEl.textContent = item.title || 'Untitled';
  if (detailCreatorsEl) detailCreatorsEl.textContent = formatCreators(item.creators);
  if (detailItemTypeEl)
    detailItemTypeEl.textContent = prettifyItemType(
      item.raw?.data?.itemType || ''
    );
  if (detailMetaEl) detailMetaEl.textContent = 'Loading latest details…';

  window.setTimeout(() => {
    detailTabButtons[0]?.focus();
  }, 0);

  try {
    const bundle = await fetchItemBundle(item.key);
    if (detailState.activeItemKey !== item.key) return;
    renderDetailBundle(bundle, item);
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Unable to load item details.');
    renderInfoSection(item.raw?.data ?? item);
    renderNotesSection([]);
    renderTagsSection(item.raw?.data?.tags ?? item.tags ?? []);
    renderAttachmentsSection(item.attachments ?? []);
    renderRelatedSection([]);
    setDetailLoading(false);
  }
}

function closeDetail() {
  if (!detailOverlay || detailOverlay.classList.contains('hidden')) return;
  detailOverlay.classList.add('hidden');
  detailOverlay.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
  detailState.activeItemKey = null;
  detailState.data = null;
  clearDetailSections();
  setDetailLoading(true);
  if (detailState.lastFocus) {
    detailState.lastFocus.focus();
    detailState.lastFocus = null;
  }
}

function applyFilters() {
  const { q, collection } = state.filters;
  const query = q.trim().toLowerCase();

  let filtered = state.items;

  if (collection !== 'all') {
    filtered = filtered.filter((item) =>
      item.collections?.includes(collection)
    );
  }

  if (query) {
    filtered = filtered.filter((item) => {
      const titleMatch = item.title?.toLowerCase().includes(query);
      const creatorMatch = formatCreators(item.creators)
        .toLowerCase()
        .includes(query);
      return titleMatch || creatorMatch;
    });
  }

  state.filtered = filtered;
  renderItems(filtered);
}

function populateCollectionFilter(collections) {
  if (!collections.length) return;
  const fragment = document.createDocumentFragment();
  collections.forEach((collection) => {
    const option = document.createElement('option');
    option.value = collection.key;
    option.textContent = collection.name;
    fragment.appendChild(option);
  });
  collectionFilter.appendChild(fragment);

  if (COLLECTION_KEY) {
    collectionFilter.value = COLLECTION_KEY;
    state.filters.collection = COLLECTION_KEY;
    collectionFilter.disabled = true;
    collectionFilter.title = 'Collection locked via configuration';
  }
}

async function bootstrap() {
  grid.innerHTML = '<p class="loading-state">Loading library…</p>';

  try {
    const [items, collections] = await Promise.all([
      fetchTopLevelItems(),
      fetchCollections().catch(() => []),
    ]);

    const itemsWithCovers = await attachCoverImages(items);

    state.items = itemsWithCovers;
    state.collections = collections;

    populateCollectionFilter(collections);
    applyFilters();
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Failed to load Zotero items.');
    renderEmptyState();
  }
}

searchInput.addEventListener('input', (event) => {
  state.filters.q = event.target.value;
  applyFilters();
});

collectionFilter.addEventListener('change', (event) => {
  state.filters.collection = event.target.value;
  applyFilters();
});

detailCloseEls.forEach((el) => {
  el.addEventListener('click', (event) => {
    event.preventDefault();
    closeDetail();
  });
});

detailTabButtons.forEach((button) => {
  button.addEventListener('click', () => {
    activateDetailTab(button.dataset.tab);
    button.focus();
  });
  button.addEventListener('keydown', handleDetailTabKeydown);
});

if (detailOverlay) {
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !detailOverlay.classList.contains('hidden')) {
      closeDetail();
    }
  });
}

bootstrap();
