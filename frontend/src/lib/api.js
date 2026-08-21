import { rewriteWebAssetHrefs } from './assets.js';

export async function fetchBuild(slug, buildSlug) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}`);
  if (!response.ok) throw new Error(`Failed to load build: ${response.status}`);
  return rewriteWebAssetHrefs(await response.json());
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
  return rewriteWebAssetHrefs(await response.json());
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

/**
 * Subscribes to a `text/event-stream` endpoint, calling `onMessage` with the
 * parsed JSON payload of every event (including the initial snapshot the
 * server sends immediately on connect). Uses the browser's native
 * `EventSource`, which reconnects automatically on a dropped connection --
 * no manual reconnect logic needed here. `onError`, if given, fires while a
 * connection attempt is failing/retrying (matches the transient-error UI a
 * poller would have shown between failed ticks). Returns an `unsubscribe()`
 * function that closes the connection; call it from the component's teardown.
 *
 * A malformed event is swallowed -- the next one will still arrive.
 */
function subscribeEvents(endpoint, onMessage, onError) {
  const source = new EventSource(endpoint);
  source.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch {
      // Ignore a malformed event -- the next one will still arrive.
    }
  };
  if (onError) source.onerror = onError;
  return () => source.close();
}

export function subscribePlayerStatus(slug, onMessage, onError) {
  return subscribeEvents(`/api/players/${slug}/events`, onMessage, onError);
}

export function subscribeActivity(onMessage, onError) {
  return subscribeEvents('/api/activity/events', onMessage, onError);
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

export async function refreshPlayer(slug, { champion, role } = {}) {
  const response = await fetch(`/api/players/${slug}/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ champion: champion || '', role: role || '' }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `Refresh failed: ${response.status}`));
  return data;
}

export async function regeneratePlayer(slug) {
  const response = await fetch(`/api/players/${slug}/regenerate`, { method: 'POST' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `Regenerate failed: ${response.status}`));
  return data;
}

export async function setPlayerWatch(slug, enabled, { intervalS } = {}) {
  const response = await fetch(`/api/players/${slug}/watch`, {
    method: enabled ? 'POST' : 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: enabled && intervalS ? JSON.stringify({ interval_s: intervalS }) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(detailMessage(data, `Could not update watch: ${response.status}`));
  }
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

export async function sendChatMessage(reportRef, history, tab, context) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report: reportRef, history, tab, context }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to send chat message: ${response.status}`);
  }
  const data = await response.json();
  return data.text || '';
}

export async function dropCareerBlock(slug, buildSlug, slot) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}/career/drop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slot }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `Drop failed: ${response.status}`));
  return data;
}

export async function ackCareerRecap(slug, buildSlug, { matchId, gameMs, hits, trackKey }) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}/career/recap/ack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      match_id: matchId,
      game_ms: gameMs,
      hits,
      track_key: trackKey,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data, `Recap ack failed: ${response.status}`));
  return data;
}
