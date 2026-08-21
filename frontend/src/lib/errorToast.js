import { writable } from 'svelte/store';

// Generic, non-technical copy only -- never interpolate a backend message or
// HTTP status text here. The real error is still logged to the console (see
// `reportApiError` below) for developer debugging; it just never reaches
// this store, so a component rendering these toasts can't leak it even by
// accident.
const MESSAGES = {
  network: "Can't reach the server. Check your connection and try again.",
  server: 'Something went wrong. Please try again.',
  client: 'Something went wrong. Please try again.',
};

const AUTO_DISMISS_MS = 6000;

let nextId = 0;
// Keyed by `kind` rather than toast id: a burst of failures of the same kind
// (e.g. a poll that keeps failing every few seconds) collapses onto one
// toast and just keeps refreshing its dismiss timer, instead of stacking a
// duplicate for every failed call.
const dismissTimers = new Map();

export const errorToasts = writable([]);

function scheduleDismiss(kind) {
  clearTimeout(dismissTimers.get(kind));
  const timer = setTimeout(() => {
    dismissTimers.delete(kind);
    errorToasts.update((toasts) => toasts.filter((toast) => toast.kind !== kind));
  }, AUTO_DISMISS_MS);
  dismissTimers.set(kind, timer);
}

/**
 * Records that an API call failed and queues a generic toast for it.
 * `kind` picks the user-facing copy ('network' | 'server' | 'client', falls
 * back to 'server'). `detail` is for the console only -- pass the real Error
 * or a status/URL string, it is never shown in the UI.
 */
export function reportApiError(kind = 'server', detail) {
  // eslint-disable-next-line no-console
  console.error(`[api] request failed (${kind})`, detail);

  const message = MESSAGES[kind] || MESSAGES.server;
  errorToasts.update((toasts) => {
    if (toasts.some((toast) => toast.kind === kind)) return toasts;
    return [...toasts, { id: ++nextId, kind, message }];
  });
  scheduleDismiss(kind);
}

export function dismissErrorToast(id) {
  errorToasts.update((toasts) => {
    const toast = toasts.find((item) => item.id === id);
    if (toast) {
      clearTimeout(dismissTimers.get(toast.kind));
      dismissTimers.delete(toast.kind);
    }
    return toasts.filter((item) => item.id !== id);
  });
}
