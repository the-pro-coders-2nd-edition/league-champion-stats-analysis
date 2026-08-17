<script>
  // cellsHtml: one pre-assembled Jinja-token string covering every <td> in
  // the row -- per-table cell shapes vary too much (icons, tone colors,
  // dual-icon cores) for a single rigid {text, tone, ...} cell schema.
  export let cellsHtml;
</script>

<tr>{@html cellsHtml}</tr>
