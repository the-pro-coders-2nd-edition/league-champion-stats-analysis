<script lang="ts">
  type Tone = 'good' | 'bad' | 'stat';

  export let tone: Tone;
  export let label: string;
  export let value: string;
  export let valueColor: `#${string}` | '' = '';
</script>

<span class="ui-chip ui-chip--{tone} hero-chip">
  <span class="hero-chip-label">{label}</span>
  <strong class="hero-chip-value" style="color: {valueColor}">{value}</strong>
</span>

<style>
  .hero-chip {
    display: inline-flex; align-items: baseline; gap: 6px;
    letter-spacing: normal; text-transform: none; font-weight: 500;
  }
  .hero-chip-label { color: var(--color-neutral-400); font-size: 11px; font-weight: 600; }
  .hero-chip-value { font-size: 13px; font-weight: 700; color: var(--color-text); }
</style>
