<script>
  // columns is a REAL array (not a Jinja token) -- a table's column set is
  // static per section, known at generation time, unlike its rows which vary
  // per report. Each entry is pre-rendered header HTML (may itself contain an
  // icon+label macro call already evaluated by the generation script's
  // manifest, or a literal Jinja token for the rare per-report-data header
  // like the peer comparison's tier name).
  // {html, id?}[] -- id is optional, used by columns whose text is updated
  // by client-side JS on filter change (e.g. the peer table's tier-name
  // header). Empty id="" on columns without one is harmless.
  export let columns;
</script>

<thead><tr>{#each columns as col}<th id={col.id || ''}>{@html col.html}</th>{/each}</tr></thead>
