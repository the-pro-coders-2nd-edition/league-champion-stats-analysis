<script>
  // Presence is conditional on real per-instance data (rec.evidence_summary),
  // so the {% if %} wrap stays in the Jinja macro, not in this component --
  // see MetricBenchmark.svelte for the same reasoning.
  export let text;
</script>

<p class="rec-evidence-summary">{text}</p>
