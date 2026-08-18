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
    border: 1px solid var(--border); background: var(--panel-2); color: var(--text);
    border-radius: 999px; padding: 8px 14px; font: inherit; font-size: 13px; cursor: pointer;
  }
  :global(.game-window-btn:hover:not(:disabled)) { border-color: var(--accent); color: var(--accent); }
  :global(.game-window-btn.is-active) { background: var(--accent); border-color: var(--accent); color: #fff; }
  :global(.game-window-btn:disabled) { opacity: .45; cursor: not-allowed; }
  :global(.filter-group.is-muted .game-window-btn) { cursor: not-allowed; }
</style>
