<script lang="ts">
  export let roleIconHtml: string = '';
  export let iconHtml: string = '';
  export let label: string;
</script>

<span class="metric-label">{@html roleIconHtml}{@html iconHtml}<span>{label}</span></span>

<style>
  /* :global -- .metric-label is also written directly (not through this component) by
     DataTable.svelte and MetricDeltaTable.svelte's slotted cell markup; scoping here would
     leave those call sites unstyled. */
  :global(.metric-label) { display: inline-flex; align-items: center; gap: 6px; }
  :global(.metric-card-label .metric-label) { min-width: 0; }
</style>
