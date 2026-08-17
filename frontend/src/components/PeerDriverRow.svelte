<script lang="ts">
  type Tone = 'above' | 'below';

  export let tone: Tone;
  export let label: string;
  export let values: string;
  export let gap: string;
  export let gapColor: `#${string}` | '' = '';
</script>

<div class="peer-driver peer-driver--{tone}">
  <span class="peer-driver-label">{label}</span>
  <span class="peer-driver-values">{values}</span>
  <span class="peer-driver-gap" style="color: {gapColor}">{gap}</span>
</div>

<style>
  :global(.peer-driver) {
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
  :global(.peer-driver--above) {
    border-left-color: var(--color-good-line);
    background: rgba(63, 182, 139, 0.06);
  }
  :global(.peer-driver--below) {
    border-left-color: var(--color-bad-line);
    background: rgba(224, 85, 99, 0.06);
  }
  :global(.peer-driver-label) { font-size: 13px; font-weight: 600; min-width: 0; }
  :global(.peer-driver-values) { font-size: 12px; color: var(--color-neutral-400); }
  :global(.peer-driver-gap) { font-size: 14px; font-weight: 700; white-space: nowrap; }
</style>
