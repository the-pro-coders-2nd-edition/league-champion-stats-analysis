<script>
  // Covers the label + title + score chip + score bar, which are always set
  // together whenever a verdict exists (confirmed in
  // src/league_stats/pipeline/bundles.py's _build_section_verdicts: score,
  // text and label are populated in the same branch, never independently).
  // The outer .section-pulse wrapper (tone class, hidden attribute) and the
  // hint paragraph / fix link (genuinely independent per-instance optionals)
  // stay as Jinja in _macros.html's section_pulse macro.
  // Deliberately does NOT include the .section-pulse-callout wrapper -- the
  // optional hint paragraph (SectionPulseHint.svelte) is a sibling that has
  // to land *inside* that same div, so the wrapper's open/close tags stay in
  // the Jinja macro, same as every other "outer wrapper" in this port.
  export let label;
  export let title;
  export let score;
</script>

<span class="section-pulse-label">{label}</span>
<div class="section-pulse-head">
  <strong class="section-pulse-title">{title}</strong>
  <span class="section-pulse-score">{score}</span>
</div>
<div class="section-pulse-bar" aria-hidden="true"><i style="width: {score}%"></i></div>
