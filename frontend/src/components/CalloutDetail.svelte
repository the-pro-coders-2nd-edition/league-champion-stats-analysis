<script>
  // Presence conditional on real data (parts|length > 1 in the Jinja macro),
  // stays wrapped there -- see MetricBenchmark.svelte for the reasoning.
  export let detail;
</script>

<span class="peer-callout-detail">{detail}</span>
