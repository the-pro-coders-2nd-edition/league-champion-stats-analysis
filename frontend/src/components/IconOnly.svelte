<script lang="ts">
  // Icon-only variant with the name as a title tooltip instead of a visible span (used for
  // game-review rails/pins where space is tight). Mirrors lib/html.js's soloIconCellHtml.
  export let name: string;
  export let iconHref: string = '';
</script>

{#if iconHref}
  <span class="icon-cell icon-cell--solo" title={name || ''}>
    <img src={iconHref} alt={name || ''} class="game-icon game-icon--sm">
  </span>
{:else}
  —
{/if}
