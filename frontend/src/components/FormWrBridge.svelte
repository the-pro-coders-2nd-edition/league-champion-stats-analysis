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
  .form-wr-from { font-size: 22px; color: var(--color-neutral-400); }
  .form-wr-arrow { color: var(--color-neutral-400); font-size: 16px; }
  .form-wr-to { font-size: 28px; color: var(--color-text); letter-spacing: -.02em; }
  .form-wr-delta {
    font-size: 13px; font-weight: 700; padding: 3px 8px; border-radius: 999px;
    background: var(--color-surface); color: var(--color-neutral-400);
  }
  .form-wr-delta--up { color: var(--tone-good-fg); background: var(--win-tint-12); }
  .form-wr-delta--down { color: var(--tone-bad-fg); background: var(--loss-tint-12); }
</style>
