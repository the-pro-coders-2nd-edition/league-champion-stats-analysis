<script lang="ts">
  type Tone = 'improved' | 'regressed';

  export let tone: Tone;
  export let label: string;
  export let values: string;
  export let gap: string;
  export let gapColor: `#${string}` | '' = '';
</script>

<div class="form-mover form-mover--{tone}">
  <span class="form-mover-label">{label}</span>
  <span class="form-mover-values">{values}</span>
  <span class="form-mover-gap" style="color: {gapColor}">{gap}</span>
</div>

<style>
  :global(.form-mover) {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) auto;
    gap: 8px 12px;
    align-items: center;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--color-divider);
    border-left-width: 3px;
    background: var(--color-surface-2);
  }
  :global(.form-mover--improved) {
    border-left-color: var(--tone-good-line);
    background: rgba(63, 182, 139, 0.06);
  }
  :global(.form-mover--regressed) {
    border-left-color: var(--tone-bad-line);
    background: rgba(224, 85, 99, 0.06);
  }
  :global(.form-mover-label) { font-size: 13px; font-weight: 600; min-width: 0; }
  :global(.form-mover-values) { font-size: 12px; color: var(--color-neutral-400); }
  :global(.form-mover-gap) { font-size: 14px; font-weight: 700; white-space: nowrap; }
</style>
