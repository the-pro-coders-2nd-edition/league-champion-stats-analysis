<script>
  import IconCellSolo from './IconCellSolo.svelte';

  export let names = [];
  export let icons = [];
  export let emptyLabel = '—';
</script>

{#if !(names || []).length}
  <span class="game-review-rune-empty">{emptyLabel || '—'}</span>
{:else}
  {#each names as name, index}
    <IconCellSolo {name} icon={icons[index] || null} />
  {/each}
{/if}
