<script lang="ts">
  type DataAttr = 'data-category' | 'data-tab';
  type Tab = {
    value: string;
    label: string;
    active: boolean;
    modifierClass?: string;
    ariaControls?: string;
    id?: string;
  };

  export let containerId: string;
  export let ariaLabel: string = '';
  export let buttonClass: string;
  export let dataAttr: DataAttr;
  export let tabs: Tab[];
</script>

<div class="{containerId}" id="{containerId}" role="tablist" aria-label={ariaLabel || null}>
  {#each tabs as tab}
  <button
    type="button"
    class="{buttonClass}{tab.modifierClass ? ' ' + buttonClass + '--' + tab.modifierClass : ''}{tab.active ? ' is-active' : ''}"
    data-category={dataAttr === 'data-category' ? tab.value : null}
    data-tab={dataAttr === 'data-tab' ? tab.value : null}
    role="tab"
    aria-selected={tab.active}
    aria-controls={tab.ariaControls || null}
    id={tab.id || null}
  >{tab.label}</button>
  {/each}
</div>

<style>
  .report-category-tabs {
    display: flex; align-items: stretch; gap: var(--space-4); flex-wrap: wrap;
    padding: 0;
  }
  .report-tab {
    flex: 0 0 auto;
    background: transparent; color: var(--color-neutral-500);
    border: 0; border-bottom: 2px solid transparent; border-radius: 0;
    padding: var(--space-2) var(--space-1);
    font-family: var(--font-heading); font-size: 13px; font-weight: 700;
    cursor: pointer;
    transition: color .15s, border-color .15s;
  }
  .report-tab:hover { color: var(--color-text); }
  .report-tab.is-active {
    color: var(--color-text);
    border-bottom-color: var(--color-neutral-100);
  }

  .form-tabs, .game-review-tabs {
    position: sticky;
    top: var(--report-sticky-offset);
    z-index: 2;
    display: flex; flex-wrap: wrap; gap: 8px;
    margin: 0 0 14px; padding: 8px 0;
    background: transparent;
  }
  .form-tab, .game-review-tab {
    border: 1px solid var(--color-divider); background: var(--color-surface-2); color: var(--color-text);
    border-radius: 999px; padding: 8px 14px; cursor: pointer; font-size: 13px; font-weight: 600;
  }
  .form-tab.is-active, .game-review-tab.is-active {
    border-color: var(--color-neutral-500); color: var(--color-neutral-100); background: var(--color-surface-2);
  }
</style>
