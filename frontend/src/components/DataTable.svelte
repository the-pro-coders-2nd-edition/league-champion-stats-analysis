<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import MetricTooltip from './MetricTooltip.svelte';

  type Column = {
    key?: string;
    type?: string;
    label: string;
    iconify?: string;
    iconTone?: string;
    tooltip?: string;
    title?: string;
  };

  export let columns: Column[];
  export let rows: any[];
  export let wrapClass: string = '';
  export let sortKey: string | null = null;
  export let sortDir: 'asc' | 'desc' = 'asc';

  const dispatch = createEventDispatcher();

  $: hasSortableColumns = columns.some((col) => col.key);

  function ariaSort(col: Column) {
    if (!col.key || sortKey !== col.key) return 'none';
    return sortDir === 'asc' ? 'ascending' : 'descending';
  }
</script>

<!-- Cell order must match `columns` order; the slot cannot enforce this on Svelte 4. -->
<div class="table-scroll {wrapClass}">
  <table class:sortable-table={hasSortableColumns}>
    <thead>
      <tr>
        {#each columns as col}
          <th title={col.key ? undefined : (col.title || undefined)} aria-sort={col.key ? ariaSort(col) : undefined}>
            {#if col.key}
              <button
                type="button"
                class="sort-btn"
                class:is-sorted={sortKey === col.key}
                class:is-sorted-asc={sortKey === col.key && sortDir === 'asc'}
                class:is-sorted-desc={sortKey === col.key && sortDir === 'desc'}
                data-sort-key={col.key}
                data-sort-type={col.type}
                title={col.title || undefined}
                on:click={() => dispatch('sort', col)}
              >
                {#if col.iconify}<iconify-icon icon={col.iconify} class="metric-icon metric-icon--{col.iconTone || 'muted'}" aria-hidden="true"></iconify-icon>{/if}
                <span>{col.label}</span>
              </button>
            {:else if col.tooltip}
              <span class="metric-label-with-tooltip">
                <span class="metric-label">
                  {#if col.iconify}<iconify-icon icon={col.iconify} class="metric-icon metric-icon--{col.iconTone || 'muted'}" aria-hidden="true"></iconify-icon>{/if}
                  <span>{col.label}</span>
                </span>
                <MetricTooltip label={col.label} tooltip={col.tooltip} />
              </span>
            {:else if col.iconify}
              <span class="metric-label">
                <iconify-icon icon={col.iconify} class="metric-icon metric-icon--{col.iconTone || 'muted'}" aria-hidden="true"></iconify-icon>
                <span>{col.label}</span>
              </span>
            {:else}
              {col.label}
            {/if}
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as row}
        <tr>
          <slot name="cells" {row} />
        </tr>
      {/each}
    </tbody>
  </table>
</div>
