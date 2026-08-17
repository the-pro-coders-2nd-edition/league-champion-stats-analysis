<script>
  // See MetricBenchmark.svelte / PORT NOTES: this is a narrow extraction of
  // the existing metric_card macro's value row, not the full design-system
  // MetricCard. Preserves the existing valueClass vocabulary ('win'|'loss'|'').
  export let value;
  export let valueClass = '';
  export let valueColor = '';
</script>

<div class="value {valueClass}" style="color: {valueColor}">{value}</div>
