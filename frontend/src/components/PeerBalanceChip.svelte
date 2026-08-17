<script>
  const MODIFIERS = ['above', 'below'];

  export let modifier;
  export let countId;
  export let count;
  export let label;

  if (!MODIFIERS.includes(modifier)) throw new Error(`PeerBalanceChip: invalid modifier "${modifier}"`);
</script>

<span class="peer-balance-chip peer-balance-chip--{modifier}">
  <strong id="{countId}">{count}</strong> {label}
</span>

<style>
  .peer-balance-chip {
    display: inline-flex; align-items: baseline; gap: 6px;
    font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 999px;
    background: var(--panel); color: var(--muted);
  }
  .peer-balance-chip strong { font-size: 16px; font-weight: 800; }
  .peer-balance-chip--above { color: var(--win); background: rgba(63, 182, 139, 0.12); }
  .peer-balance-chip--below { color: var(--loss); background: rgba(224, 85, 99, 0.12); }
</style>
