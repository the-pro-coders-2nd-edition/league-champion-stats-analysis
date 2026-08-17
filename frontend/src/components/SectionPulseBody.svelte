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
<div class="section-pulse-bar" aria-hidden="true"><i style="width: {score}%"></i></div>

<style>
  :global(.section-pulse-label) {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--color-neutral-400);
  }
  :global(.section-pulse--strong .section-pulse-label) { color: var(--tone-good-fg); }
  :global(.section-pulse--focus .section-pulse-label) { color: var(--tone-bad-fg); }
  :global(.section-pulse--solid .section-pulse-label) { color: #b0b6c9; }
  :global(.section-pulse-head) {
    display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  }
  :global(.section-pulse-title) { font-size: 14px; font-weight: 700; line-height: 1.3; min-width: 0; }
  :global(.section-pulse-score) {
    flex-shrink: 0; font-size: 15px; font-weight: 800; letter-spacing: -.02em; color: var(--color-text);
  }
  :global(.section-pulse-bar) {
    height: 6px; background: rgba(0, 0, 0, 0.28); border-radius: 3px;
    margin-top: 2px; overflow: hidden; container-type: inline-size;
  }
  :global(.section-pulse-bar i) {
    display: block; height: 100%;
    background-image: var(--score-fill-gradient);
    background-size: 100cqw 100%;
    background-repeat: no-repeat;
  }
</style>
