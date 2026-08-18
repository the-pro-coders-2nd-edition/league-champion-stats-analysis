<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import DataTableHead from '../components/DataTableHead.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { escapeHtml, iconCellHtml, metricLabelWithIconify } from '../lib/html.js';
  import { pyFloatStr } from '../lib/format.js';

  export let data;

  function coreCellHtml(row) {
    if (row.first_item_icon || row.second_item_icon) {
      return `<span class="core-cell"> ${iconCellHtml(row.first_item, row.first_item_icon)} ` +
        `<span class="core-arrow" aria-hidden="true">→</span> ${iconCellHtml(row.second_item, row.second_item_icon)} </span>`;
    }
    return escapeHtml(row.core || `${row.first_item} -> ${row.second_item}`);
  }

  const AVG_DPM_HEAD = { html: metricLabelWithIconify('Avg DPM', 'lucide:flame', 'orange'), id: '' };
  const AVG_DEATHS_HEAD = { html: metricLabelWithIconify('Avg deaths', 'lucide:skull', 'danger'), id: '' };

  $: itemsCoreColumns = [
    { html: 'Core', id: '' },
    { html: 'Games', id: '' },
    { html: 'Win rate', id: '' },
    AVG_DPM_HEAD,
    AVG_DEATHS_HEAD,
  ];

  $: runeColumns = [
    { html: 'Keystone', id: '' },
    { html: 'Games', id: '' },
    { html: 'Win rate', id: '' },
    AVG_DPM_HEAD,
    AVG_DEATHS_HEAD,
  ];

  function itemsCoreRowHtml(row) {
    return `<td>${coreCellHtml(row)}</td><td>${row.games}</td><td>${Math.round(row.winrate * 100)}%</td>` +
      `<td>${pyFloatStr(row.avg_dpm)}</td><td>${pyFloatStr(row.avg_deaths)}</td>`;
  }

  function runeRowHtml(row) {
    return `<td>${iconCellHtml(row.keystone, row.keystone_icon)}</td><td>${row.games}</td><td>${Math.round(row.winrate * 100)}%</td>` +
      `<td>${pyFloatStr(row.avg_dpm)}</td><td>${pyFloatStr(row.avg_deaths)}</td>`;
  }

</script>

<section id="items" class="report-section report-section--champion">
  <SectionHeader id="items" title="Items" icon="wand-sparkles" />
  <div class="figure-block">
    <PlotlyFigure id="fig-item_winrate_bar" html={data.figures?.item_winrate_bar || ''} />
    <p class="figure-caption">Win rate by completed item — favor items with enough sample size and a clear WR edge.</p>
  </div>
  <h3>Two-item cores</h3>
  <div class="table-scroll">
    <table>
      <DataTableHead columns={itemsCoreColumns} />
      <tbody id="build-paths-body">
        {#each data.build_paths || [] as row}
          <DataTableRow cellsHtml={itemsCoreRowHtml(row)} />
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section id="runes" class="report-section report-section--champion">
  <SectionHeader id="runes" title="Runes" icon="sparkles" />
  <div class="figure-block">
    <PlotlyFigure id="fig-rune_winrate_bar" html={data.figures?.rune_winrate_bar || ''} />
    <p class="figure-caption">Win rate by keystone — compare rune setups with similar game counts before switching.</p>
  </div>
  <div class="table-scroll">
    <table>
      <DataTableHead columns={runeColumns} />
      <tbody id="rune-rows-body">
        {#each data.rune_rows || [] as row}
          <DataTableRow cellsHtml={runeRowHtml(row)} />
        {/each}
      </tbody>
    </table>
  </div>
</section>
