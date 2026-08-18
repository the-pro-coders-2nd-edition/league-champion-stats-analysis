export async function fetchBuild(slug, buildSlug) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}`);
  if (!response.ok) throw new Error(`Failed to load build: ${response.status}`);
  return response.json();
}

export async function fetchPlayerStatus(slug) {
  const response = await fetch(`/api/players/${slug}`);
  if (!response.ok) throw new Error(`Failed to load player: ${response.status}`);
  return response.json();
}

export async function fetchAccountViews(slug, buildSlug, accounts) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}/account-views`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ accounts }),
  });
  if (!response.ok) throw new Error(`Failed to load account views: ${response.status}`);
  return response.json();
}

export async function sendChatMessage(reportRef, history) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report: reportRef, history }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to send chat message: ${response.status}`);
  }
  const data = await response.json();
  return data.text || '';
}
