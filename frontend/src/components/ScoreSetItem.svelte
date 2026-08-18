<script lang="ts">
  export let name: string;
  export let scoreLabel: string;
  export let scoreValue: number;
  // Resolved CSS color/gradient values, not a tone name -- the flat/good/warn/bad
  // branch (scoreColor === var(--color-text) when tone is 'flat') is decided by
  // the caller (Jinja token in manifest.json, or real JS in report.html) since
  // this component is SSR'd once per literal-token props, not per real value.
  export let valueColor: string;
  export let fillColor: string;
  export let verdict: string;
  export let sub: string = '';
  export let hint: string = '';
</script>

<div class="score-set-item" title={hint || null}>
  <div class="score-set-item-head">
    <span class="score-set-item-name">{name}</span>
    <span class="score-set-item-score" style="color: {valueColor}">{scoreLabel}</span>
  </div>
  <div class="score-set-item-bar">
    <i class="score-set-item-fill" style="width: {scoreValue}%; background: {fillColor}"></i>
    <i class="score-set-item-tick"></i>
  </div>
  <div class="score-set-item-foot">
    <span class="score-set-item-verdict" style="color: {valueColor}">{verdict}</span>
    {#if sub}<span class="score-set-item-sub">{sub}</span>{/if}
  </div>
</div>

<style>
  /* :global -- renderScoreSetItem() in report.html rebuilds this markup on
     queue/window filter changes, so these classes must not be scoped/hashed. */
  :global(.score-set-item-head) {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }
  :global(.score-set-item-name) {
    font-size: 13px;
    color: var(--color-neutral-300);
  }
  :global(.score-set-item-score) {
    margin-left: auto;
    font-family: var(--font-heading);
    font-weight: 700;
    font-size: 15px;
    font-variant-numeric: tabular-nums;
  }
  :global(.score-set-item-bar) {
    position: relative;
    margin-top: var(--space-2);
    height: 6px;
    border-radius: 3px;
    background: var(--color-neutral-800);
  }
  :global(.score-set-item-fill) {
    position: absolute;
    inset: 0 auto 0 0;
    border-radius: 3px;
    display: block;
  }
  :global(.score-set-item-tick) {
    position: absolute;
    top: -3px;
    bottom: -3px;
    left: 50%;
    width: 1px;
    background: var(--color-neutral-500);
    display: block;
  }
  :global(.score-set-item-foot) {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
    margin-top: var(--space-2);
  }
  :global(.score-set-item-verdict) {
    font-size: 11px;
  }
  :global(.score-set-item-sub) {
    margin-left: auto;
    font-size: 11px;
    color: var(--color-neutral-600);
    font-variant-numeric: tabular-nums;
  }
</style>
