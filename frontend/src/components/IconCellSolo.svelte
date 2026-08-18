<script>
  // Icon-only cell with the name as a title tooltip (mirrors lib/html.js `soloIconCellHtml`).
  export let name = '';
  export let icon = null;
</script>

{#if !icon}
  —
{:else}
  <span class="icon-cell icon-cell--solo" title={name || ''}>
    <img src={icon} alt={name || ''} class="game-icon game-icon--sm">
  </span>
{/if}
