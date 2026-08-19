<script>
  import { tick } from 'svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import SkillGrid from '../components/SkillGrid.svelte';
  import Panel from '../components/Panel.svelte';
  import GameSummaryHeader from '../components/GameSummaryHeader.svelte';
  import ScoreDisclosure from '../components/ScoreDisclosure.svelte';
  import SegmentedControl from '../components/SegmentedControl.svelte';
  import Callout from '../components/Callout.svelte';
  import Disclosure from '../components/Disclosure.svelte';
  import ShowMore from '../components/ShowMore.svelte';
  import Chip from '../components/Chip.svelte';
  import IconCellSolo from '../components/IconCellSolo.svelte';
  import ChampionIconStack from '../components/ChampionIconStack.svelte';
  import GameReviewRuneDuo from '../components/GameReviewRuneDuo.svelte';
  import GameReviewRunePage from '../components/GameReviewRunePage.svelte';
  import GameReviewSummoners from '../components/GameReviewSummoners.svelte';
  import GameReviewKeyMoments from './GameReviewKeyMoments.svelte';
  import { escapeHtml, soloIconCellHtml } from '../lib/html.js';
  import { formatGameTime, pct } from '../lib/format.js';
  import { resizePlotlySoon } from '../lib/plotlyResize.js';
  import { careerGoalsForGame, goalOutcomeByColumn } from '../lib/careerGameGoals.js';
  import { REPORT_NAV_KEY, handleNavClick } from '../lib/reportNav.js';
  import { getContext } from 'svelte';

  export let data;
  // The resolved Career ladder. Career follows neither the queue nor the window
  // filter, so it is passed in rather than read off the slice.
  export let career = null;

  const reportNav = getContext(REPORT_NAV_KEY);

  let selectedMatchId = data.game_review_selected_match_id ?? null;
  let activeTab = 'overview';
  let moreOpen = false;
  let moreOpenInitialized = false;
  let scoreDetailsOpen = false;
  let objectivesOpen = false;
  let timelineMode = 'lane';
  let timelineMetric = 'gold';
  let chartEl;
  let plotlyReady = typeof window !== 'undefined' && !!window.Plotly;

  // `iconCell(name, iconHref, true)` from report.html — always icon-only in this section.
  const iconCellHtml = soloIconCellHtml;

  function formatMetricValue(value) {
    if (value === null || value === undefined) return '—';
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    if (Math.abs(num - Math.round(num)) < 1e-9) return String(Math.round(num));
    const rounded1 = Math.round(num * 10) / 10;
    if (Math.abs(num - rounded1) < 1e-9) return rounded1.toFixed(1);
    return (Math.round(num * 100) / 100).toFixed(2);
  }

  function isShareOrParticipationMetric(name) {
    const key = String(name || '').toLowerCase();
    return key.indexOf('share') !== -1 || key.indexOf('participation') !== -1 ||
      key.slice(-5) === '_rate' || key === 'objectives_present_rate';
  }

  function isGoldDiffMetric(name) {
    const key = String(name || '').toLowerCase();
    return key === 'gd10' || key === 'gd15' || key.indexOf('gold_diff') !== -1 || key.indexOf('gold diff') !== -1;
  }

  function formatGameReviewMetricValue(metric, value) {
    if (value === null || value === undefined) return '—';
    if (isShareOrParticipationMetric(metric)) return pct(value);
    if (isGoldDiffMetric(metric)) {
      const gold = Math.round(Number(value));
      if (!Number.isFinite(gold)) return String(value);
      return (gold > 0 ? '+' : '') + gold;
    }
    return formatMetricValue(value);
  }

  function formatGameReviewMetricDelta(metric, value) {
    if (value === null || value === undefined) return '—';
    if (isShareOrParticipationMetric(metric)) {
      const num = Number(value);
      if (!Number.isFinite(num)) return String(value);
      const scaled = Math.round(num * 100);
      return (scaled > 0 ? '+' : '') + scaled + '%';
    }
    if (isGoldDiffMetric(metric)) {
      const goldDelta = Math.round(Number(value));
      if (!Number.isFinite(goldDelta)) return String(value);
      return (goldDelta > 0 ? '+' : '') + goldDelta;
    }
    const plain = Number(value);
    if (!Number.isFinite(plain)) return formatMetricValue(value);
    const formatted = formatMetricValue(value);
    return plain > 0 ? '+' + formatted : formatted;
  }

  function metricValueTone(metric, value) {
    if (value === null || value === undefined) return '';
    const num = Number(value);
    if (!Number.isFinite(num)) return '';
    if (isGoldDiffMetric(metric)) {
      if (num > 0) return 'is-good';
      if (num < 0) return 'is-bad';
      return '';
    }
    return '';
  }

  function primaryBehaviorSignal(game) {
    const bad = (game.behaviors_bad || [])[0];
    if (bad) return { tone: 'bad', title: bad.title };
    const good = (game.behaviors_good || [])[0];
    if (good) return { tone: 'good', title: good.title };
    return null;
  }

  function lookupReportPlayerIcon(account) {
    const players = data.report_players || [];
    if (!account || !players.length) return null;
    const folded = String(account).toLowerCase();
    for (const member of players) {
      const label = member.label || '';
      if (label === account || label.toLowerCase() === folded) return member.profile_icon || null;
    }
    return null;
  }

  function gameReviewRowHtml(game) {
    const isWin = game.result === 'win';
    const resultClass = isWin ? 'game-review-result--win' : 'game-review-result--loss';
    const signal = primaryBehaviorSignal(game);
    const signalHtml = signal
      ? `<span class="game-review-signal game-review-signal--${signal.tone}">${escapeHtml(signal.title)}</span>`
      : '<span class="game-review-signal game-review-signal--none">—</span>';
    let accountHtml = '';
    if (isGroupReport && game.account) {
      const iconHref = game.account_icon || lookupReportPlayerIcon(game.account);
      const accountIcon = iconHref
        ? `<img src="${escapeHtml(iconHref)}" alt="" class="game-review-account-icon" width="16" height="16">`
        : '';
      accountHtml = `<span class="game-review-account">${accountIcon}<span>${escapeHtml(game.account)}</span></span>`;
    }
    return `<button type="button" class="game-review-row game-review-row--${isWin ? 'win' : 'loss'}${game.match_id === selectedMatchId ? ' is-selected' : ''}" data-match-id="${escapeHtml(game.match_id)}">` +
      `<span class="game-review-result ${resultClass}">${isWin ? 'W' : 'L'}</span>` +
      accountHtml +
      `<span class="game-review-icons">${iconCellHtml(game.champion || 'You', game.champion_icon)}<span class="game-review-vs">vs</span>${iconCellHtml(game.opponent || 'Opponent', game.opponent_icon)}</span>` +
      `<span class="game-review-rail-stats"><span class="game-review-kda">${escapeHtml(game.kda)}</span>` +
      `<span class="game-review-score">${(game.score && game.score.overall) || 0}</span></span>` +
      signalHtml + '</button>';
  }

  function handleListClick(event) {
    const row = event.target.closest('.game-review-row');
    if (row) selectedMatchId = row.getAttribute('data-match-id');
  }

  // --- Key Moments feed tab (deaths / fights / objectives) ---

  const EPIC_OBJECTIVE_IDS = { dragon: 1, elder: 1, baron: 1, herald: 1, grubs: 1 };

  function isStructureAsset(id) {
    return id && !EPIC_OBJECTIVE_IDS[id];
  }

  function objectiveSplitSwings(row) {
    const gains = row.trade_gain || [];
    const losses = row.trade_loss || [];
    return {
      hasSplitGain: gains.some(isStructureAsset),
      hasSplitLoss: losses.some(isStructureAsset),
    };
  }

  function objectiveOutcome(row) {
    const isGrubs = row.kind === 'grubs' && row.secured_count != null && row.objective_total != null;
    if (isGrubs) {
      const secured = Number(row.secured_count);
      const total = Number(row.objective_total) || 3;
      const share = total > 0 ? secured / total : 0;
      if (share >= 2 / 3) return { label: `${secured}/${total} secured`, tone: 'good' };
      if (share <= 1 / 3) return { label: `${secured}/${total} secured`, tone: 'bad' };
      return { label: `${secured}/${total} secured`, tone: 'mixed' };
    }
    const swings = objectiveSplitSwings(row);
    if (row.taken_by_team) {
      if (swings.hasSplitLoss) return { label: 'Trade', tone: 'mixed' };
      return { label: 'Secured', tone: 'good' };
    }
    if (swings.hasSplitGain) return { label: 'Trade', tone: 'mixed' };
    return { label: 'Lost', tone: 'bad' };
  }

  function objectivePresenceDetail(row, objectiveHints) {
    if (row.dead_before) {
      return { label: 'Died in setup', tone: 'bad', hint: objectiveHints['Died in setup window (45–10s)'] || '' };
    }
    if (row.present) {
      return { label: 'Present at pit', tone: 'good', hint: objectiveHints['Present'] || '' };
    }
    if (row.macro_role === 'split_pushing') {
      return { label: 'Split pushing', tone: 'warn', hint: objectiveHints['Split pushing'] || '' };
    }
    if (row.macro_role === 'defending_split') {
      let label = 'Defending split';
      if (row.defending_lane) label += ` (${row.defending_lane})`;
      return { label, tone: 'warn', hint: objectiveHints['Defending split'] || '' };
    }
    if (row.justified_absence) {
      return { label: 'Accounted absence', tone: 'warn', hint: '' };
    }
    return { label: 'Absent without pressure', tone: 'bad', hint: objectiveHints['Absent without pressure'] || '' };
  }

  function objectiveWardLabel(row) {
    const wardCount = Number(row.wards_before) || 0;
    return wardCount === 1
      ? 'You placed 1 ward near objective during setup'
      : `You placed ${wardCount} wards near objective during setup`;
  }

  function objectiveKindLabel(row) {
    return row.kind.charAt(0).toUpperCase() + row.kind.slice(1);
  }

  function objectiveGrubClass(row) {
    if (row.kind !== 'grubs' || row.secured_count == null || row.objective_total == null) return '';
    const bucket = Math.max(0, Math.min(3, Math.round(Number(row.secured_count) || 0)));
    return ` game-review-objective--grubs-${bucket}`;
  }

  // --- Overview tab ---

  function metricRowHtml(stats, statLabels, statHints, baselineByMetric, key) {
    const val = stats[key];
    if (val === null || val === undefined) return '';
    const label = statLabels[key] || key;
    const hint = statHints[key] || '';
    const tone = metricValueTone(key, val);
    const compare = baselineByMetric[key];
    let compareHtml = '<span class="game-review-stat-delta">— vs your avg</span>';
    if (compare) {
      const neutralDelta = compare.verdict === 'on_par' || compare.gap_label === 'close to your avg' || compare.gap_label === 'same as your avg';
      const deltaTone = neutralDelta ? '' : compare.verdict === 'above' ? 'is-good' : compare.verdict === 'below' ? 'is-bad' : '';
      const deltaText = compare.gap_label || (formatGameReviewMetricDelta(key, compare.delta) + ' vs your avg');
      compareHtml = `<span class="game-review-stat-delta ${deltaTone}"${(neutralDelta || !compare.gap_color) ? '' : ` style="color:${compare.gap_color}"`}>${escapeHtml(deltaText)}</span>`;
    }
    // A row the live Career block is judging gets a marker, so the reader can see
    // which of these numbers their current goal actually counts.
    const goal = goalByColumn[key];
    const goalHint = goal ? ` — Career goal: ${goal.text}` : '';
    const goalClass = goal ? ` game-review-stat-row--goal-${goal.outcome}` : '';
    const goalMark = goal
      ? (goal.outcome === 'met' ? '✓ ' : goal.outcome === 'missed' ? '✕ ' : '– ')
      : '';
    return `<div class="game-review-stat-row${goalClass}" title="${escapeHtml(hint + goalHint)}"><span class="game-review-stat-label">${goalMark}${escapeHtml(label)}</span>` +
      `<span class="game-review-stat-value ${tone}">${formatGameReviewMetricValue(key, val)}</span>${compareHtml}</div>`;
  }

  function renderGameReviewStructurePressureHtml(game) {
    const rows = game.structure_pressure || [];
    if (!rows.length) return '';
    const bars = rows.map((row) => {
      const height = Math.min(100, (Number(row.towers) || 0) * 35 + (Number(row.tower_damage) || 0) / 500);
      return `<div class="game-review-structure-bar" title="${Math.round(Number(row.tower_damage) || 0)} tower dmg">` +
        `<span class="game-review-structure-bar-fill" style="height:${height}%"></span>` +
        `<span class="game-review-structure-bar-label">${Math.round(Number(row.minute) || 0)}m</span></div>`;
    }).join('');
    return `<section class="game-review-stat-group game-review-structure-pressure"><h4 class="game-review-stat-group-title"><span>Structure pressure</span></h4>` +
      `<div class="game-review-structure-chart" id="game-review-structure-pressure">${bars}</div></section>`;
  }

  function renderGameReviewOverviewHtml(game, tooltips) {
    const stats = game.key_stats || {};
    const statLabels = tooltips.key_stats_labels || {};
    const statHints = tooltips.key_stats || {};
    const groups = tooltips.key_stats_groups || [];
    const baselineByMetric = {};
    (game.key_stats_vs_baseline || []).forEach((row) => {
      if (row && row.metric) baselineByMetric[row.metric] = row;
    });

    const groupedKeys = {};
    let groupsHtml = groups.map((group) => {
      const rows = (group.keys || []).map((key) => {
        groupedKeys[key] = true;
        return metricRowHtml(stats, statLabels, statHints, baselineByMetric, key);
      }).join('');
      if (!rows) return '';
      const icon = group.iconify
        ? `<iconify-icon icon="${escapeHtml(group.iconify)}" class="game-review-stat-group-icon" aria-hidden="true"></iconify-icon>`
        : '';
      return `<section class="game-review-stat-group"><h4 class="game-review-stat-group-title">${icon}<span>${escapeHtml(group.label || '')}</span></h4>` +
        `<div class="game-review-stat-list">${rows}</div></section>`;
    }).join('');

    const leftover = Object.keys(stats)
      .filter((key) => !groupedKeys[key] && stats[key] !== null && stats[key] !== undefined)
      .map((key) => metricRowHtml(stats, statLabels, statHints, baselineByMetric, key))
      .join('');
    if (leftover) {
      groupsHtml += `<section class="game-review-stat-group"><h4 class="game-review-stat-group-title"><span>Other</span></h4>` +
        `<div class="game-review-stat-list">${leftover}</div></section>`;
    }

    return `<div class="game-review-overview">${groupsHtml || '<p class="sub">No overview stats for this game.</p>'}` +
      renderGameReviewStructurePressureHtml(game) + '</div>';
  }

  // --- Story tab: timeline ---

  function buildGameReviewTimelineTraces(game, mode, metric) {
    const points = game.timeline || [];
    const minutes = points.map((point) => point.minute || 0);
    const allyKey = mode === 'team' ? `ally_${metric}` : metric;
    const enemyKey = mode === 'team' ? `enemy_${metric}` : `opp_${metric}`;
    const diffs = points.map((point) => (Number(point[allyKey]) || 0) - (Number(point[enemyKey]) || 0));
    const ahead = diffs.map((value) => (value > 0 ? value : 0));
    const behind = diffs.map((value) => (value < 0 ? value : 0));
    const modeLabel = mode === 'team' ? 'Team diff' : 'Lane diff';
    const maxAbs = diffs.reduce((best, value) => Math.max(best, Math.abs(value) || 0), 0) || 1;
    return [
      {
        x: minutes, y: ahead, type: 'scatter', mode: 'lines', name: 'Ahead',
        line: { color: 'rgba(65, 183, 140, 0.85)', width: 2 }, fill: 'tozeroy',
        fillgradient: { type: 'vertical', colorscale: [[0, 'rgba(65, 183, 140, 0.02)'], [1, 'rgba(65, 183, 140, 0.45)']], start: 0, stop: maxAbs },
        hoverinfo: 'skip', showlegend: false,
      },
      {
        x: minutes, y: behind, type: 'scatter', mode: 'lines', name: 'Behind',
        line: { color: 'rgba(224, 85, 99, 0.85)', width: 2 }, fill: 'tozeroy',
        fillgradient: { type: 'vertical', colorscale: [[0, 'rgba(224, 85, 99, 0.45)'], [1, 'rgba(224, 85, 99, 0.02)']], start: -maxAbs, stop: 0 },
        hoverinfo: 'skip', showlegend: false,
      },
      {
        x: minutes, y: diffs, type: 'scatter', mode: 'lines', name: modeLabel,
        line: { width: 1.5, color: 'rgba(232, 234, 242, 0.55)' },
        hovertemplate: `${modeLabel}: %{y:+.0f}<extra></extra>`, showlegend: false,
      },
    ];
  }

  function paintTimeline() {
    if (!chartEl || !selectedGame) return;
    if (!window.Plotly) {
      plotlyReady = false;
      return;
    }
    plotlyReady = true;
    const metricLabels = { gold: 'Gold', xp: 'XP', cs: 'CS' };
    const metricLabel = metricLabels[timelineMetric] || 'Gold';
    const modeLabel = timelineMode === 'team' ? 'team' : 'lane';
    const layout = {
      title: { text: `${metricLabel} diff (${modeLabel})`, font: { size: 14 } },
      margin: { t: 40, r: 16, b: 40, l: 56 },
      height: 340,
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: '#e8eaf2', family: 'Manrope, -apple-system, "Segoe UI", sans-serif' },
      xaxis: { title: 'Minute', gridcolor: '#2a2f40', zeroline: false },
      yaxis: { title: `${metricLabel} diff`, gridcolor: '#2a2f40', zeroline: true, zerolinecolor: 'rgba(232, 234, 242, 0.35)', zerolinewidth: 1 },
      showlegend: false,
    };
    window.Plotly.react(chartEl, timelineTraces, layout, { displayModeBar: false, responsive: true });
    resizePlotlySoon(chartEl);
  }

  function selectGameReviewTab(tab) {
    activeTab = tab;
    tick().then(() => {
      const panel = document.getElementById(`game-review-panel-${tab}`);
      if (panel) resizePlotlySoon(panel);
    });
  }

  $: games = data.game_review?.[data.queue_filter_default]?.games || [];
  $: available = !!data.game_review?.[data.queue_filter_default]?.available && games.length > 0;
  $: gamesCount = data.game_review?.[data.queue_filter_default]?.games_count || games.length;
  $: subtitleText = `Last ${gamesCount} game${gamesCount === 1 ? '' : 's'} — follows queue filter.`;

  $: isGroupReport = (data.report_players || []).length > 1;
  $: visibleGames = games.slice(0, 5);
  $: extraGames = games.slice(5);
  $: if (!moreOpenInitialized && games.length) {
    moreOpen = extraGames.some((g) => g.match_id === selectedMatchId);
    moreOpenInitialized = true;
  }
  $: listHtml = visibleGames.map(gameReviewRowHtml).join('');
  $: extraGamesHtml = extraGames.map(gameReviewRowHtml).join('');

  $: selectedGame = games.find((g) => g.match_id === selectedMatchId) || games[0];

  $: trackedGoals = careerGoalsForGame(career, selectedGame);
  $: goalByColumn = goalOutcomeByColumn(career, selectedGame);
  $: tooltips = data.game_review_tooltips || {};
  $: objectiveHints = tooltips.objectives || {};
  $: objectiveRows = selectedGame ? (selectedGame.objectives || []) : [];
  $: deathHints = tooltips.key_moments || {};
  $: deaths = selectedGame?.deaths || [];
  $: fights = selectedGame?.fights || [];
  $: score = selectedGame?.score || {};
  $: build = selectedGame?.build || {};
  $: dimensions = score.dimensions || [];
  $: keep = keepFix(selectedGame).keep;
  $: fixItems = keepFix(selectedGame).fix;
  function keepFix(game) {
    if (!game) return { keep: [], fix: [] };
    return { keep: (game.behaviors_good || []).slice(0, 2), fix: (game.behaviors_bad || []).slice(0, 2) };
  }

  $: metaBits = selectedGame ? [selectedGame.date, formatGameTime(selectedGame.duration_min), selectedGame.side] : [];
  $: hasMetaChip = !!selectedGame?.archetype;
  $: archetypeTone = selectedGame?.result === 'win' ? 'good' : 'bad';
  $: hasLoadoutTeaser = !!(build.keystone_icon || build.secondary_tree_icon || (build.summoners || []).length);

  $: items = build.items || [];
  $: itemIcons = build.item_icons || [];

  $: csStats = data.show_cs_stats ?? true;
  $: if (!csStats && timelineMetric === 'cs') timelineMetric = 'gold';
  $: timelineTraces = selectedGame ? buildGameReviewTimelineTraces(selectedGame, timelineMode, timelineMetric) : [];
  $: hasTimeline = !!(selectedGame?.timeline || []).length;
  $: if (chartEl && selectedGame && hasTimeline && timelineTraces) tick().then(paintTimeline);

  const gameReviewTabs = [
    { value: 'overview', label: 'Overview' },
    { value: 'story', label: 'Story' },
    { value: 'key-moments', label: 'Key Moments' },
    { value: 'loadout', label: 'Loadout' },
  ];

  $: timelineModeItems = [
    { value: 'lane', label: 'You vs opponent' },
    { value: 'team', label: 'Your team vs their team' },
  ];
  $: timelineMetricItems = [
    { value: 'gold', label: 'Gold' },
    { value: 'xp', label: 'XP' },
    ...(csStats ? [{ value: 'cs', label: 'CS' }] : []),
  ];
