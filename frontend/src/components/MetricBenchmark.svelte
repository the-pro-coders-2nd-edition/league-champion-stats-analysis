<script>
  // Presence of this whole row is conditional on real per-instance report data
  // (card.benchmark), which the Jinja-token generation trick can't resolve --
  // a token string is always truthy at generation time. The {% if card.benchmark %}
  // wrap therefore stays in _macros.html's metric_card macro, around the
  // {% include %} of this generated snippet, not inside this component.
  // Preserves the existing benchmarkTone vocabulary ('above'|'below'|'inline'),
  // not the design system's good/warn/bad/flat/accent tone set.
  export let benchmark;
  export let benchmarkTone = 'inline';
  export let benchmarkColor = '';
</script>

<div class="card-benchmark card-benchmark--{benchmarkTone}" style="color: {benchmarkColor}">{benchmark}</div>
