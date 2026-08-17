<script lang="ts">
  type Kind = 'keep' | 'fix' | 'watch';

  export let kind: Kind = 'keep';
  export let label: string;
  export let title: string;
  export let text: string;
  export let meta: string = '';
</script>

<div class="callout callout--{kind}">
  <span class="callout-label">{label}</span>
  <strong class="callout-title">{title}</strong>
  <p class="callout-text">{text}</p>
  {#if meta}<span class="callout-meta">{meta}</span>{/if}
</div>

<style>
  .callout {
    background: var(--color-surface);
    box-shadow: var(--shadow-sm);
    border-radius: var(--radius-md);
    padding: 16px 20px;
    display: grid;
    gap: 8px;
  }
  .callout-label {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
  }
  .callout--keep .callout-label { color: var(--color-good); }
  .callout--fix .callout-label { color: var(--color-bad); }
  .callout--watch .callout-label { color: var(--color-warn); }
  .callout-title {
    font-size: 14px;
  }
  .callout-text {
    font-size: 13px;
    color: var(--color-neutral-400);
    margin: 0;
  }
  .callout-meta {
    font-size: 12px;
    color: var(--color-neutral-600);
    font-variant-numeric: tabular-nums;
  }
</style>
