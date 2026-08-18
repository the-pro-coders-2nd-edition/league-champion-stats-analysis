<script lang="ts">
  export let targetId: string;
  export let label: string;
  export let expanded: boolean = false;
</script>

<button
  type="button"
  class="rec-extend"
  class:is-expanded={expanded}
  data-target="{targetId}"
  data-more-label="{label}"
  aria-expanded={expanded}
  aria-controls="{targetId}"
  on:click
>
  {expanded ? 'Show less' : label}
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
