<script lang="ts">
  type Tone = 'strong' | 'focus' | 'solid';

  export let name: string;
  export let scoreLabel: string;
  export let scoreValue: number;
  export let value: string;
  export let tone: Tone;
  export let verdict: string;
  export let hint: string;
</script>

<div class="comp comp--{tone}" title="{hint}">
  <span class="comp-verdict">{verdict}</span>
  <div class="comp-head">
    <span class="comp-name">{name}</span>
    <span class="comp-score">{scoreLabel}</span>
  </div>
  <div class="comp-value">{value}</div>
  <div class="bar"><i style="width: {scoreValue}%"></i></div>
</div>

<style>
  :global(.comp) {
    position: relative;
    background: var(--color-surface-2); border: 1px solid var(--color-divider);
    border-radius: 12px; padding: 14px 14px 12px;
  }
  :global(.comp--strong) {
    border-color: rgba(63, 182, 139, 0.45);
    background: linear-gradient(180deg, rgba(63, 182, 139, 0.12), var(--color-surface-2));
  }
  :global(.comp--focus) {
    border-color: rgba(224, 85, 99, 0.45);
    background: linear-gradient(180deg, rgba(224, 85, 99, 0.12), var(--color-surface-2));
  }
  :global(.comp--solid) {
    border-color: rgba(154, 160, 181, 0.40);
    background: linear-gradient(180deg, rgba(154, 160, 181, 0.10), var(--color-surface-2));
  }
  :global(.comp-verdict) {
    position: absolute; top: 10px; right: 10px;
    font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 999px;
    background: var(--color-surface); color: var(--color-neutral-400); border: 1px solid var(--color-divider);
  }
  :global(.comp--strong .comp-verdict) {
    color: var(--tone-good-fg); background: rgba(63, 182, 139, 0.14);
    border-color: rgba(63, 182, 139, 0.35);
  }
  :global(.comp--focus .comp-verdict) {
    color: var(--tone-bad-fg); background: rgba(224, 85, 99, 0.14);
    border-color: rgba(224, 85, 99, 0.35);
  }
  :global(.comp--solid .comp-verdict) {
    color: #b0b6c9; background: rgba(154, 160, 181, 0.12);
    border-color: rgba(154, 160, 181, 0.30);
  }
  :global(.comp-head) {
    display: flex; align-items: baseline; gap: 8px;
    padding-right: 78px;
  }
  :global(.comp-name) { font-size: 13px; font-weight: 600; color: var(--color-text); min-width: 0; }
  :global(.comp-score) { font-size: 13px; font-weight: 700; color: var(--color-accent); flex-shrink: 0; }
  :global(.comp-value) { font-size: 12px; color: var(--color-neutral-400); line-height: 1.35; margin-top: 4px; }
  :global(.comp .bar) {
    height: 6px; background: rgba(0, 0, 0, 0.28); border-radius: 3px;
    margin-top: 8px; overflow: hidden; container-type: inline-size;
  }
  :global(.comp .bar i) {
    display: block; height: 100%;
    background-image: var(--score-fill-gradient);
    background-size: 100cqw 100%;
    background-repeat: no-repeat;
  }
</style>
