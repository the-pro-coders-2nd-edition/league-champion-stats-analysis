<script lang="ts">
  import MetricTooltip from './MetricTooltip.svelte';

  type Column = { label: string; iconify?: string; tooltip?: string; title?: string };

  export let columns: Column[];
  export let rows: any[];
  export let wrapClass: string = '';
</script>

<!-- Cell order must match `columns` order; the slot cannot enforce this on Svelte 4. -->
<div class="table-scroll {wrapClass}">
  <table>
    <thead>
      <tr>
        {#each columns as col}
          <th title={col.title || undefined}>
            {#if col.tooltip}
              <span class="metric-label-with-tooltip">
                <span class="metric-label">
                  {#if col.iconify}<iconify-icon icon={col.iconify} class="metric-icon" aria-hidden="true"></iconify-icon>{/if}
                  <span>{col.label}</span>
                </span>
                <MetricTooltip label={col.label} tooltip={col.tooltip} />
              </span>
            {:else if col.iconify}
              <span class="metric-label">
                <iconify-icon icon={col.iconify} class="metric-icon" aria-hidden="true"></iconify-icon>
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
