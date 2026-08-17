<script>
  // valueClass ('win'|'loss'|'') and valueColor (#rrggbb|'') are per-instance
  // report data (Jinja tokens at generation time, not literals here) -- their
  // vocabulary is enforced by the Python producer (view_models.py), not
  // validatable at generation time.
  export let value;
  export let valueClass = '';
  export let valueColor = '';
</script>

<div class="value {valueClass}" style="color: {valueColor}">{value}</div>

<style>
  :global(.card .value) { font-size: 28px; font-weight: 700; margin-top: 6px; letter-spacing: -.02em; }
  :global(.card .value.win) { color: var(--win); }
  :global(.card .value.loss) { color: var(--loss); }
  :global(.card--headline .value) { font-size: 32px; }
</style>
