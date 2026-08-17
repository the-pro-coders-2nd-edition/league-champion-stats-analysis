<script lang="ts">
  type DeltaClass = 'form-wr-delta--up' | 'form-wr-delta--down' | '';

  export let from: string;
  export let to: string;
  export let deltaClass: DeltaClass = '';
  export let deltaText: string;
</script>

<span class="form-wr-from">{from}</span>
<span class="form-wr-arrow" aria-hidden="true">→</span>
<span class="form-wr-to">{to}</span>
<span class="form-wr-delta {deltaClass}">{deltaText}</span>

<style>
  :global(.form-wr-from) { font-size: 22px; color: var(--muted); }
  :global(.form-wr-arrow) { color: var(--muted); font-size: 16px; }
  :global(.form-wr-to) { font-size: 28px; color: var(--text); letter-spacing: -.02em; }
  :global(.form-wr-delta) {
    font-size: 13px; font-weight: 700; padding: 3px 8px; border-radius: 999px;
    background: var(--panel); color: var(--muted);
  }
  :global(.form-wr-delta--up) { color: var(--win); background: rgba(63, 182, 139, 0.12); }
  :global(.form-wr-delta--down) { color: var(--loss); background: rgba(224, 85, 99, 0.12); }
</style>
