<script lang="ts">
  // Icon + visible name pair (used in data-table cells). Mirrors lib/html.js's iconCellHtml —
  // the two functions render different accessibility shapes (this one keeps the name visible,
  // IconOnly moves it into a title/alt tooltip), so they stay two components, not one `solo` flag.
  export let name: string;
  export let iconHref: string = '';
</script>

{#if iconHref}
  <span class="icon-cell">
    <img src={iconHref} alt="" class="game-icon game-icon--sm">
    <span>{name}</span>
  </span>
{:else}
  {name}
{/if}
