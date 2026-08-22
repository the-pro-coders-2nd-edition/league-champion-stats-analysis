<script>
  import { gameReviewScoreColor } from '../lib/metricColors.js';

  // value: number, 0-100. size: 'sm' | 'md' | 'lg'. tone: 'good' | 'warn' | 'bad' | 'flat' | 'solid' | null.
  // ramp: boolean. reference: number | null.
  export let value;
  export let size = 'md';
  export let tone = 'flat';
  export let ramp = false;
  export let reference = null;

  $: pct = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  $: fillColor = ramp ? gameReviewScoreColor(pct) : `var(--tone-${tone || 'flat'}-line)`;
</script>

<div class="meter meter--{size}" aria-hidden="true">
  <i class="meter-fill" style="width: {pct}%; background: {fillColor}"></i>
  {#if reference !== null}
    <i class="meter-tick" style="left: {Math.max(0, Math.min(100, reference))}%"></i>
  {/if}
</div>

<style>
  .meter {
    position: relative;
    margin: var(--meter-margin, 0);
    grid-column: var(--meter-grid-column, auto);
  }
  /* No overflow: hidden here -- the reference tick deliberately overhangs the track. */
  .meter--sm { height: 4px; border-radius: 2px; background: rgba(0, 0, 0, 0.28); }
  .meter--md { height: 6px; border-radius: 3px; background: var(--color-neutral-800); }
  .meter--lg { height: 8px; border-radius: 4px; background: var(--color-neutral-800); }

  .meter-fill {
    position: absolute;
    inset: 0 auto 0 0;
    display: block;
    /* Own radius (not inherited from the track) so a 2% score doesn't paint a ~6px pill. */
    border-radius: 0;
  }

  .meter-tick {
    position: absolute;
    width: 1px;
    display: block;
    background: var(--color-neutral-500);
  }
  .meter--sm .meter-tick { top: -2px; bottom: -2px; }
  .meter--md .meter-tick,
  .meter--lg .meter-tick { top: -3px; bottom: -3px; }
</style>
