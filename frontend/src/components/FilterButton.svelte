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
  on:click
>{label}</button>

<style>
  :global(.game-window-btn) {
    border: 1px solid transparent; background: var(--color-surface-2); color: var(--color-neutral-500);
    border-radius: 999px; padding: 6px 12px; font: inherit; font-size: 12px; font-weight: 600;
    cursor: pointer; transition: color .15s, border-color .15s, background .15s;
  }
  :global(.game-window-btn:hover:not(:disabled)) { color: var(--color-text); }
  :global(.game-window-btn.is-active) {
    background: transparent; border-color: var(--color-neutral-500); color: var(--color-neutral-100);
  }
  :global(.game-window-btn:disabled) { opacity: .45; cursor: not-allowed; }
  :global(.filter-group.is-muted .game-window-btn) { cursor: not-allowed; }
</style>
