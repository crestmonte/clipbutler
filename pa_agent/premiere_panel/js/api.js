// ClipButler API client for the Premiere CEP panel
const CB_API = 'http://localhost:8765';

async function cbSearch(params) {
  const url = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') url.set(k, v); });
  const r = await fetch(`${CB_API}/api/search?${url}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function cbGetStatus() {
  const r = await fetch(`${CB_API}/api/status`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function cbThumbnailUrl(id) {
  return `${CB_API}/api/thumbnail/${id}`;
}
