<script>
  import { createEventDispatcher } from 'svelte';

  export let items = [];
  export let value = '';
  export let id = '';
  export let variant = 'pill'; // 'underline' | 'pill' | 'inset'
  export let as = 'group'; // 'tablist' | 'group'
  export let size = 'md'; // 'sm' | 'md'
  export let sticky = false;
  export let ariaLabel = '';

  const dispatch = createEventDispatcher();

  function select(item) {
    if (item.disabled) return;
    dispatch('select', item);
  }
</script>

<div
  class="segmented segmented--{variant} segmented--{size}{sticky ? ' is-sticky' : ''}"
  id={id || null}
  role={as === 'tablist' ? 'tablist' : 'group'}
  aria-label={ariaLabel || null}
>
  {#each items as item (item.value)}
    <button
      type="button"
      class="segmented-item{item.value === value ? ' is-active' : ''}"
      role={as === 'tablist' ? 'tab' : null}
      aria-selected={as === 'tablist' ? item.value === value : null}
      aria-controls={item.ariaControls || null}
      title={item.title || null}
      disabled={!!item.disabled}
      on:click={() => select(item)}
    >{item.label}</button>
  {/each}
</div>

<style>
  .segmented {
    display: flex;
    align-items: stretch;
    flex-wrap: wrap;
  }

  .segmented.is-sticky {
    position: sticky;
    top: var(--report-sticky-offset);
    z-index: 2;
    margin: 0 0 14px;
    padding: 8px 0;
    background: transparent;
  }

  /* Underline: report category tabs. */
  .segmented--underline {
    gap: var(--space-4);
    padding: 0;
  }
  .segmented--underline .segmented-item {
    flex: 0 0 auto;
    background: transparent;
    color: var(--color-neutral-500);
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    font-family: var(--font-heading);
    font-weight: 700;
    cursor: pointer;
    transition: color .15s, border-color .15s;
  }
  .segmented--underline.segmented--md .segmented-item { font-size: 13px; padding: var(--space-2) var(--space-1); }
  .segmented--underline.segmented--sm .segmented-item { font-size: 12px; padding: 6px var(--space-1); }
  .segmented--underline .segmented-item:hover { color: var(--color-text); }
  .segmented--underline .segmented-item.is-active {
    color: var(--color-text);
    border-bottom-color: var(--color-neutral-100);
  }

  /* Pill: form tracker / game review main tabs, queue / game window filters. */
  .segmented--pill {
    gap: 8px;
  }
  .segmented--pill .segmented-item {
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
    color: var(--color-text);
    border-radius: 999px;
    font-weight: 600;
    cursor: pointer;
    transition: color .15s, border-color .15s, background .15s;
  }
  .segmented--pill.segmented--md .segmented-item { font-size: 13px; padding: 8px 14px; }
  .segmented--pill.segmented--sm .segmented-item { font-size: 12px; padding: 6px 12px; }
  .segmented--pill .segmented-item:hover:not(:disabled) { color: var(--color-text); }
  .segmented--pill .segmented-item.is-active {
    border-color: var(--color-neutral-500);
    color: var(--color-neutral-100);
  }
  .segmented--pill .segmented-item:disabled { opacity: .45; cursor: not-allowed; }

  /* Inset: game review timeline mode / resource toggles. */
  .segmented--inset {
    gap: 6px;
    padding: 4px;
    border-radius: 10px;
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
  }
  .segmented--inset .segmented-item {
    border: 0;
    background: transparent;
    color: var(--color-neutral-400);
    border-radius: 8px;
    font-weight: 600;
    cursor: pointer;
    transition: color .15s, background .15s;
  }
  .segmented--inset.segmented--md .segmented-item { font-size: 12px; padding: 7px 12px; }
  .segmented--inset.segmented--sm .segmented-item { font-size: 11px; padding: 6px 10px; }
  .segmented--inset .segmented-item:hover:not(:disabled) { color: var(--color-text); background: rgba(255, 255, 255, 0.04); }
  .segmented--inset .segmented-item.is-active {
    color: var(--color-neutral-100);
    background: var(--color-surface-2);
  }

  @media (max-width: 860px) {
    .segmented--underline { gap: 8px; }
    .segmented--underline .segmented-item { font-size: 12px; padding: 8px 6px; }
    .segmented--pill.segmented--md .segmented-item { font-size: 12px; padding: 6px 12px; }
    .segmented--inset { gap: 4px; padding: 3px; }
    .segmented--inset .segmented-item { padding: 6px 10px; }
  }
</style>
