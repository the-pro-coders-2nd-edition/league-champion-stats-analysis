<script lang="ts">
  type Modifier = 'above' | 'below';

  export let modifier: Modifier;
  export let countId: string;
  export let count: number;
  export let label: Modifier;
</script>

<span class="peer-balance-chip peer-balance-chip--{modifier}">
  <strong id="{countId}">{count}</strong> {label}
</span>

<style>
  .peer-balance-chip {
    display: inline-flex; align-items: baseline; gap: 6px;
    font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 999px;
    background: var(--color-surface); color: var(--color-neutral-400);
  }
  .peer-balance-chip strong { font-size: 16px; font-weight: 800; }
  .peer-balance-chip--above { color: var(--color-good); background: rgba(63, 182, 139, 0.12); }
  .peer-balance-chip--below { color: var(--color-bad); background: rgba(224, 85, 99, 0.12); }
</style>
