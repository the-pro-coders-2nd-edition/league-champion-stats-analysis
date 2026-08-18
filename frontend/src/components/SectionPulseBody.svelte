<script lang="ts">
  export let label: string;
  export let title: string;
  export let score: number;
</script>

<span class="section-pulse-label">{label}</span>
<div class="section-pulse-head">
  <strong class="section-pulse-title">{title}</strong>
  <span class="section-pulse-score">{score}</span>
</div>
<div class="section-pulse-bar" aria-hidden="true">
  <i class="section-pulse-fill" style="width: {score}%"></i>
  <i class="section-pulse-tick"></i>
</div>

<style>
  :global(.section-pulse-label) {
    display: inline-flex; align-items: center; align-self: flex-start;
    width: fit-content; height: 19px; padding: 0 7px;
    border-radius: var(--radius-sm);
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    background: var(--tone-flat-soft); color: var(--tone-flat-fg);
  }
  :global(.section-pulse--strong .section-pulse-label) {
    background: var(--tone-good-soft); color: var(--tone-good-fg);
  }
  :global(.section-pulse--focus .section-pulse-label) {
    background: var(--tone-bad-soft); color: var(--tone-bad-fg);
  }
  :global(.section-pulse-head) {
    display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3);
    margin-top: var(--space-3);
  }
  :global(.section-pulse-title) {
    font-family: var(--font-heading); font-size: 17px; font-weight: 700; line-height: 1.3; min-width: 0;
  }
  :global(.section-pulse-score) {
    flex-shrink: 0; font-family: var(--font-heading); font-size: 23px; font-weight: 700;
    letter-spacing: -.02em; font-variant-numeric: tabular-nums; color: var(--color-text);
  }
  :global(.section-pulse--strong .section-pulse-score) { color: var(--tone-good-fg); }
  :global(.section-pulse--focus .section-pulse-score) { color: var(--tone-bad-fg); }
  :global(.section-pulse-bar) {
    position: relative; height: 6px; border-radius: 3px;
    background: var(--color-neutral-800); margin: var(--space-3) 0 var(--space-2);
  }
  :global(.section-pulse-fill) {
    position: absolute; inset: 0 auto 0 0; border-radius: 3px; display: block;
    background: var(--tone-flat-line);
  }
  :global(.section-pulse--strong .section-pulse-fill) { background: var(--tone-good-line); }
  :global(.section-pulse--focus .section-pulse-fill) { background: var(--tone-bad-line); }
  :global(.section-pulse-tick) {
    position: absolute; top: -3px; bottom: -3px; left: 50%; width: 1px;
    background: var(--color-neutral-500); display: block;
  }
</style>
