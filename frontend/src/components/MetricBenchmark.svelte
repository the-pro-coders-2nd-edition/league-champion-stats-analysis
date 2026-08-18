<script lang="ts">
  type BenchmarkTone = 'above' | 'below' | 'inline';

  export let benchmark: string;
  export let benchmarkTone: BenchmarkTone = 'inline';
  export let benchmarkColor: `#${string}` | '' = '';
</script>

<div class="card-benchmark card-benchmark--{benchmarkTone}" style="color: {benchmarkColor}">{benchmark}</div>

<style>
  .card-benchmark { margin-top: 6px; font-size: 11.5px; font-weight: 600; color: var(--color-neutral-400); }
  .card-benchmark--above { color: var(--tone-good-fg); }
  .card-benchmark--below { color: var(--tone-bad-fg); }
</style>