</script>

<section id="game-review" class="report-section report-section--games">
  <SectionHeader id="game-review" title="Game Review" icon="swords" scope="Last 10 games" lead={subtitleText} />
  {#if !available}
    <div id="game-review-unavailable" class="form-empty">
      <p>No ranked games in this queue for Game Review.</p>
    </div>
  {:else}
    <div id="game-review-content">
      <div class="game-review-layout">
        <aside class="game-review-rail" aria-label="Recent games">
          <!-- svelte-ignore a11y-click-events-have-key-events -->
          <!-- svelte-ignore a11y-no-static-element-interactions -->
          <!-- Each row is a real <button> (see gameReviewRowHtml) with native keyboard support;
               this on:click is a single delegated listener over the raw-HTML-rendered rows, not
               an interactive element in its own right, so it takes no role/tabindex/keydown of
               its own. -->
          <div class="game-review-list" id="game-review-list" on:click={handleListClick}>
            {@html listHtml}
            {#if extraGames.length}
              <ShowMore
                bind:open={moreOpen}
                indicator="icon"
                triggerClass="game-review-more-toggle"
                label={`Show ${extraGames.length} more`}
                openLabel="Show fewer"
                id="game-review-more"
                style="--show-more-content-display: flex; --show-more-content-gap: 8px;"
              >
                {@html extraGamesHtml}
              </ShowMore>
            {/if}
          </div>
        </aside>
        {#if selectedGame}
          <Panel id="game-review-detail">
            <GameSummaryHeader
              result={selectedGame.result === 'win' ? 'win' : 'loss'}
              kda={selectedGame.kda}
              score={score.overall || 0}
              metaText={metaBits.join(' · ')}
              {hasMetaChip}
              hasLoadout={hasLoadoutTeaser}
            >
              <svelte:fragment slot="champion"><IconCellSolo name={selectedGame.champion || 'You'} icon={selectedGame.champion_icon} /></svelte:fragment>
              <svelte:fragment slot="opponent"><IconCellSolo name={selectedGame.opponent || 'Opponent'} icon={selectedGame.opponent_icon} /></svelte:fragment>
              <svelte:fragment slot="score-chip"><Chip tone="flat" label={score.tier || '—'} /></svelte:fragment>
              <svelte:fragment slot="meta-chip">
                {#if hasMetaChip}<Chip tone={archetypeTone} label={selectedGame.archetype} />{/if}
              </svelte:fragment>
              <svelte:fragment slot="loadout">
                <GameReviewRuneDuo {build} />
                {#if (build.summoners || []).length}
                  <span class="game-review-rune-sep" aria-hidden="true">·</span>
                  <GameReviewSummoners {build} />
                {/if}
              </svelte:fragment>
            </GameSummaryHeader>

            <div class="game-review-verdict" id="game-review-verdict">
              {#if !keep.length && !fixItems.length}
                <p class="sub game-review-verdict-empty">No standout behavior flags for this game.</p>
              {:else}
                <div class="game-review-verdict-grid">
                  {#each fixItems as item}
                    <Callout tone="bad" edge label="Fix" title={item.title} body={item.detail} />
                  {/each}
                  {#each keep as item}
                    <Callout tone="good" edge label="Keep" title={item.title} body={item.detail} />
                  {/each}
                </div>
              {/if}
            </div>

            {#if trackedGoals.length}
              <a
                class="game-goals"
                href="#career"
                on:click={handleNavClick(reportNav, 'career')}
                title="Open Career mode"
              >
                <span class="game-goals-head">
                  <span class="game-goals-label">Career goals for this game</span>
                  <span class="game-goals-more">Career mode →</span>
                </span>
                <span class="game-goals-lead">
                  Whether this game met the live block's per-game bar. Only games after the block started counting toward the 20-game window.
                </span>
                <span class="game-goals-list">
                  {#each trackedGoals as goal (goal.column)}
                    <span class="game-goal game-goal--{goal.outcome}">
                      <span class="game-goal-mark" aria-hidden="true">
                        {goal.outcome === 'met' ? '✓' : goal.outcome === 'missed' ? '✕' : '–'}
                      </span>
                      <span class="game-goal-text">{goal.text}</span>
                      <span class="game-goal-verdict">
                        {goal.outcome === 'met'
                          ? 'this game hit'
                          : goal.outcome === 'missed'
                            ? 'this game missed'
                            : 'played before tracking'}
                      </span>
                    </span>
                  {/each}
                </span>
              </a>
            {/if}

            <div class="game-review-scoreboard" id="game-review-score-hero">
              <div class="gr-score-list">
                {#if dimensions.length}
                  {#each dimensions as dim (dim.name)}
                    <ScoreDisclosure
                      name={dim.name}
                      score={dim.score}
                      hint={dim.hint || (tooltips.score || {})[dim.name] || ''}
                      ingredients={dim.ingredients || []}
                      bind:open={scoreDetailsOpen}
                    />
                  {/each}
                {:else}
                  <p class="game-review-verdict-empty">No score dimensions available.</p>
                {/if}
              </div>
            </div>

            <SegmentedControl
              id="game-review-tabs"
              variant="pill"
              as="tablist"
              sticky
              items={gameReviewTabs}
              value={activeTab}
              on:select={(e) => selectGameReviewTab(e.detail.value)}
            />

            <div class="game-review-panel" id="game-review-panel-overview" role="tabpanel" hidden={activeTab !== 'overview'}>
              {@html renderGameReviewOverviewHtml(selectedGame, tooltips)}
            </div>

            <div class="game-review-panel" id="game-review-panel-story" role="tabpanel" hidden={activeTab !== 'story'}>
              <div class="game-review-story-block" id="game-review-key-moments-viewer">
                <GameReviewKeyMoments game={selectedGame} tooltips={tooltips} />
              </div>
              <hr class="game-review-story-sep" aria-hidden="true" />
              <div class="game-review-story-block" id="game-review-panel-timeline">
                {#if !hasTimeline}
                  <p class="sub">Timeline unavailable.</p>
                {:else}
                  <div class="game-review-timeline-toolbar">
                    <SegmentedControl
                      variant="inset"
                      ariaLabel="Compare mode"
                      items={timelineModeItems}
                      value={timelineMode}
                      on:select={(e) => (timelineMode = e.detail.value)}
                    />
                    <SegmentedControl
                      variant="inset"
                      ariaLabel="Resource"
                      items={timelineMetricItems}
                      value={timelineMetric}
                      on:select={(e) => (timelineMetric = e.detail.value)}
                    />
                  </div>
                  <div class="game-review-timeline-chart" id="game-review-timeline-chart" bind:this={chartEl}>
                    {#if !plotlyReady}<p class="sub">Chart library still loading…</p>{/if}
                  </div>
                {/if}
              </div>
            </div>

            <div class="game-review-panel" id="game-review-panel-key-moments" role="tabpanel" hidden={activeTab !== 'key-moments'}>
              <div class="game-review-feed-section">
                <h4 class="game-review-feed-title">Deaths</h4>
                {#if !deaths.length}
                  <p class="sub">No deaths recorded.</p>
                {:else}
                  <div class="game-review-feed">
                    {#each deaths as row}
                      {@const goldGiven = row.gold_given != null ? Number(row.gold_given) : null}
                      <div class="game-review-event game-review-event--death">
                        <span class="game-review-event-time">{formatGameTime(row.minute)}</span>
                        <div class="game-review-event-body">
                          <div class="game-review-death-line">
                            <span class="game-review-killed-by">Killed by
                              {#if row.killer_icon}
                                <IconCellSolo name={row.killer || 'Unknown'} icon={row.killer_icon} />
                              {:else}
                                <span>{row.killer || 'Unknown'}</span>
                              {/if}
                            </span>
                            {#if goldGiven != null && Number.isFinite(goldGiven)}
                              <span class="game-review-death-gold" title={deathHints.gold_given || ''}>Gave {Math.round(goldGiven).toLocaleString()}g</span>
                            {/if}
                            {#if (row.flags || []).length}
                              <span class="ui-chip-row">
                                {#each row.flags as flag}<Chip tone="warn" label={flag} />{/each}
                              </span>
                            {/if}
                          </div>
                        </div>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>

              <div class="game-review-feed-section">
                <h4 class="game-review-feed-title">Fights</h4>
                {#if !fights.length}
                  <p class="sub">No teamfights joined.</p>
                {:else}
                  <div class="game-review-feed">
                    {#each fights as row}
                      {@const fightTone = row.fight_won ? 'good' : 'bad'}
                      <div class="game-review-event game-review-event--{fightTone} game-review-event--subtle">
                        <span class="game-review-event-time">{formatGameTime(row.start_minute)}</span>
                        <div class="game-review-event-body">
                          <div class="game-review-event-headline">
                            <strong>{row.fight_won ? 'Fight won' : 'Fight lost'}</strong>
                            <div class="ui-chip-row">
                              <Chip tone="flat" label={`${row.kills}/${row.deaths}/${row.assists}`} />
                              <Chip tone="flat" label={`${Math.round(Number(row.damage) || 0).toLocaleString()} dmg`} />
                            </div>
                          </div>
                          <div class="game-review-fight-sides">
                            <ChampionIconStack names={row.ally_champions} icons={row.ally_icons} tone="ally" />
                            <span class="game-review-fight-vs">vs</span>
                            <ChampionIconStack names={row.enemy_champions} icons={row.enemy_icons} tone="enemy" />
                          </div>
                        </div>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>

              <div class="game-review-feed-section">
                <h4 class="game-review-feed-title">Objectives</h4>
                {#if !objectiveRows.length}
                  <p class="sub">No objectives tracked.</p>
                {:else}
                  <div class="game-review-objective-list">
                    {#each objectiveRows as row}
                      {@const outcome = objectiveOutcome(row)}
                      {@const presence = objectivePresenceDetail(row, objectiveHints)}
                      <Disclosure
                        variant="objective"
                        chevron="trailing"
                        class="game-review-objective game-review-objective--{outcome.tone}{objectiveGrubClass(row)}"
                        bind:open={objectivesOpen}
                      >
                        <svelte:fragment slot="summary">
                          <span class="game-review-objective-time">{formatGameTime(row.minute)}</span>
                          <span class="game-review-objective-kind"><IconCellSolo name={objectiveKindLabel(row)} icon={row.objective_icon} /><span>{objectiveKindLabel(row)}</span></span>
                          <span class="game-review-objective-outcome game-review-objective-outcome--{outcome.tone}">{outcome.label}</span>
                        </svelte:fragment>
                        <div class="game-review-objective-details">
                          <div class="game-review-objective-detail-row">
                            <span class="game-review-objective-detail-label">Your role</span>
                            <Chip tone={presence.tone} label={presence.label} title={presence.hint} />
                          </div>
                          {#if (row.trade_gain_labels || []).length || (row.trade_loss_labels || []).length}
                            <div class="game-review-objective-detail-row game-review-objective-detail-row--swings">
                              <span class="game-review-objective-detail-label">Map trade</span>
                              <div class="game-review-objective-swing-chips">
                                {#each row.trade_gain_labels || [] as label}<Chip tone="good" {label} />{/each}
                                {#each row.trade_loss_labels || [] as label}<Chip tone="bad" {label} />{/each}
                              </div>
                            </div>
                          {/if}
                          {#if (row.pit_ally_champions || []).length || (row.pit_enemy_champions || []).length}
                            <div class="game-review-objective-detail-row game-review-objective-detail-row--pit">
                              <span class="game-review-objective-detail-label">At pit{row.manpower_at_pit ? ` (${row.manpower_at_pit})` : ''}</span>
                              <div class="game-review-fight-sides">
                                <ChampionIconStack names={row.pit_ally_champions} icons={row.pit_ally_icons} tone="ally" />
                                <span class="game-review-fight-vs">vs</span>
                                <ChampionIconStack names={row.pit_enemy_champions} icons={row.pit_enemy_icons} tone="enemy" />
                              </div>
                            </div>
                          {/if}
                          {#if row.present && row.wards_before != null}
                            <div class="game-review-objective-detail-row">
                              <span class="game-review-objective-detail-label">Objective</span>
                              <Chip tone="flat" label={objectiveWardLabel(row)} title={objectiveHints['Wards during setup'] || objectiveHints['Wards before'] || ''} />
                            </div>
                          {/if}
                          {#if row.tp_available === true}
                            <div class="game-review-objective-detail-row">
                              <span class="game-review-objective-detail-label">Summoners</span>
                              <Chip tone="warn" label="TP available" title={objectiveHints['TP available'] || ''} />
                            </div>
                          {:else if row.tp_available === false}
                            <div class="game-review-objective-detail-row">
                              <span class="game-review-objective-detail-label">Summoners</span>
                              <Chip tone="flat" label="No TP" title={objectiveHints['No TP'] || ''} />
                            </div>
                          {/if}
                        </div>
                      </Disclosure>
                    {/each}
                  </div>
                {/if}
              </div>
            </div>

            <div class="game-review-panel" id="game-review-panel-loadout" role="tabpanel" hidden={activeTab !== 'loadout'}>
              <div class="cards game-review-loadout-cards">
                <div class="card card--wide"><div class="label">Runes</div><div class="value"><GameReviewRunePage {build} /></div></div>
                <div class="card card--wide game-review-loadout-utilities">
                  <div class="game-review-loadout-section"><div class="label">Summoners</div><div class="value"><GameReviewSummoners {build} /></div></div>
                  <div class="game-review-loadout-section">
                    <div class="label">Item path</div>
                    <div class="value core-cell">
                      {#if !items.length}
                        —
                      {:else}
                        {#each items as name, index}
                          {#if index > 0}<span class="core-arrow" aria-hidden="true">→</span>{/if}
                          <IconCellSolo {name} icon={itemIcons[index] || null} />
                        {/each}
                      {/if}
                    </div>
                  </div>
                </div>
                <div class="card card--full"><div class="label">Skill progression</div><div class="value"><SkillGrid build={build} /></div></div>
              </div>
            </div>
          </Panel>
        {/if}
      </div>
    </div>
  {/if}
</section>

<style>
  /* Live Career block: did this one game hit the bar those goals count? */
  .game-goals {
    display: grid;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
    padding: var(--space-4);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    color: inherit;
    text-decoration: none;
    transition: border-color .15s;
  }
  .game-goals:hover { border-color: var(--color-accent); }
  .game-goals-head { display: flex; align-items: baseline; gap: var(--space-3); }
  .game-goals-label {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--color-neutral-500);
  }
  .game-goals-more { margin-left: auto; font-size: 11px; color: var(--color-accent); }
  .game-goals-lead {
    font-size: 12px;
    line-height: 1.4;
    color: var(--color-neutral-400);
  }
  .game-goals-list { display: grid; gap: 6px; }
  .game-goal {
    display: grid;
    grid-template-columns: 16px minmax(0, 1fr) auto;
    align-items: baseline;
    gap: var(--space-2);
    font-size: 12px;
  }
  .game-goal-mark { font-weight: 700; text-align: center; }
  .game-goal--met .game-goal-mark, .game-goal--met .game-goal-verdict { color: var(--tone-good-fg); }
  .game-goal--missed .game-goal-mark, .game-goal--missed .game-goal-verdict { color: var(--tone-bad-fg); }
  .game-goal--untracked .game-goal-mark, .game-goal--untracked .game-goal-verdict {
    color: var(--color-neutral-600);
  }
  .game-goal-text { color: var(--color-text); min-width: 0; }
  .game-goal-verdict { font-size: 11px; white-space: nowrap; }

</style>
