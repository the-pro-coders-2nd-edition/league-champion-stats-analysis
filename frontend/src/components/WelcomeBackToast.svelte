<script>
  import { onDestroy } from 'svelte';

  // The `welcome_back` field from `/api/players/{slug}` (see
  // `league_stats.web.welcome_back.compute_welcome_back_summary`): a
  // `{ new_match_id, match_summary, detected_at_unix }` object, or null when
  // there is nothing new. `match_summary` carries win/kills/deaths/assists/
  // kda/cs_per_min/damage_share/champion for the just-detected game.
  export let data = null;
  export let onDismiss = () => {};

  // Long enough to read at a glance without lingering; matches the codebase's
  // other self-clearing banners (e.g. Report.svelte's refresh-failed banner).
  const AUTO_DISMISS_MS = 9000;

  let timer = null;

  function clearTimer() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function dismiss() {
    clearTimer();
    onDismiss();
  }

  // Deliberately keyed on `data` alone: a poll tick that returns the same
  // (already-shown) toast never re-fires this, since the server-side cache
  // is consumed on read and only ever hands back a given update once.
  $: if (data) {
    clearTimer();
    timer = setTimeout(dismiss, AUTO_DISMISS_MS);
  }

  onDestroy(clearTimer);

  $: summary = data?.match_summary || null;
  $: resultClass = summary?.win ? 'win' : 'loss';
  $: resultLabel = summary?.win ? 'Victory' : 'Defeat';
  $: kda = summary ? `${summary.kills ?? 0} / ${summary.deaths ?? 0} / ${summary.assists ?? 0}` : '';
  $: csPerMin = summary?.cs_per_min != null ? summary.cs_per_min.toFixed(1) : null;
  $: damageSharePct = summary?.damage_share != null ? Math.round(summary.damage_share * 100) : null;
</script>

{#if summary}
  <div class="welcome-back-toast welcome-back-toast--{resultClass}" role="status">
    <button
      type="button"
      class="welcome-back-toast-close"
      aria-label="Dismiss"
      on:click={dismiss}
    >×</button>
    <div class="welcome-back-toast-header">
      <span class="welcome-back-toast-result">{resultLabel}</span>
      {#if summary.champion}
        <span class="welcome-back-toast-champ">{summary.champion}</span>
      {/if}
    </div>
    <div class="welcome-back-toast-stats">
      <div class="welcome-back-toast-stat">
        <span class="welcome-back-toast-stat-value">{kda}</span>
        <span class="welcome-back-toast-stat-label">KDA</span>
      </div>
      {#if csPerMin != null}
        <div class="welcome-back-toast-stat">
          <span class="welcome-back-toast-stat-value">{csPerMin}</span>
          <span class="welcome-back-toast-stat-label">CS/min</span>
        </div>
      {/if}
      {#if damageSharePct != null}
        <div class="welcome-back-toast-stat">
          <span class="welcome-back-toast-stat-value">{damageSharePct}%</span>
          <span class="welcome-back-toast-stat-label">Dmg share</span>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .welcome-back-toast {
    position: fixed;
    right: var(--space-4);
    bottom: var(--space-4);
    z-index: 60;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 220px;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-divider);
    border-left: 3px solid transparent;
    background: var(--color-surface-2);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.24);
    opacity: 0;
    transform: translateY(12px);
    animation: welcomeBackToastIn 0.35s ease forwards;
  }
  .welcome-back-toast--win { border-left-color: var(--tone-good-line); }
  .welcome-back-toast--loss { border-left-color: var(--tone-bad-line); }

  .welcome-back-toast-close {
    position: absolute;
    top: var(--space-2);
    right: var(--space-2);
    width: 20px;
    height: 20px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--color-neutral-500);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
  }
  .welcome-back-toast-close:hover { color: var(--color-neutral-300); }

  .welcome-back-toast-header {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    padding-right: var(--space-4);
  }
  .welcome-back-toast-result { font-size: 14px; font-weight: 800; }
  .welcome-back-toast--win .welcome-back-toast-result { color: var(--tone-good-fg); }
  .welcome-back-toast--loss .welcome-back-toast-result { color: var(--tone-bad-fg); }
  .welcome-back-toast-champ { font-size: 12px; color: var(--color-neutral-500); }

  .welcome-back-toast-stats {
    display: flex;
    gap: var(--space-4);
  }
  .welcome-back-toast-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .welcome-back-toast-stat-value {
    font-size: 13px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .welcome-back-toast-stat-label {
    font-size: 10px;
    color: var(--color-neutral-600);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  @keyframes welcomeBackToastIn { to { opacity: 1; transform: translateY(0); } }

  @media (prefers-reduced-motion: reduce) {
    .welcome-back-toast { animation: none; opacity: 1; transform: none; }
  }
</style>
