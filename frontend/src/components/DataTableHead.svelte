<script lang="ts">
  type Column = { html: string; id?: string };

  export let columns: Column[];
</script>

<thead><tr>{#each columns as col}<th id={col.id || ''}>{@html col.html}</th>{/each}</tr></thead>
