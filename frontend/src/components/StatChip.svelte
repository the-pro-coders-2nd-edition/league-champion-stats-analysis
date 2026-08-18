<script lang="ts">
  // RFC-001 chip unification: the stat/metric-value sibling of Chip.svelte.
  // Replaces HeroChip (hero overview mini-chips) and PeerBalanceChip (rank
  // peer above/below counts) -- both were "label + big value" chips with a
  // tone, just styled differently (baseline vs. pill-surface layout).
  type Tone = 'good' | 'warn' | 'bad' | 'flat' | 'info' | 'plan' | 'note';

  export let tone: Tone = 'flat';
  export let label: string = '';
  export let value: string | number = '';
  export let valueColor: string = '';
  export let icon: string = '';
  export let pill: boolean = false;
  export let id: string = '';
  export let title: string = '';
</script>

<span
  class="stat-chip"
  class:stat-chip--pill={pill}
  data-tone={tone}
  id={id || null}
  title={title || null}
>
  {#if icon}<img src={icon} alt="" class="stat-chip__icon" />{/if}
  {#if pill}
    <strong class="stat-chip__value" style={valueColor ? `color: ${valueColor}` : null}>{value}</strong>
    <span class="stat-chip__label">{label}</span>
  {:else}
    <span class="stat-chip__label">{label}</span>
    <strong class="stat-chip__value" style={valueColor ? `color: ${valueColor}` : null}>{value}</strong>
  {/if}
</span>

<style>
  :global(.stat-chip) {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    letter-spacing: normal;
    text-transform: none;
    font-weight: 500;
  }
  :global(.stat-chip__icon) {
    width: 16px;
    height: 16px;
    object-fit: contain;
    flex: none;
  }
  :global(.stat-chip__label) {
    color: var(--color-neutral-400);
    font-size: 11px;
    font-weight: 600;
  }
  :global(.stat-chip__value) {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text);
  }

  :global(.stat-chip[data-tone="good"] .stat-chip__value) { color: var(--tone-good-fg); }
  :global(.stat-chip[data-tone="warn"] .stat-chip__value) { color: var(--tone-warn-fg); }
  :global(.stat-chip[data-tone="bad"] .stat-chip__value) { color: var(--tone-bad-fg); }
  :global(.stat-chip[data-tone="info"] .stat-chip__value) { color: var(--tone-info-fg); }
  :global(.stat-chip[data-tone="plan"] .stat-chip__value) { color: var(--tone-plan-fg); }
  :global(.stat-chip[data-tone="note"] .stat-chip__value) { color: var(--tone-note-fg); }

  /* pill: the padded, rounded "count vs modifier" surface used by rank-peer
     above/below balance -- distinct from the plain baseline hero-chip layout. */
  :global(.stat-chip--pill) {
    padding: 6px 10px;
    border-radius: 999px;
    background: var(--color-surface);
    color: var(--color-neutral-400);
  }
  :global(.stat-chip--pill .stat-chip__label) {
    color: inherit;
    font-size: 12px;
    font-weight: 600;
  }
  :global(.stat-chip--pill .stat-chip__value) {
    font-size: 16px;
    font-weight: 800;
  }
  :global(.stat-chip--pill[data-tone="good"]) { color: var(--tone-good-fg); background: var(--win-tint-12); }
  :global(.stat-chip--pill[data-tone="bad"]) { color: var(--tone-bad-fg); background: rgba(224, 85, 99, 0.12); }
  :global(.stat-chip--pill[data-tone="good"] .stat-chip__value) { color: var(--tone-good-fg); }
  :global(.stat-chip--pill[data-tone="bad"] .stat-chip__value) { color: var(--tone-bad-fg); }
</style>
