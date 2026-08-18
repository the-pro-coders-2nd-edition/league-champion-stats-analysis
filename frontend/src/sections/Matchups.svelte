<script>
  import Pill from '../components/Pill.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';

  export let data;

  const MATCHUP_VERDICT_RANK = { unfavorable: 0, lean_unfavorable: 1, even: 2, lean_favorable: 3, favorable: 4 };

  const COLUMNS = [
    { key: 'opponent', type: 'string', label: 'Opponent' },
    { key: 'games', type: 'number', label: 'Games' },
    { key: 'winrate', type: 'number', label: 'WR', title: 'Your win rate in this matchup' },
    { key: 'verdict', type: 'verdict', label: 'Verdict', title: 'Win-rate read, softer below 3 games' },
    { key: 'avg_gd10', type: 'number', label: 'Gold@10', title: 'Average gold difference at 10 minutes' },
    { key: 'avg_csd10', type: 'number', label: 'CS@10', title: 'Average CS difference at 10 minutes', csOnly: true },
    { key: 'avg_deaths_pre14', type: 'number', label: 'Deaths <14', title: 'Average deaths before 14 minutes' },
  ];

  let sortKey = 'verdict';
  let sortDir = 'desc';
  let sortType = 'verdict';

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function iconCell(name, iconHref) {
    if (iconHref) {
      return `<span class="icon-cell"><img src="${iconHref}" alt="" class="game-icon game-icon--sm"><span>${name}</span></span>`;
    }
    return name;
  }

  function pct(value) {
    const num = Number(value);
    if (value == null || !Number.isFinite(num)) return '—';
    return Math.round(num * 100) + '%';
  }

  function signedMetric(value, color, digits) {
    if (value == null || value === '') return '—';
    const num = Number(value);
    if (!Number.isFinite(num)) return '—';
    const text = (num > 0 ? '+' : '') + num.toFixed(digits == null ? 0 : digits);
    if (color) return `<span class="matchup-metric" style="color: ${color}">${text}</span>`;
    return text;
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

  function matchupRowCellsHtml(row) {
    const wrHtml = pct(row.winrate);
    const deaths = row.avg_deaths_pre14;
    let deathsHtml = '—';
    if (deaths != null && deaths !== '' && Number.isFinite(Number(deaths))) {
      deathsHtml = Number(deaths).toFixed(1);
      if (row.deaths_pre14_color) {
        deathsHtml = `<span class="matchup-metric" style="color: ${row.deaths_pre14_color}">${deathsHtml}</span>`;
      }
    }
    const verdict = escapeHtml(row.verdict_label || 'Even');
    const verdictKey = escapeHtml(row.verdict || 'even');
    const focusKey = escapeHtml(row.focus_key || 'standard');
    const focus = row.focus
      ? `<span class="matchup-focus matchup-focus--${focusKey}">${escapeHtml(row.focus)}</span>`
      : '';
    const tip = `<span class="matchup-tip">${escapeHtml(row.recommendation || '')}</span>`;
    const csCell = showCsStats ? `<td>${signedMetric(row.avg_csd10, row.csd10_color, 0)}</td>` : '';
    return `<td>${iconCell(row.opponent, row.opponent_icon)}</td><td>${row.games}</td><td>${wrHtml}</td>` +
      `<td><span class="matchup-verdict matchup-verdict--${verdictKey}">${verdict}</span></td><td>` +
      `${signedMetric(row.avg_gd10, row.gd10_color, 0)}</td>${csCell}<td>${deathsHtml}</td>` +
      `<td class="matchup-plan">${focus}${tip}</td>`;
  }

  $: windowScopeOption = (data.game_window_options || []).find((o) => o.key === data.game_window_default);
  $: windowScopeLabel = windowScopeOption ? `${windowScopeOption.label} games` : 'All games';

  $: showCsStats = data.show_cs_stats !== false;
  $: matchupRows = data.matchup_rows || [];
  $: visibleColumns = COLUMNS.filter((col) => !col.csOnly || showCsStats);
  $: sortedRows = sortRows(matchupRows, sortKey, sortDir, sortType);
</script>

<section id="matchups" class="report-section report-section--champion">
  <h2 class="section-title section-title--champion">
    <iconify-icon icon="lucide:swords" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Matchups</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <p class="sub sub--lead">Lane opponents you've faced most. <strong>Verdict</strong> is your win rate read; <strong>Play plan</strong> is the single biggest pattern in those games.</p>
  <div class="figure-block">
    <PlotlyFigure id="fig-matchup_bar" html={(data.figures && data.figures.matchup_bar) || ''} />
    <p class="figure-caption">Win rate vs lane opponents — bar length shows sample size; color shows above/below 50% WR.</p>
  </div>
  <div class="table-scroll">
  <table class="sortable-table matchup-table" id="matchup-table" data-sortable="matchups">
    <thead>
      <tr>
        {#each visibleColumns as column}
        <th aria-sort={sortKey === column.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
          <button
            type="button"
            class="sort-btn"
            class:is-sorted={sortKey === column.key}
            class:is-sorted-asc={sortKey === column.key && sortDir === 'asc'}
            class:is-sorted-desc={sortKey === column.key && sortDir === 'desc'}
            data-sort-key={column.key}
            data-sort-type={column.type}
            title={column.title || null}
            on:click={() => handleSort(column)}
          >{column.label}</button>
        </th>
        {/each}
        <th>Play plan</th>
      </tr>
    </thead>
    <tbody id="matchup-rows-body">
    {#each sortedRows as row}
    <DataTableRow cellsHtml={matchupRowCellsHtml(row)} />
    {/each}
    </tbody>
  </table>
  </div>
</section>
