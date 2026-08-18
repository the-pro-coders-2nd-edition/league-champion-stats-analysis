<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import Disclosure from '../components/Disclosure.svelte';
  import SectionPulse from '../components/SectionPulse.svelte';
  import TieredCards from '../components/TieredCards.svelte';
  import DataTable from '../components/DataTable.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { metricLabelWithIconify } from '../lib/html.js';
  import { pyFloatStr } from '../lib/format.js';

  export let data;

  function titleCase(str) {
    return String(str)
      .split(' ')
      .map((word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : word))
      .join(' ');
  }

  const OBJECTIVE_DIED_TOOLTIP = 'Share of epic objectives where you died in the 45–10s setup window before the take ' +
    '(caught before the fight; deaths in the last 10s are excluded as teamfight deaths).';
  const OBJECTIVE_WARDS_TOOLTIP = 'Average wards you placed in the 2 minutes before each objective take. Any ward type counts; map location is not filtered.';

  $: objectiveColumns = [
    { label: 'Objective' },
    { label: 'Count' },
    { label: 'Taken' },
    { label: 'Present' },
    { label: 'Arrived early' },
    { label: 'Died in setup window (45–10s)', tooltip: OBJECTIVE_DIED_TOOLTIP },
    { label: 'Wards before', tooltip: OBJECTIVE_WARDS_TOOLTIP },
  ];

  $: blindSpotColumns = [
    { label: 'Zone' },
    { label: 'Deaths', iconify: 'lucide:skull' },
  ];


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
  <Disclosure variant="pill" chevron="leading">
    <svelte:fragment slot="summary">Show charts</svelte:fragment>
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
  </Disclosure>
</section>

<section id="objectives" class="report-section report-section--deepdive">
  <SectionHeader id="objectives" title="Objectives" icon="target" />
  <SectionPulse sectionId="objectives" verdict={data.section_verdicts?.objectives} />
  {#if showSplitPushStats}
    <div id="objective-macro-cards"><TieredCards cards={objectiveMacroCards} moreLabel="More split-push stats" /></div>
  {/if}
  <DataTable columns={objectiveColumns} rows={data.objective_rows || []} wrapClass="objectives-table">
    <svelte:fragment slot="cells" let:row>
      <td>
        {#if row.objective_icon}
          <span class="icon-cell"><img src={row.objective_icon} alt="" class="game-icon game-icon--sm"><span>{titleCase(row.kind)}</span></span>
        {:else}
          {titleCase(row.kind)}
        {/if}
      </td>
      <td>{row.count}</td>
      <td>{Math.round(row.taken_rate * 100)}%</td>
      <td>{Math.round(row.presence_rate * 100)}%</td>
      <td>{Math.round(row.early_rate * 100)}%</td>
      <td>{Math.round(row.dead_before_rate * 100)}%</td>
      <td>{pyFloatStr(row.avg_wards_before)}</td>
    </svelte:fragment>
  </DataTable>
  <Disclosure variant="pill" chevron="leading">
    <svelte:fragment slot="summary">Show charts</svelte:fragment>
    <div class="figure-block">
      <PlotlyFigure id="fig-objective_timing" html={data.figures?.objective_timing || ''} />
      <p class="figure-caption">When objectives are taken — early arrivals and ward placement before spawn improve take rates.</p>
    </div>
  </Disclosure>
</section>

<section id="deaths" class="report-section report-section--deepdive">
  <SectionHeader id="deaths" title="Deaths" icon="skull" />
  <SectionPulse sectionId="deaths" verdict={data.section_verdicts?.deaths} />
  <div id="death-cards"><TieredCards cards={data.death_cards || []} moreLabel="More death stats" /></div>
  <Disclosure variant="pill" chevron="leading">
    <svelte:fragment slot="summary">Show charts &amp; danger zones</svelte:fragment>
    <div class="figure-block">
      <PlotlyFigure id="fig-death_heatmap" html={data.figures?.death_heatmap || ''} />
      <p class="figure-caption">Where you die on the map — red-side games are mirrored across the river (perpendicular to mid) so bot-lane deaths stay on the bottom edge. Hot zones without nearby team vision are prime gank or face-check risks.</p>
    </div>
    <div class="figure-block">
      <PlotlyFigure id="fig-deaths_box" html={data.figures?.deaths_box || ''} />
      <p class="figure-caption">Deaths per game spread — a high median with a long upper tail means volatile survival.</p>
    </div>
    <div id="blind-spots-block" style={blindSpots.length ? null : 'display:none'}>
      <h3>{@html metricLabelWithIconify('Most dangerous zones (solo deaths without recent team vision)', 'lucide:skull')}</h3>
      <DataTable columns={blindSpotColumns} rows={blindSpots}>
        <svelte:fragment slot="cells" let:row>
          <td>{row.zone}</td>
          <td>{row.deaths}</td>
        </svelte:fragment>
      </DataTable>
    </div>
  </Disclosure>
</section>
