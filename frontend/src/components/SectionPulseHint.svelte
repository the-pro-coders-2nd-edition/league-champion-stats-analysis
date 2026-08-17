<script>
  // Presence conditional on real data (v.hint or v.value), stays wrapped in
  // Jinja -- see MetricBenchmark.svelte for the same reasoning.
  export let text;
</script>

<p class="section-pulse-hint">{text}</p>
