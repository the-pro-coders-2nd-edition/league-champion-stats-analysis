<script>
  import IconCellSolo from './IconCellSolo.svelte';

  export let names = [];
  export let icons = [];
  export let tone = '';
</script>

{#if !(names || []).length}
  <span class="game-review-champ-empty">—</span>
{:else}
  <span class="game-review-champ-stack game-review-champ-stack--{tone}">
    {#each names as name, index}
      <IconCellSolo {name} icon={icons[index] || null} />
    {/each}
  </span>
{/if}
