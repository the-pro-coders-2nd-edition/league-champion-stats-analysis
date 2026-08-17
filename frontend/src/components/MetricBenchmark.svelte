<script>
  // benchmarkTone ('above'|'below'|'inline') and benchmarkColor (#rrggbb|'')
  // are per-instance report data (Jinja tokens here, not literals) -- vocabulary
  // enforced by the Python producer (bundles.py/peer/comparison.py).
  export let benchmark;
  export let benchmarkTone = 'inline';
  export let benchmarkColor = '';
</script>

<div class="card-benchmark card-benchmark--{benchmarkTone}" style="color: {benchmarkColor}">{benchmark}</div>

<style>
  :global(.card-benchmark) { margin-top: 6px; font-size: 11.5px; font-weight: 600; color: var(--muted); }
  :global(.card-benchmark--above) { color: var(--win); }
  :global(.card-benchmark--below) { color: var(--loss); }
</style>
