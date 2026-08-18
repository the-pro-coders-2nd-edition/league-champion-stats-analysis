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
  .peer-driver,
  .form-mover {
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
  .peer-driver--above,
  .form-mover--improved {
    border-left-color: var(--win);
    background: var(--win-tint-06);
  }
  .peer-driver--below,
  .form-mover--regressed {
    border-left-color: var(--loss);
    background: var(--loss-tint-06);
  }
  .peer-driver-label,
  .form-mover-label { font-size: 13px; font-weight: 600; min-width: 0; }
  .peer-driver-values,
  .form-mover-values { font-size: 12px; color: var(--muted); }
  .peer-driver-gap,
  .form-mover-gap { font-size: 14px; font-weight: 700; white-space: nowrap; }

  /* Moved from report.css: co-locating with the (now scoped) base selector keeps this override
     from losing a specificity fight it cannot win once .peer-driver/.form-mover are scoped
     (report.css:342-346 / :783-787 previously relied on source order at equal specificity —
     already dead before this change, per RFC-001 step 21). */
  @media (max-width: 860px) {
    .peer-driver,
    .form-mover {
      grid-template-columns: 1fr auto;
    }
    .peer-driver-values,
    .form-mover-values { grid-column: 1 / -1; }
  }
</style>
