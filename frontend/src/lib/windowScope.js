// Svelte context key for the window-scope label, set once in Report.svelte and read by
// SectionHeader so every section stops recomputing its own copy of this reactive statement.
export const WINDOW_SCOPE_KEY = 'windowScopeLabel';

// Human-readable label for the currently active game-window filter, e.g. "Last 20 games"
// or "All games" — shown as a scope pill next to a section title.
export function computeWindowScopeLabel(data) {
  const option = (data.game_window_options || []).find((o) => o.key === data.game_window_default);
  return option ? `${option.label} games` : 'All games';
}
