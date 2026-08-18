<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import SectionPulse from '../components/SectionPulse.svelte';
  import TieredCards from '../components/TieredCards.svelte';
  import DataTableHead from '../components/DataTableHead.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { escapeHtml, iconCellHtml, metricLabelWithIconify } from '../lib/html.js';
  import { pyFloatStr } from '../lib/format.js';

  export let data;

  function titleCase(str) {
    return String(str)
      .split(' ')
      .map((word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : word))
      .join(' ');
  }

  function metricLabelTooltipHtml(label, tooltip) {
    return `<span class="metric-label-with-tooltip">\n` +
      `  <span class="metric-label"><span>${escapeHtml(label)}</span></span>\n` +
      `  <span class="metric-tooltip-wrap"><button type="button" class="metric-tooltip-btn" aria-label="How ${escapeHtml(label)} is calculated" aria-expanded="false">?</button> <span class="metric-tooltip-panel" role="tooltip">${escapeHtml(tooltip)}</span> </span>\n` +
      `</span>`;
  }

  const OBJECTIVE_DIED_TOOLTIP = 'Share of epic objectives where you died in the 45–10s setup window before the take ' +
    '(caught before the fight; deaths in the last 10s are excluded as teamfight deaths).';
  const OBJECTIVE_WARDS_TOOLTIP = 'Average wards you placed in the 2 minutes before each objective take. Any ward type counts; map location is not filtered.';

  $: objectiveColumns = [
    { html: 'Objective', id: '' },
    { html: 'Count', id: '' },
    { html: 'Taken', id: '' },
    { html: 'Present', id: '' },
    { html: 'Arrived early', id: '' },
    { html: metricLabelTooltipHtml('Died in setup window (45–10s)', OBJECTIVE_DIED_TOOLTIP), id: '' },
    { html: metricLabelTooltipHtml('Wards before', OBJECTIVE_WARDS_TOOLTIP), id: '' },
  ];

  $: blindSpotColumns = [
    { html: 'Zone', id: '' },
    { html: metricLabelWithIconify('Deaths', 'lucide:skull', 'danger'), id: '' },
  ];

  function objectiveRowHtml(row) {
    return `<td>${iconCellHtml(titleCase(row.kind), row.objective_icon)}</td><td>${row.count}</td>` +
      `<td>${Math.round(row.taken_rate * 100)}%</td><td>${Math.round(row.presence_rate * 100)}%</td>` +
      `<td>${Math.round(row.early_rate * 100)}%</td><td>${Math.round(row.dead_before_rate * 100)}%</td>` +
      `<td>${pyFloatStr(row.avg_wards_before)}</td>`;
  }

  function blindSpotRowHtml(row) {
    return `<td>${escapeHtml(row.zone)}</td><td>${row.deaths}</td>`;
  }

  $: earlySectionTitle = data.early_section_title || 'Lane';
  $: laneMoreLabel = `More ${(data.early_section_title || 'lane').toLowerCase()} stats`;
  $: showCsStats = !!data.show_cs_stats;
  $: showSplitPushStats = !!data.show_split_push_stats;
  $: objectiveMacroCards = data.objective_macro_cards || [];
  $: blindSpots = data.blind_spots || [];
</script>

<section id="lane" class="report-section report-section--deepdive">
  <SectionHeader id="lane" title={earlySectionTitle} icon="map" />
  <SectionPulse sectionId="lane" verdict={data.section_verdicts?.lane} />
  <div id="lane-cards"><TieredCards cards={data.lane_cards || []} moreLabel={laneMoreLabel} /></div>
  <details class="section-details">
    <summary>Show charts</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-gd10_histogram" html={data.figures?.gd10_histogram || ''} />
      <p class="figure-caption">Gold diff @10 distribution — a right-skewed shape means you often win lane; left-skewed means you fall behind early.</p>
    </div>
    <div class="figure-block">
      <PlotlyFigure id="fig-gold_diff_timeline" html={data.figures?.gold_diff_timeline || ''} />
      <p class="figure-caption">Average gold lead over time in wins vs losses — check where your leads convert or collapse.</p>
    </div>
    {#if showCsStats}
      <div class="figure-block">
        <PlotlyFigure id="fig-cs10_violin" html={data.figures?.cs10_violin || ''} />
        <p class="figure-caption">CS @10 spread across games — tighter clusters mean more consistent farming.</p>
      </div>
    {/if}
  </details>
</section>

<section id="objectives" class="report-section report-section--deepdive">
  <SectionHeader id="objectives" title="Objectives" icon="target" />
  <SectionPulse sectionId="objectives" verdict={data.section_verdicts?.objectives} />
  {#if showSplitPushStats}
    <div id="objective-macro-cards"><TieredCards cards={objectiveMacroCards} moreLabel="More split-push stats" /></div>
  {/if}
  <div class="table-scroll">
    <table>
      <DataTableHead columns={objectiveColumns} />
      <tbody id="objective-table-body">
        {#each data.objective_rows || [] as row}
          <DataTableRow cellsHtml={objectiveRowHtml(row)} />
        {/each}
      </tbody>
    </table>
  </div>
  <details class="section-details">
    <summary>Show charts</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-objective_timing" html={data.figures?.objective_timing || ''} />
      <p class="figure-caption">When objectives are taken — early arrivals and ward placement before spawn improve take rates.</p>
    </div>
  </details>
</section>

<section id="deaths" class="report-section report-section--deepdive">
  <SectionHeader id="deaths" title="Deaths" icon="skull" />
  <SectionPulse sectionId="deaths" verdict={data.section_verdicts?.deaths} />
  <div id="death-cards"><TieredCards cards={data.death_cards || []} moreLabel="More death stats" /></div>
  <details class="section-details">
    <summary>Show charts &amp; danger zones</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-death_heatmap" html={data.figures?.death_heatmap || ''} />
      <p class="figure-caption">Where you die on the map — red-side games are mirrored across the river (perpendicular to mid) so bot-lane deaths stay on the bottom edge. Hot zones without nearby team vision are prime gank or face-check risks.</p>
    </div>
    <div class="figure-block">
      <PlotlyFigure id="fig-deaths_box" html={data.figures?.deaths_box || ''} />
      <p class="figure-caption">Deaths per game spread — a high median with a long upper tail means volatile survival.</p>
    </div>
    {#if blindSpots.length}
      <div id="blind-spots-block">
        <h3>{@html metricLabelWithIconify('Most dangerous zones (solo deaths without recent team vision)', 'lucide:skull', 'danger')}</h3>
        <div class="table-scroll">
          <table>
            <DataTableHead columns={blindSpotColumns} />
            <tbody id="blind-spots-body">
              {#each blindSpots as row}
                <DataTableRow cellsHtml={blindSpotRowHtml(row)} />
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {:else}
      <div id="blind-spots-block" style="display:none">
        <h3>{@html metricLabelWithIconify('Most dangerous zones (solo deaths without recent team vision)', 'lucide:skull', 'danger')}</h3>
        <div class="table-scroll">
          <table>
            <DataTableHead columns={blindSpotColumns} />
            <tbody id="blind-spots-body"></tbody>
          </table>
        </div>
      </div>
    {/if}
  </details>
</section>
