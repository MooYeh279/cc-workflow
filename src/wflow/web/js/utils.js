const API = '/api/v1';

async function apiGet(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const url = qs ? `${API}${path}?${qs}` : `${API}${path}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.status}`);
  return resp.json();
}

async function apiPost(path, body = {}) {
  const resp = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST ${path}: ${resp.status}`);
  if (resp.status === 204) return null;
  return resp.json();
}

async function apiDelete(path) {
  const resp = await fetch(`${API}${path}`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`DELETE ${path}: ${resp.status}`);
}

// Expose globally so Alpine can access them
window.apiGet = apiGet;
window.apiPost = apiPost;
window.apiDelete = apiDelete;
