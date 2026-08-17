<script lang="ts">
  export let targetId: string;
  export let label: string;
</script>

<button type="button" class="rec-extend" data-target="{targetId}" data-more-label="{label}" aria-expanded="false" aria-controls="{targetId}">
  {label}
</button>

<style>
  :global(.rec-extend) {
    display: block; margin: 16px auto 0; padding: 10px 20px; border: 1px solid var(--color-divider);
    border-radius: 8px; background: var(--color-surface-2); color: var(--color-text); font: inherit;
    cursor: pointer;
  }
  :global(.rec-extend:hover) { border-color: var(--color-accent); color: var(--color-accent); }
  :global(.rec-extend.is-expanded) { margin-top: 8px; }
</style>
