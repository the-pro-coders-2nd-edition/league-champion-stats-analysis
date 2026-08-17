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
    display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap;
    padding: 0 0 14px;
  }
  .report-tab {
    flex: 1 1 auto; min-width: 0;
    border: 1px solid var(--color-divider); border-bottom-width: 3px;
    background: var(--color-surface-2); color: var(--color-neutral-400);
    border-radius: 10px 10px 0 0; padding: 10px 16px;
    font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: color .15s, background .15s, border-color .15s;
  }
  .report-tab:hover { color: var(--color-text); background: var(--color-surface); }
  .report-tab.is-active {
    color: var(--color-text); background: var(--color-surface);
    border-bottom-color: var(--color-accent);
  }
  .report-tab--summary.is-active { border-bottom-color: var(--cat-summary); }
  .report-tab--performance.is-active { border-bottom-color: var(--cat-performance); }
  .report-tab--games.is-active { border-bottom-color: var(--cat-games); }
  .report-tab--champion.is-active { border-bottom-color: var(--cat-champion); }
  .report-tab--deepdive.is-active { border-bottom-color: var(--cat-deepdive); }
  .report-tab--advanced.is-active { border-bottom-color: var(--cat-advanced); }

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
    border-color: var(--color-accent); color: #fff; background: var(--color-accent);
  }
</style>
