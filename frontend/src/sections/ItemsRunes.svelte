<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import IconStatTable from '../components/IconStatTable.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';

  export let data;
</script>

<section id="items" class="report-section report-section--champion">
  <SectionHeader id="items" title="Items" icon="wand-sparkles" />
  <div class="figure-block">
    <PlotlyFigure id="fig-item_winrate_bar" html={data.figures?.item_winrate_bar || ''} />
    <p class="figure-caption">Win rate by completed item — favor items with enough sample size and a clear WR edge.</p>
  </div>
  <h3>Two-item cores</h3>
  <IconStatTable firstColumnLabel="Core" rows={data.build_paths || []}>
    <svelte:fragment slot="first" let:row>
      {#if row.first_item_icon || row.second_item_icon}
        <span class="core-cell">
          <span class="icon-cell">
            {#if row.first_item_icon}<img src={row.first_item_icon} alt="" class="game-icon game-icon--sm">{/if}
            <span>{row.first_item}</span>
          </span>
          <span class="core-arrow" aria-hidden="true">→</span>
          <span class="icon-cell">
            {#if row.second_item_icon}<img src={row.second_item_icon} alt="" class="game-icon game-icon--sm">{/if}
            <span>{row.second_item}</span>
          </span>
        </span>
      {:else}
        {row.core || `${row.first_item} -> ${row.second_item}`}
      {/if}
    </svelte:fragment>
  </IconStatTable>
</section>

<section id="runes" class="report-section report-section--champion">
  <SectionHeader id="runes" title="Runes" icon="sparkles" />
  <div class="figure-block">
    <PlotlyFigure id="fig-rune_winrate_bar" html={data.figures?.rune_winrate_bar || ''} />
    <p class="figure-caption">Win rate by keystone — compare rune setups with similar game counts before switching.</p>
  </div>
  <IconStatTable firstColumnLabel="Keystone" rows={data.rune_rows || []}>
    <svelte:fragment slot="first" let:row>
      {#if row.keystone_icon}
        <span class="icon-cell">
          <img src={row.keystone_icon} alt="" class="game-icon game-icon--sm">
          <span>{row.keystone}</span>
        </span>
      {:else}
        {row.keystone}
      {/if}
    </svelte:fragment>
  </IconStatTable>
</section>
