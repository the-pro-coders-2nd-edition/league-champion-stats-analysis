<script>
  // Always-present structure only -- the outer .rec wrapper (with its
  // conditional anchor id) and the evidence <details> block stay as existing
  // Jinja in _macros.html's rec_card macro, same pattern as MetricCard's
  // outer .card wrapper. badge is the existing priority-badge vocabulary
  // ('high'|'medium'|'low'), not the design system's tone names.
  export let badge;
  export let priorityLabel;
  export let category;
  export let title;
  export let detail;
</script>

<div class="rec-head">
  <span class="badge badge--priority badge--{badge}">{priorityLabel}</span>
  <span class="badge">{category}</span>
  <strong>{title}</strong>
</div>
<p>{detail}</p>
