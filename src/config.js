export const LIBRARY_TYPE = 'users'; // "users" or "groups"
export const LIBRARY_ID = ''; // Zotero user or group ID
export const API_KEY = ''; // Optional, but required for private libraries or file access

export const COLLECTION_KEY = ""; // Optional: focus on a specific collection key
export const PAGE_SIZE = 3000;
export const ATTACHMENT_CONCURRENCY = 6;

// WebDAV Configuration
// Set this to your WebDAV base URL to redirect attachment links there instead of Zotero API.
// Example: 'https://your-webdav-server.com/zotero/'
// Files are expected at: {WEBDAV_BASE_URL}/{itemKey}.zip (Zotero's default WebDAV format)
// Leave empty to use Zotero API links.
export const WEBDAV_BASE_URL = 'https://webdav.kerewes.com/zotero/';
