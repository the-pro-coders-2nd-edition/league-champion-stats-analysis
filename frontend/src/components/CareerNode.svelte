<script lang="ts">
  import Pill from './Pill.svelte';

  export let state: string;
  export let stateClass: string;
  export let tone: string;
  export let pct: string;
  export let mark: string;
  export let text: string;
  export let note: string;
  export let count: string;
  export let compact: boolean = false;
  export let last: boolean = false;
</script>

<div class="career-node{compact ? ' career-node--compact' : ''}{last ? ' career-node--last' : ''}">
  <div class="career-rail">
    <div class="career-ring career-ring--{stateClass}" style="--career-pct: {pct}%">
      <div class="career-mark career-mark--{stateClass}">{mark}</div>
    </div>
    <i class="career-connector"></i>
  </div>
  <div class="career-body">
    {#if !compact}
    <div class="career-head">
      <Pill tone={tone} label={state} />
      <span class="career-count">{count}</span>
    </div>
    {/if}
    <div class="career-text career-text--{stateClass}">{text}</div>
    <div class="career-note">{note}</div>
  </div>
</div>

<style>
  .career-node {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr);
    gap: 10px;
    align-items: start;
    color: var(--color-text);
  }
  .career-node--compact { grid-template-columns: 30px minmax(0, 1fr); }

  .career-rail { display: grid; justify-items: center; gap: 0; }

  /* :global -- CareerMode.svelte's legend row builds its own .career-ring/.career-mark markup
     directly (not through this component) to preview all five states side by side, so this
     whole ring/mark/connector family must resolve without the scoping hash. */
  :global(.career-ring) {
    width: 36px; height: 36px; border-radius: 50%;
    display: grid; place-items: center;
    background: transparent;
  }
  .career-node--compact :global(.career-ring) { width: 28px; height: 28px; }

  :global(.career-ring--cleared) { background: var(--tone-good-line); }
  :global(.career-ring--in-progress) {
    background: conic-gradient(var(--tone-warn-line) 0 var(--career-pct), var(--color-neutral-800) 0);
  }
  :global(.career-ring--at-risk) {
    background: conic-gradient(var(--tone-warn-line) 0 var(--career-pct), var(--tone-good-mid) 0);
    border: 1px dashed var(--tone-warn-line);
  }
  :global(.career-ring--revoked) {
    background: conic-gradient(var(--tone-bad-line) 0 var(--career-pct), var(--color-neutral-800) 0);
    border: 1px dashed var(--tone-bad-line);
  }
  :global(.career-ring--locked) { border: 1px dashed var(--color-neutral-700); }

  :global(.career-mark) {
    width: 27px; height: 27px; border-radius: 50%;
    background: var(--color-surface);
    display: grid; place-items: center;
    font-size: 10px; font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--tone-flat-fg);
  }
  .career-node--compact :global(.career-mark) { width: 21px; height: 21px; font-size: 9px; }
  :global(.career-mark--cleared) { color: var(--tone-good-fg); }
  :global(.career-mark--in-progress), :global(.career-mark--at-risk) { color: var(--tone-warn-fg); }
  :global(.career-mark--revoked) { color: var(--tone-bad-fg); }
  :global(.career-mark--locked) { color: var(--color-neutral-600); }

  /* :global -- report.css:1541 (.career-legend-row .career-connector) also targets this class. */
  :global(.career-connector) {
    width: 1px; height: 22px;
    background: var(--color-neutral-800);
    display: block;
  }
  .career-node--compact :global(.career-connector) { height: 20px; }
  .career-node--last :global(.career-connector) { height: 0; }

  .career-body { padding-bottom: 18px; min-width: 0; }
  .career-head {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin-bottom: 6px;
  }
  .career-count {
    font-size: 11px; color: var(--color-neutral-600);
    font-variant-numeric: tabular-nums;
  }
  .career-text { font-size: 12px; line-height: 1.45; text-wrap: pretty; }
  .career-text--locked { color: var(--color-neutral-600); }
  .career-note {
    margin-top: 2px; font-size: 11px;
    color: var(--color-neutral-600); line-height: 1.5; text-wrap: pretty;
  }
</style>
