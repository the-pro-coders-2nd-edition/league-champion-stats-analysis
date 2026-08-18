<script lang="ts">
  type ValueClass = 'win' | 'loss' | '';

  export let value: string;
  export let valueClass: ValueClass = '';
  export let valueColor: `#${string}` | '' = '';
</script>

<div class="value {valueClass}" style="color: {valueColor}">{value}</div>

<style>
  /* :global -- .value is also written directly in GameReview.svelte's loadout cards, which
     are not instances of this component but must share this sizing/tone. */
  :global(.card .value) { font-size: 28px; font-weight: 700; margin-top: 6px; letter-spacing: -.02em; }
  :global(.card .value.win) { color: var(--tone-good-fg); }
  :global(.card .value.loss) { color: var(--tone-bad-fg); }
  :global(.card--headline .value) { font-size: 32px; }
</style>
