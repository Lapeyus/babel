export const LIBRARY_TYPE = 'users'; // "users" or "groups"
export const LIBRARY_ID = '1595072'; // Zotero user or group ID
export const API_KEY = 'qdG2rwj0E6BfsxgvINYaum6s'; // Optional, but required for private libraries or file access

// export const COLLECTION_KEY = "F753DWXD"; // Optional: focus on a specific collection key
export const ALLOWED_COLLECTIONS = ['F753DWXD', '7XAUZNUB', 'DUZZXNMG'];
export const DEFAULT_COLLECTION_ID = '7XAUZNUB';
export const PAGE_SIZE = 3000;
export const ATTACHMENT_CONCURRENCY = 6;

// WebDAV Configuration
// Set this to your WebDAV base URL to redirect attachment links there instead of Zotero API.
// Example: 'https://your-webdav-server.com/zotero/'
// Files are expected at: {WEBDAV_BASE_URL}/{itemKey}.zip (Zotero's default WebDAV format)
// Leave empty to use Zotero API links.
export const WEBDAV_BASE_URL = 'https://webdav.kerewes.com/zotero/';
