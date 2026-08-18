<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import DataTable from '../components/DataTable.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { pct } from '../lib/format.js';

  export let data;

  const MATCHUP_VERDICT_RANK = { unfavorable: 0, lean_unfavorable: 1, even: 2, lean_favorable: 3, favorable: 4 };

  const COLUMNS = [
    { key: 'opponent', type: 'string', label: 'Opponent' },
    { key: 'games', type: 'number', label: 'Games' },
    { key: 'winrate', type: 'number', label: 'WR', title: 'Your win rate in this matchup' },
    { key: 'verdict', type: 'verdict', label: 'Verdict', title: 'Win-rate read, softer below 3 games' },
    { key: 'avg_gd10', type: 'number', label: 'Gold@10', title: 'Average gold difference at 10 minutes' },
    { key: 'avg_csd10', type: 'number', label: 'CS@10', title: 'Average CS difference at 10 minutes', csOnly: true },
    { key: 'avg_deaths_pre14', type: 'number', label: 'Deaths <14', title: 'Average deaths before 14 minutes' },
  ];

  let sortKey = 'verdict';
  let sortDir = 'desc';
  let sortType = 'verdict';

  function signedMetricText(value, digits) {
    if (value == null || value === '') return null;
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    return (num > 0 ? '+' : '') + num.toFixed(digits == null ? 0 : digits);
  }

  function deathsText(value) {
    if (value == null || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(1) : null;
  }

  function sortValue(value, type) {
    if (value == null || value === '') return null;
    if (type === 'number') {
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    }
    if (type === 'verdict') {
      const rank = MATCHUP_VERDICT_RANK[String(value)];
      return rank == null ? null : rank;
    }
    return String(value).toLowerCase();
  }

  function sortRows(rows, key, dir, type) {
    const ascending = dir === 'asc';
    return (rows || []).slice().sort((left, right) => {
      const a = sortValue(left[key], type);
      const b = sortValue(right[key], type);
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      if (a < b) return ascending ? -1 : 1;
      if (a > b) return ascending ? 1 : -1;
      const gamesA = Number(left.games) || 0;
      const gamesB = Number(right.games) || 0;
      if (gamesA !== gamesB) return gamesB - gamesA;
      return String(left.opponent || '').localeCompare(String(right.opponent || ''));
    });
  }

  function handleSort(column) {
    if (sortKey === column.key) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey = column.key;
      sortType = column.type;
      sortDir = column.type === 'number' || column.type === 'verdict' ? 'desc' : 'asc';
    }
  }

  $: showCsStats = data.show_cs_stats !== false;
  $: matchupRows = data.matchup_rows || [];
  $: visibleColumns = COLUMNS.filter((col) => !col.csOnly || showCsStats);
  $: tableColumns = [...visibleColumns, { label: 'Play plan' }];
  $: sortedRows = sortRows(matchupRows, sortKey, sortDir, sortType);
</script>

<section id="matchups" class="report-section report-section--champion">
  <SectionHeader id="matchups" title="Matchups" icon="swords">
    <svelte:fragment slot="lead">Lane opponents you've faced most. <strong>Verdict</strong> is your win rate read; <strong>Play plan</strong> is the single biggest pattern in those games.</svelte:fragment>
  </SectionHeader>
  <div class="figure-block">
    <PlotlyFigure id="fig-matchup_bar" html={(data.figures && data.figures.matchup_bar) || ''} />
    <p class="figure-caption">Win rate vs lane opponents — bar length shows sample size; color shows above/below 50% WR.</p>
  </div>
  <DataTable
    columns={tableColumns}
    rows={sortedRows}
    wrapClass="matchup-table"
    {sortKey}
    {sortDir}
    on:sort={(event) => handleSort(event.detail)}
  >
    <svelte:fragment slot="cells" let:row>
      <td>
        {#if row.opponent_icon}
          <span class="icon-cell"><img src={row.opponent_icon} alt="" class="game-icon game-icon--sm"><span>{row.opponent}</span></span>
        {:else}
          {row.opponent}
        {/if}
      </td>
      <td>{row.games}</td>
      <td>{pct(row.winrate)}</td>
      <td><span class="matchup-verdict matchup-verdict--{row.verdict || 'even'}">{row.verdict_label || 'Even'}</span></td>
      <td>
        {#if signedMetricText(row.avg_gd10, 0) == null}—{:else if row.gd10_color}<span class="matchup-metric" style="color: {row.gd10_color}">{signedMetricText(row.avg_gd10, 0)}</span>{:else}{signedMetricText(row.avg_gd10, 0)}{/if}
      </td>
      {#if showCsStats}
        <td>
          {#if signedMetricText(row.avg_csd10, 0) == null}—{:else if row.csd10_color}<span class="matchup-metric" style="color: {row.csd10_color}">{signedMetricText(row.avg_csd10, 0)}</span>{:else}{signedMetricText(row.avg_csd10, 0)}{/if}
        </td>
      {/if}
      <td>
        {#if deathsText(row.avg_deaths_pre14) == null}—{:else if row.deaths_pre14_color}<span class="matchup-metric" style="color: {row.deaths_pre14_color}">{deathsText(row.avg_deaths_pre14)}</span>{:else}{deathsText(row.avg_deaths_pre14)}{/if}
      </td>
      <td class="matchup-plan">
        {#if row.focus}<span class="matchup-focus matchup-focus--{row.focus_key || 'standard'}">{row.focus}</span>{/if}
        <span class="matchup-tip">{row.recommendation || ''}</span>
      </td>
    </svelte:fragment>
  </DataTable>
</section>
