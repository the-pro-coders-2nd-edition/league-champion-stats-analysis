<script>
  import Meter from './Meter.svelte';
  import Disclosure from './Disclosure.svelte';

  export let name;
  export let score;
  export let hint = '';
  export let ingredients = [];

  // Own `open` state per instance -- fixes the bug where every dimension shared one flag.
  let open = false;

  $: pct = Math.max(0, Math.min(100, Math.round(Number(score) || 0)));
  $: hasIngredients = (ingredients || []).length > 0;

  const RATE_COLUMNS = {
    kill_participation: 1, damage_share: 1, gold_share: 1, damage_taken_share: 1,
    tf_participation: 1, tf_won_share: 1, lane_priority: 1, objectives_present_rate: 1, kp15: 1,
  };

  function formatIngredientValue(column, value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const num = Number(value);
    if (RATE_COLUMNS[column] || (column && String(column).slice(-5) === '_rate')) {
      return (num * 100).toFixed(0) + '%';
    }
    if (column === 'vspm' || column === 'ccpm' || column === 'first_item_min') {
      return num.toFixed(1);
    }
    if (column === 'avg_unspent_gold' || column === 'gd10' || column === 'gd15' || column === 'xpd10') {
      return String(Math.round(num));
    }
    if (Math.abs(num - Math.round(num)) < 1e-6) return String(Math.round(num));
    return num.toFixed(1);
  }

  function ingredientPct(item) {
    return Math.max(0, Math.min(100, Math.round(Number(item.score) || 0)));
  }
</script>

{#if hasIngredients}
  <Disclosure variant="score" chevron="trailing" bind:open title={hint || null}>
    <svelte:fragment slot="summary">
      <div class="gr-score-summary">
        <span class="gr-score-name">{name || 'Score'}</span>
        <span class="gr-score-value">{pct}</span>
        <Meter value={pct} ramp size="sm" --meter-grid-column="1 / -1" />
      </div>
    </svelte:fragment>
    <div class="gr-score-details">
      {#each ingredients as item (item.column || item.label)}
        {@const subPct = ingredientPct(item)}
        <div class="comp-ingredient">
          <div class="comp-ingredient-head">
            <span class="comp-ingredient-label">{item.label || item.column || ''}</span>
            <span class="comp-ingredient-score">{subPct}</span>
          </div>
          <div class="comp-ingredient-meta">
            {formatIngredientValue(item.column, item.game_value)} vs your avg {formatIngredientValue(item.column, item.baseline_value)}
          </div>
          <Meter value={subPct} ramp size="sm" />
        </div>
      {/each}
    </div>
  </Disclosure>
{:else}
  <div class="gr-score" title={hint || null}>
    <div class="gr-score-summary">
      <span class="gr-score-name">{name || 'Score'}</span>
      <span class="gr-score-value">{pct}</span>
      <Meter value={pct} ramp size="sm" --meter-grid-column="1 / -1" />
    </div>
  </div>
{/if}

<style>
  .gr-score {
    padding: 10px 12px;
    border: 1px solid var(--color-divider);
    border-radius: 10px;
    background: var(--color-surface-2);
  }
  .gr-score-summary {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
    gap: 2px 10px;
    align-items: baseline;
  }
  .gr-score-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text);
    min-width: 0;
  }
  .gr-score-value {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-neutral-400);
    font-variant-numeric: tabular-nums;
  }
  .gr-score-details {
    padding: 8px 12px 10px;
    display: grid;
    gap: 8px;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    background: rgba(0, 0, 0, 0.12);
  }
  .comp-ingredient { display: grid; gap: 2px; }
  .comp-ingredient-head {
    display: flex; justify-content: space-between; gap: 8px;
    font-size: 11px; font-weight: 600; color: var(--color-text);
  }
  .comp-ingredient-score { color: var(--color-text); }
  .comp-ingredient-meta {
    font-size: 10px; color: var(--color-neutral-400); margin: 2px 0 4px; line-height: 1.3;
  }
</style>
