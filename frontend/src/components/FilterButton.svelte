<script lang="ts">
  type DataAttr = 'data-queue' | 'data-window';

  export let dataAttr: DataAttr;
  export let buttonClass: string;
  export let value: string;
  export let label: string;
  export let activeClass: string = '';
  export let disabled: boolean = false;
</script>

<button
  type="button"
  class="{buttonClass}{activeClass ? ' ' + activeClass : ''}"
  data-queue={dataAttr === 'data-queue' ? value : null}
  data-window={dataAttr === 'data-window' ? value : null}
  disabled={disabled}
>{label}</button>

<style>
  :global(.game-window-btn) {
    border: 1px solid var(--color-divider); background: var(--color-surface-2); color: var(--color-text);
    border-radius: 999px; padding: 8px 14px; font: inherit; font-size: 13px; cursor: pointer;
  }
  :global(.game-window-btn:hover:not(:disabled)) { border-color: var(--color-accent); color: var(--color-accent); }
  :global(.game-window-btn.is-active) { background: var(--color-accent); border-color: var(--color-accent); color: #fff; }
  :global(.game-window-btn:disabled) { opacity: .45; cursor: not-allowed; }
  :global(.filter-group.is-muted .game-window-btn) { cursor: not-allowed; }
</style>
