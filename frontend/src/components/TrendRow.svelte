<script lang="ts">
  // Shared by RankPeers ("peer-driver" rows: peer comparison) and FormTracker
  // ("form-mover" rows: recent vs. baseline) — same markup/CSS, different class
  // prefix and tone-modifier vocabulary, so both keep their existing selectors.
  type BlockClass = 'peer-driver' | 'form-mover';
  type Tone = 'positive' | 'negative';

  export let blockClass: BlockClass;
  export let tone: Tone;
  export let label: string;
  export let values: string;
  export let gap: string;
  export let gapColor: `#${string}` | '' = '';

  const TONE_MODIFIER = {
    'peer-driver': { positive: 'above', negative: 'below' },
    'form-mover': { positive: 'improved', negative: 'regressed' },
  };

  $: modifier = TONE_MODIFIER[blockClass][tone];
</script>

<div class="{blockClass} {blockClass}--{modifier}">
  <span class="{blockClass}-label">{@html label}</span>
  <span class="{blockClass}-values">{values}</span>
  <span class="{blockClass}-gap" style="color: {gapColor}">{gap}</span>
</div>

<style>
  :global(.peer-driver),
  :global(.form-mover) {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) auto;
    gap: 8px 12px;
    align-items: center;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    border-left-width: 3px;
    background: var(--panel-2);
  }
  :global(.peer-driver--above),
  :global(.form-mover--improved) {
    border-left-color: var(--win);
    background: var(--win-tint-06);
  }
  :global(.peer-driver--below),
  :global(.form-mover--regressed) {
    border-left-color: var(--loss);
    background: var(--loss-tint-06);
  }
  :global(.peer-driver-label),
  :global(.form-mover-label) { font-size: 13px; font-weight: 600; min-width: 0; }
  :global(.peer-driver-values),
  :global(.form-mover-values) { font-size: 12px; color: var(--muted); }
  :global(.peer-driver-gap),
  :global(.form-mover-gap) { font-size: 14px; font-weight: 700; white-space: nowrap; }
</style>
