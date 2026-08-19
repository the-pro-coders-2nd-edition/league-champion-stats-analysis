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
