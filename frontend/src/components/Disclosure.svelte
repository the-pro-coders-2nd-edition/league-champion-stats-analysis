<script lang="ts">
  // Real <details>/<summary> wrapper. Unifies the four disclosure sites that used to each
  // reimplement marker-hiding, list-style and a chevron: rec-evidence, form/peer all-metrics,
  // section-details and the game-review objective row.
  export let open: boolean = false;
  export let chevron: 'leading' | 'trailing' | 'none' = 'trailing';
  export let variant: string = '';

  let className = '';
  export { className as class };
</script>

<details class="disclosure disclosure--{variant} {className}" {open}>
  <summary class="disclosure-summary">
    {#if chevron === 'leading'}<span class="disclosure-chevron" aria-hidden="true"></span>{/if}
    <slot name="summary" />
    {#if chevron === 'trailing'}<span class="disclosure-chevron" aria-hidden="true"></span>{/if}
  </summary>
  <slot />
</details>

<style>
  .disclosure > summary {
    list-style: none;
    cursor: pointer;
    user-select: none;
  }
  .disclosure > summary::-webkit-details-marker { display: none; }
  .disclosure > summary:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
    border-radius: 4px;
  }
  .disclosure-chevron {
    display: inline-block;
    flex-shrink: 0;
    transition: transform 0.15s;
  }
  .disclosure-chevron::before { content: "▸"; }
  .disclosure[open] > summary .disclosure-chevron { transform: rotate(90deg); }

  /* rec-evidence: bare inline text link, no chevron, colour-only open state */
  .disclosure--evidence {
    margin-top: var(--space-4);
    padding-top: var(--space-3);
    border-top: 1px solid var(--color-divider);
  }
  .disclosure--evidence > summary {
    font-size: 11px;
    color: var(--color-neutral-600);
  }
  .disclosure--evidence[open] > summary { color: var(--color-accent); }

  /* section-details: pill-shaped button summary */
  .disclosure--pill { margin: 14px 0; }
  .disclosure--pill > summary {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-radius: 999px;
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
    font-size: 12.5px;
    font-weight: 600;
    color: var(--color-neutral-400);
  }
  .disclosure--pill > summary:hover { color: var(--color-text); border-color: var(--color-accent); }
  .disclosure--pill[open] > summary { color: var(--color-accent); margin-bottom: 10px; }
  .disclosure--pill .disclosure-chevron { color: var(--color-accent); font-size: 11px; }

  /* form/peer all-metrics: bordered box, leading chevron */
  .disclosure--box {
    margin-top: 14px;
    border: 1px solid var(--color-divider);
    border-radius: 12px;
    background: var(--color-surface-2);
    padding: 10px 14px;
  }
  .disclosure--box > summary {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--color-neutral-400);
  }
  .disclosure--box[open] > summary { color: var(--color-accent); margin-bottom: 10px; }
  .disclosure--box .disclosure-chevron { color: var(--color-accent); }

  /* game-review objective row: the summary IS the row layout, chevron is the 4th grid cell */
  .disclosure--objective > summary {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
  }
  .disclosure--objective .disclosure-chevron {
    color: var(--color-neutral-400);
    font-size: 11px;
  }
</style>
