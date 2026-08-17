<script>
  // columns: {html, id?}[] -- real array, known at generation time (a table's
  // column set is static per section; only its rows vary per report).
  export let columns;
</script>

<thead><tr>{#each columns as col}<th id={col.id || ''}>{@html col.html}</th>{/each}</tr></thead>
