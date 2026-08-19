// In-memory cache of fetched build payloads, keyed by `${slug}/${buildSlug}`.
// Module-level (not component-level) so it survives navigating away to PlayerHub/Landing
// and back to a report -- Report.svelte gets recreated on those navigations, but this
// module stays loaded for the life of the SPA session. Cleared entirely on a full page
// reload, which is the correct time to accept stale data being gone.
//
// A small LRU cap keeps memory bounded for players with many champion builds: each get()
// re-inserts its entry so recently-viewed builds survive eviction longest.

const MAX_ENTRIES = 12;
const cache = new Map();

export function getCachedBuild(key) {
  if (!cache.has(key)) return undefined;
  const value = cache.get(key);
  cache.delete(key);
  cache.set(key, value); // move to the end -- most recently used
  return value;
}

export function setCachedBuild(key, payload) {
  cache.delete(key);
  cache.set(key, payload);
  while (cache.size > MAX_ENTRIES) {
    cache.delete(cache.keys().next().value);
  }
}

// The player-status endpoint (already polled regularly while any report is open) is
// metadata-only -- cheap to call -- and carries a fresh `generated_at` for every build
// a player has, not just the one currently displayed. Comparing it against a cached
// entry's own `generated_at` tells us, for free, whether that entry is still good
// without a dedicated staleness endpoint. A mismatch drops the entry rather than
// eagerly refetching -- nobody's looking at that build right now, so the next visit's
// normal cache-miss fetch is enough.
export function invalidateIfStale(key, freshGeneratedAt) {
  const cached = cache.get(key);
  if (cached && freshGeneratedAt && cached.generated_at !== freshGeneratedAt) {
    cache.delete(key);
  }
}
