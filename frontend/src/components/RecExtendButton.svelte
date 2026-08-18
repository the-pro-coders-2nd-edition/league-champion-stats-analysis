<script lang="ts">
  export let targetId: string;
  export let label: string;
</script>

<button type="button" class="rec-extend" data-target="{targetId}" data-more-label="{label}" aria-expanded="false" aria-controls="{targetId}">
  {label}
</button>

<style>
  :global(.rec-extend) {
    display: block; margin: 16px auto 0; padding: 10px 20px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--panel-2); color: var(--text); font: inherit;
    cursor: pointer;
  }
  :global(.rec-extend:hover) { border-color: var(--accent); color: var(--accent); }
  :global(.rec-extend.is-expanded) { margin-top: 8px; }
</style>
