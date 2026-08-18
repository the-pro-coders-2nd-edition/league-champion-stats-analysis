<script>
  import IconCellSolo from './IconCellSolo.svelte';

  export let build = {};

  $: spells = build.summoners || [];
  $: icons = build.summoner_icons || [];
</script>

{#if !spells.length}
  —
{:else}
  <span class="game-review-summoners">
    {#each spells as name, index}
      {#if index > 0}<span class="game-review-summoner-sep" aria-hidden="true">+</span>{/if}
      <IconCellSolo {name} icon={icons[index] || null} />
    {/each}
  </span>
{/if}
