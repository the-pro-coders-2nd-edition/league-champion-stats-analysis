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

function detailMessage(body, fallback) {
  let detail = body.detail;
  if (Array.isArray(detail)) {
    detail = detail.map((item) => item.msg || JSON.stringify(item)).join('; ');
  }
  return detail || fallback;
}

export async function fetchGroups() {
  const response = await fetch('/api/groups');
  if (!response.ok) throw new Error(`Failed to load groups: ${response.status}`);
  return response.json();
}

export async function fetchActivity() {
  const response = await fetch('/api/activity', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load activity: ${response.status}`);
  return response.json();
}

export async function submitAnalysis({ players, region, minGames }) {
  const response = await fetch('/api/analyses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ players, region, min_games: minGames }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `HTTP ${response.status}`));
  return data;
}

export async function fetchJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) throw new Error(`Failed to load job: ${response.status}`);
  return response.json();
}

export async function cancelJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `Failed to cancel job: ${response.status}`));
  return data;
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
