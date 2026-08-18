import { onDestroy } from 'svelte';

/**
 * Registers cleanup for a poll-on-interval lifecycle. Must be called
 * synchronously during component initialization (it calls onDestroy), but
 * the returned methods can be called later (e.g. after an async setup step
 * resolves in onMount).
 *
 * - `start(fn, intervalMs)`: runs `fn` immediately, then repeats it on the
 *   given interval. Clears any previous interval first, so it doubles as a
 *   "restart now" for user-triggered actions (e.g. after a refresh request).
 * - `reschedule(intervalMs)`: changes the interval for the *next* ticks of
 *   whatever function was last passed to `start`, without calling it again
 *   immediately — for a poll function that wants to slow itself down once
 *   it detects nothing is happening, without re-triggering itself mid-call.
 */
export function createPoller() {
  let timer = null;
  let currentFn = null;

  onDestroy(() => {
    if (timer) clearInterval(timer);
  });

  function reschedule(intervalMs) {
    if (timer) clearInterval(timer);
    if (currentFn) timer = setInterval(currentFn, intervalMs);
  }

  function start(fn, intervalMs) {
    currentFn = fn;
    reschedule(intervalMs);
    return fn();
  }

  return { start, reschedule };
}
