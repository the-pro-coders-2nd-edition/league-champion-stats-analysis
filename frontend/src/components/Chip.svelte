<script lang="ts">
  // RFC-001 chip unification: replaces Pill/UiChipBadge/HeroChip/PeerBalanceChip
  // with one component. 'accent' was deprecated and dropped from the tone set --
  // see frontend/src/styles/design-tokens.css for the token values per tone.
  type Tone = 'good' | 'warn' | 'bad' | 'flat' | 'info' | 'plan' | 'note';
  type Density = 'compact' | 'normal';

  export let tone: Tone = 'flat';
  export let fill: boolean = true;
  export let bordered: boolean = false;
  export let density: Density = 'normal';
  export let caps: boolean = false;
  export let dot: boolean = false;
  export let label: string = '';
  export let title: string = '';
  export let id: string = '';
</script>

<span
  class="chip"
  class:chip--compact={density === 'compact'}
  class:chip--caps={caps}
  data-tone={tone}
  data-fill={fill}
  data-bordered={bordered}
  id={id || null}
  title={title || null}
>
  {#if dot}<i class="chip__dot"></i>{/if}<span class="chip__label">{label}</span>
</span>

<style>
  /* :global -- some callers still build raw markup around this class name
     (see report.css's shared chip rules), so selectors must resolve without
     Svelte's per-component scoping hash. */
  :global(.chip) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 19px;
    padding: 0 7px;
    border-radius: calc(var(--radius-md) * 0.75);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
    white-space: nowrap;
    border: 1px solid transparent;
    background: transparent;
  }
  :global(.chip--compact) {
    height: 16px;
    padding: 0 6px;
    font-size: 10px;
  }
  :global(.chip--caps) {
    text-transform: uppercase;
  }
  :global(.chip__dot) {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  :global(.chip[data-tone="good"]) { color: var(--tone-good-fg); }
  :global(.chip[data-tone="warn"]) { color: var(--tone-warn-fg); }
  :global(.chip[data-tone="bad"]) { color: var(--tone-bad-fg); }
  :global(.chip[data-tone="flat"]) { color: var(--tone-flat-fg); }
  :global(.chip[data-tone="info"]) { color: var(--tone-info-fg); }
  :global(.chip[data-tone="plan"]) { color: var(--tone-plan-fg); }
  :global(.chip[data-tone="note"]) { color: var(--tone-note-fg); }

  :global(.chip[data-tone="good"] .chip__dot) { background: var(--tone-good-line); }
  :global(.chip[data-tone="warn"] .chip__dot) { background: var(--tone-warn-line); }
  :global(.chip[data-tone="bad"] .chip__dot) { background: var(--tone-bad-line); }
  :global(.chip[data-tone="flat"] .chip__dot) { background: var(--tone-flat-line); }
  :global(.chip[data-tone="info"] .chip__dot) { background: var(--tone-info-line); }
  :global(.chip[data-tone="plan"] .chip__dot) { background: var(--tone-plan-line); }
  :global(.chip[data-tone="note"] .chip__dot) { background: var(--tone-note-line); }

  :global(.chip[data-fill="true"][data-tone="good"]) { background: var(--tone-good-soft); }
  :global(.chip[data-fill="true"][data-tone="warn"]) { background: var(--tone-warn-soft); }
  :global(.chip[data-fill="true"][data-tone="bad"]) { background: var(--tone-bad-soft); }
  :global(.chip[data-fill="true"][data-tone="flat"]) { background: var(--tone-flat-soft); }
  :global(.chip[data-fill="true"][data-tone="info"]) { background: var(--tone-info-soft); }
  :global(.chip[data-fill="true"][data-tone="plan"]) { background: var(--tone-plan-soft); }
  :global(.chip[data-fill="true"][data-tone="note"]) { background: var(--tone-note-soft); }

  :global(.chip[data-bordered="true"][data-tone="good"]) { border-color: var(--tone-good-line); }
  :global(.chip[data-bordered="true"][data-tone="warn"]) { border-color: var(--tone-warn-line); }
  :global(.chip[data-bordered="true"][data-tone="bad"]) { border-color: var(--tone-bad-line); }
  :global(.chip[data-bordered="true"][data-tone="flat"]) { border-color: var(--tone-flat-line); }
  :global(.chip[data-bordered="true"][data-tone="info"]) { border-color: var(--tone-info-line); }
  :global(.chip[data-bordered="true"][data-tone="plan"]) { border-color: var(--tone-plan-line); }
  :global(.chip[data-bordered="true"][data-tone="note"]) { border-color: var(--tone-note-line); }
</style>
