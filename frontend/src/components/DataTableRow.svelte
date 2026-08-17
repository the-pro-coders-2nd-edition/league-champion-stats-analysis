<script>
  // cellsHtml is ONE pre-assembled Jinja-token string covering every <td> in
  // the row (built in the manifest from the table's existing per-cell
  // rendering -- metric_label/core_cell/icon_cell macros, delta-tone
  // classes, inline color overrides, whatever that specific table needs).
  // This is deliberately not a per-cell {#each}: real per-table cells vary
  // too much in shape (icons, tone colors, dual-icon cores) to force through
  // one rigid {text, tone, ...} schema without losing existing behavior.
  // {% for row in rows %} stays in Jinja since row count is real per-report
  // data, unlike the fixed cell count within one row.
  export let cellsHtml;
</script>

<tr>{@html cellsHtml}</tr>
