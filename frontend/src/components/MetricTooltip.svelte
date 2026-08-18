<script lang="ts">
  export let label: string;
  export let tooltip: string;

  let open = false;

  function toggle() {
    open = !open;
  }

  function close() {
    open = false;
  }

  function handleWindowKeydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape') close();
  }

  function clickOutside(node: HTMLElement, onOutside: () => void) {
    function handleDocumentClick(event: MouseEvent) {
      if (!node.contains(event.target as Node)) onOutside();
    }
    document.addEventListener('click', handleDocumentClick, true);
    return {
      destroy() {
        document.removeEventListener('click', handleDocumentClick, true);
      },
    };
  }
</script>

<svelte:window on:keydown={handleWindowKeydown} />

<span class="metric-tooltip-wrap" class:is-open={open} use:clickOutside={close}>
  <button
    type="button"
    class="metric-tooltip-btn"
    aria-label="How {label} is calculated"
    aria-expanded={open}
    on:click={toggle}
  >?</button>
  <span class="metric-tooltip-panel" role="tooltip">{tooltip}</span>
</span>

<style>
  .metric-tooltip-wrap { position: relative; flex-shrink: 0; margin-top: -1px; }
  .metric-tooltip-btn {
    width: 16px; height: 16px; border-radius: 50%;
    border: 1px solid var(--color-divider); background: var(--color-surface-2); color: var(--color-neutral-400);
    font: inherit; font-size: 10px; font-weight: 600; line-height: 1; cursor: pointer; padding: 0;
  }
  .metric-tooltip-btn:hover,
  .metric-tooltip-btn[aria-expanded="true"] { border-color: var(--color-accent); color: var(--color-accent); }
  .metric-tooltip-panel {
    display: none; position: absolute; top: calc(100% + 6px); right: 0; z-index: 4;
    width: max-content; max-width: min(280px, 70vw); background: var(--color-surface-2);
    border: 1px solid var(--color-divider); border-radius: 8px; padding: 10px 12px;
    font-size: 12px; line-height: 1.45; color: var(--color-neutral-400); text-transform: none;
    letter-spacing: normal; box-shadow: 0 8px 24px rgba(0, 0, 0, .35);
  }
  .metric-tooltip-wrap.is-open .metric-tooltip-panel,
  .metric-tooltip-wrap:hover .metric-tooltip-panel { display: block; }
</style>
