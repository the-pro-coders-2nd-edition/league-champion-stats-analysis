<script>
  import { tick } from 'svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import SkillGrid from '../components/SkillGrid.svelte';
  import Panel from '../components/Panel.svelte';
  import GameSummaryHeader from '../components/GameSummaryHeader.svelte';
  import ScoreDisclosure from '../components/ScoreDisclosure.svelte';
  import SegmentedControl from '../components/SegmentedControl.svelte';
  import GameReviewKeyMoments from './GameReviewKeyMoments.svelte';
  import { escapeHtml, soloIconCellHtml } from '../lib/html.js';
  import { formatGameTime, pct } from '../lib/format.js';
  import { resizePlotlySoon } from '../lib/plotlyResize.js';

  export let data;

  let selectedMatchId = data.game_review_selected_match_id ?? null;
  let activeTab = 'overview';
  let moreOpen = false;
  let moreOpenInitialized = false;
  let timelineMode = 'lane';
  let timelineMetric = 'gold';
  let chartEl;
  let plotlyReady = typeof window !== 'undefined' && !!window.Plotly;

  // `iconCell(name, iconHref, true)` from report.html — always icon-only in this section.
  const iconCellHtml = soloIconCellHtml;

  function chipHtml(label, tone, title) {
    const cls = tone ? `ui-chip ui-chip--${tone}` : 'ui-chip';
    const attrs = title ? ` title="${escapeHtml(title)}"` : '';
    return `<span class="${cls}"${attrs}>${escapeHtml(label)}</span>`;
  }

  function championIconStackHtml(names, icons, tone) {
    names = names || [];
    icons = icons || [];
    if (!names.length) return '<span class="game-review-champ-empty">—</span>';
    return `<span class="game-review-champ-stack game-review-champ-stack--${tone}">` +
      names.map((name, index) => iconCellHtml(name, icons[index] || null)).join('') +
      '</span>';
  }

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

  function renderGameReviewRunesHtml(build) {
    build = build || {};
    const keystone = iconCellHtml(build.keystone || 'Keystone', build.keystone_icon);
    const secondary = iconCellHtml(build.secondary_tree || 'Secondary', build.secondary_tree_icon);
    return `<div class="game-review-runes">${keystone}<span class="game-review-rune-sep" aria-hidden="true">+</span>${secondary}</div>`;
  }

  function renderRuneIconRowHtml(names, icons, emptyLabel) {
    names = names || [];
    icons = icons || [];
    if (!names.length) return `<span class="game-review-rune-empty">${escapeHtml(emptyLabel || '—')}</span>`;
    return names.map((name, index) => iconCellHtml(name, icons[index] || null)).join('');
  }

  function renderGameReviewRunePageHtml(build) {
    build = build || {};
    const hasFull = (build.primary_runes || []).length || (build.secondary_runes || []).length || (build.shards || []).length;
    if (!hasFull) return renderGameReviewRunesHtml(build);
    return `<div class="game-review-rune-page">` +
      `<div class="game-review-rune-tree"><div class="game-review-rune-tree-head">` +
      iconCellHtml(build.primary_tree || 'Primary', build.primary_tree_icon) +
      `<span class="game-review-rune-tree-label">Primary</span></div>` +
      `<div class="game-review-rune-row game-review-rune-row--keystone">${iconCellHtml(build.keystone || 'Keystone', build.keystone_icon)}</div>` +
      `<div class="game-review-rune-row">${renderRuneIconRowHtml(build.primary_runes, build.primary_rune_icons)}</div></div>` +
      `<div class="game-review-rune-tree"><div class="game-review-rune-tree-head">` +
      iconCellHtml(build.secondary_tree || 'Secondary', build.secondary_tree_icon) +
      `<span class="game-review-rune-tree-label">Secondary</span></div>` +
      `<div class="game-review-rune-row">${renderRuneIconRowHtml(build.secondary_runes, build.secondary_rune_icons)}</div></div>` +
      ((build.shards || []).length
        ? `<div class="game-review-rune-shards"><span class="game-review-rune-tree-label">Shards</span>` +
          `<div class="game-review-rune-row">${renderRuneIconRowHtml(build.shards, build.shard_icons)}</div></div>`
        : '') +
      '</div>';
  }

  function renderGameReviewSummonersHtml(build) {
    build = build || {};
    const icons = build.summoner_icons || [];
    const spells = build.summoners || [];
    if (!spells.length) return '—';
    return `<span class="game-review-summoners">` +
      spells.map((name, index) => iconCellHtml(name, icons[index] || null)).join('<span class="game-review-summoner-sep" aria-hidden="true">+</span>') +
      '</span>';
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
    const toggle = event.target.closest('.game-review-more-toggle');
    if (toggle) {
      moreOpen = !moreOpen;
      return;
    }
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
    if (row.trade_outcome === 'held') return { label: 'Held', tone: 'good' };
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

  function renderObjectiveDetailsHtml(row, objectiveHints) {
    const parts = [];
    const presence = objectivePresenceDetail(row, objectiveHints);
    parts.push(`<div class="game-review-objective-detail-row"><span class="game-review-objective-detail-label">Your role</span>${chipHtml(presence.label, presence.tone, presence.hint)}</div>`);

    const gainLabels = row.trade_gain_labels || [];
    const lossLabels = row.trade_loss_labels || [];
    if (gainLabels.length || lossLabels.length) {
      const swingChips = gainLabels.map((label) => chipHtml(label, 'good', ''))
        .concat(lossLabels.map((label) => chipHtml(label, 'bad', '')));
      parts.push(`<div class="game-review-objective-detail-row game-review-objective-detail-row--swings">` +
        `<span class="game-review-objective-detail-label">Map trade</span>` +
        `<div class="game-review-objective-swing-chips">${swingChips.join('')}</div></div>`);
    }

    const allies = row.pit_ally_champions || [];
    const enemies = row.pit_enemy_champions || [];
    if (allies.length || enemies.length) {
      parts.push(`<div class="game-review-objective-detail-row game-review-objective-detail-row--pit">` +
        `<span class="game-review-objective-detail-label">At pit${row.manpower_at_pit ? ` (${escapeHtml(row.manpower_at_pit)})` : ''}</span>` +
        `<div class="game-review-fight-sides">${championIconStackHtml(allies, row.pit_ally_icons, 'ally')}` +
        `<span class="game-review-fight-vs">vs</span>${championIconStackHtml(enemies, row.pit_enemy_icons, 'enemy')}</div></div>`);
    }

    if (row.present && row.wards_before != null) {
      const wardCount = Number(row.wards_before) || 0;
      const wardLabel = wardCount === 1
        ? 'You placed 1 ward near objective during setup'
        : `You placed ${wardCount} wards near objective during setup`;
      parts.push(`<div class="game-review-objective-detail-row"><span class="game-review-objective-detail-label">Objective</span>` +
        chipHtml(wardLabel, 'stat', objectiveHints['Wards during setup'] || objectiveHints['Wards before'] || '') + '</div>');
    }

    if (row.tp_available === true) {
      parts.push(`<div class="game-review-objective-detail-row"><span class="game-review-objective-detail-label">Summoners</span>` +
        chipHtml('TP available', 'warn', objectiveHints['TP available'] || '') + '</div>');
    } else if (row.tp_available === false) {
      parts.push(`<div class="game-review-objective-detail-row"><span class="game-review-objective-detail-label">Summoners</span>` +
        chipHtml('No TP', 'stat', objectiveHints['No TP'] || '') + '</div>');
    }

    return parts.join('');
  }

  function renderObjectiveRowHtml(row, objectiveHints) {
    const kindLabel = row.kind.charAt(0).toUpperCase() + row.kind.slice(1);
    const outcome = objectiveOutcome(row);
    let grubClass = '';
    if (row.kind === 'grubs' && row.secured_count != null && row.objective_total != null) {
      const bucket = Math.max(0, Math.min(3, Math.round(Number(row.secured_count) || 0)));
      grubClass = ` game-review-objective--grubs-${bucket}`;
    }
    return `<details class="game-review-objective game-review-objective--${outcome.tone}${grubClass}">` +
      `<summary class="game-review-objective-summary"><span class="game-review-objective-time">${formatGameTime(row.minute)}</span>` +
      `<span class="game-review-objective-kind">${iconCellHtml(kindLabel, row.objective_icon)}<span>${escapeHtml(kindLabel)}</span></span>` +
      `<span class="game-review-objective-outcome game-review-objective-outcome--${outcome.tone}">${escapeHtml(outcome.label)}</span></summary>` +
      `<div class="game-review-objective-details">${renderObjectiveDetailsHtml(row, objectiveHints)}</div></details>`;
  }

  function keyMomentsFeedHtml(game, tooltips) {
    const objectiveHints = tooltips.objectives || {};
    const deathHints = tooltips.key_moments || {};
    const deaths = game.deaths || [];
    const fights = game.fights || [];
    const objectives = game.objectives || [];
    const parts = [];

    parts.push('<div class="game-review-feed-section"><h4 class="game-review-feed-title">Deaths</h4>');
    if (!deaths.length) {
      parts.push('<p class="sub">No deaths recorded.</p>');
    } else {
      parts.push('<div class="game-review-feed">' + deaths.map((row) => {
        const flags = (row.flags || []).map((flag) => chipHtml(flag, 'flag')).join('');
        const goldGiven = row.gold_given != null ? Number(row.gold_given) : null;
        const goldHtml = (goldGiven != null && Number.isFinite(goldGiven))
          ? `<span class="game-review-death-gold" title="${escapeHtml(deathHints.gold_given || '')}">Gave ${Math.round(goldGiven).toLocaleString()}g</span>`
          : '';
        return `<div class="game-review-event game-review-event--death"><span class="game-review-event-time">${formatGameTime(row.minute)}</span>` +
          `<div class="game-review-event-body"><div class="game-review-death-line"><span class="game-review-killed-by">Killed by ` +
          (row.killer_icon ? iconCellHtml(row.killer || 'Unknown', row.killer_icon) : `<span>${escapeHtml(row.killer || 'Unknown')}</span>`) +
          `</span>${goldHtml}${flags ? `<span class="ui-chip-row">${flags}</span>` : ''}</div></div></div>`;
      }).join('') + '</div>');
    }
    parts.push('</div>');

    parts.push('<div class="game-review-feed-section"><h4 class="game-review-feed-title">Fights</h4>');
    if (!fights.length) {
      parts.push('<p class="sub">No teamfights joined.</p>');
    } else {
      parts.push('<div class="game-review-feed">' + fights.map((row) => {
        const tone = row.fight_won ? 'good' : 'bad';
        const verdict = row.fight_won ? 'Fight won' : 'Fight lost';
        const stats = [
          chipHtml(`${row.kills}/${row.deaths}/${row.assists}`, 'stat'),
          chipHtml(`${Math.round(Number(row.damage) || 0).toLocaleString()} dmg`, 'stat'),
        ].join('');
        return `<div class="game-review-event game-review-event--${tone} game-review-event--subtle">` +
          `<span class="game-review-event-time">${formatGameTime(row.start_minute)}</span>` +
          `<div class="game-review-event-body"><div class="game-review-event-headline"><strong>${verdict}</strong>` +
          `<div class="ui-chip-row">${stats}</div></div>` +
          `<div class="game-review-fight-sides">${championIconStackHtml(row.ally_champions, row.ally_icons, 'ally')}` +
          `<span class="game-review-fight-vs">vs</span>${championIconStackHtml(row.enemy_champions, row.enemy_icons, 'enemy')}</div></div></div>`;
      }).join('') + '</div>');
    }
    parts.push('</div>');

    parts.push('<div class="game-review-feed-section"><h4 class="game-review-feed-title">Objectives</h4>');
    if (!objectives.length) {
      parts.push('<p class="sub">No objectives tracked.</p>');
    } else {
      parts.push('<div class="game-review-objective-list">' +
        objectives.map((row) => renderObjectiveRowHtml(row, objectiveHints)).join('') + '</div>');
    }
    parts.push('</div>');
    return parts.join('');
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
    return `<div class="game-review-stat-row" title="${escapeHtml(hint)}"><span class="game-review-stat-label">${escapeHtml(label)}</span>` +
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
        line: { color: 'rgba(63, 182, 139, 0.85)', width: 2 }, fill: 'tozeroy',
        fillgradient: { type: 'vertical', colorscale: [[0, 'rgba(63, 182, 139, 0.02)'], [1, 'rgba(63, 182, 139, 0.45)']], start: 0, stop: maxAbs },
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
  $: listHtml = visibleGames.map(gameReviewRowHtml).join('') +
    (extraGames.length
      ? `<div class="game-review-more${moreOpen ? ' is-open' : ''}" id="game-review-more">${extraGames.map(gameReviewRowHtml).join('')}</div>` +
        `<button type="button" class="game-review-more-toggle${moreOpen ? ' is-expanded' : ''}" aria-expanded="${moreOpen ? 'true' : 'false'}" aria-controls="game-review-more" data-extra-count="${extraGames.length}">` +
        `<iconify-icon icon="lucide:chevron-${moreOpen ? 'up' : 'down'}" aria-hidden="true"></iconify-icon>` +
        `<span class="game-review-more-label">${moreOpen ? 'Show fewer' : `Show ${extraGames.length} more`}</span></button>`
      : '');

  $: selectedGame = games.find((g) => g.match_id === selectedMatchId) || games[0];

  $: tooltips = data.game_review_tooltips || {};
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
  $: archetypeChip = selectedGame?.archetype ? chipHtml(selectedGame.archetype, selectedGame.result === 'win' ? 'good' : 'bad') : '';
  $: hasLoadoutTeaser = !!(build.keystone_icon || build.secondary_tree_icon || (build.summoners || []).length);

  $: itemsHtml = build.items?.length
    ? build.items.map((name, index) => iconCellHtml(name, (build.item_icons || [])[index] || null)).join('<span class="core-arrow" aria-hidden="true">→</span>')
    : '';

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
          <div class="game-review-list" id="game-review-list" on:click={handleListClick}>
            {@html listHtml}
          </div>
        </aside>
        {#if selectedGame}
          <Panel id="game-review-detail">
            <GameSummaryHeader
              result={selectedGame.result === 'win' ? 'win' : 'loss'}
              kda={selectedGame.kda}
              score={score.overall || 0}
              metaText={metaBits.join(' · ')}
              hasMetaChip={!!archetypeChip}
              hasLoadout={hasLoadoutTeaser}
            >
              <svelte:fragment slot="champion">{@html iconCellHtml(selectedGame.champion || 'You', selectedGame.champion_icon)}</svelte:fragment>
              <svelte:fragment slot="opponent">{@html iconCellHtml(selectedGame.opponent || 'Opponent', selectedGame.opponent_icon)}</svelte:fragment>
              <svelte:fragment slot="score-chip">{@html chipHtml(score.tier || '—', 'stable')}</svelte:fragment>
              <svelte:fragment slot="meta-chip">{@html archetypeChip}</svelte:fragment>
              <svelte:fragment slot="loadout">
                {@html renderGameReviewRunesHtml(build)}
                {#if (build.summoners || []).length}
                  <span class="game-review-rune-sep" aria-hidden="true">·</span>
                  {@html renderGameReviewSummonersHtml(build)}
                {/if}
              </svelte:fragment>
            </GameSummaryHeader>

            <div class="game-review-verdict" id="game-review-verdict">
              {#if !keep.length && !fixItems.length}
                <p class="sub game-review-verdict-empty">No standout behavior flags for this game.</p>
              {:else}
                <div class="game-review-verdict-grid">
                  {#each fixItems as item}
                    <div class="game-review-callout game-review-callout--fix">
                      <span class="game-review-callout-label">Fix</span>
                      <strong class="game-review-callout-title">{item.title}</strong>
                      <span class="game-review-callout-detail">{item.detail}</span>
                    </div>
                  {/each}
                  {#each keep as item}
                    <div class="game-review-callout game-review-callout--keep">
                      <span class="game-review-callout-label">Keep</span>
                      <strong class="game-review-callout-title">{item.title}</strong>
                      <span class="game-review-callout-detail">{item.detail}</span>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="game-review-scoreboard" id="game-review-score-hero">
              <div class="gr-score-list">
                {#if dimensions.length}
                  {#each dimensions as dim (dim.name)}
                    <ScoreDisclosure
                      name={dim.name}
                      score={dim.score}
                      hint={dim.hint || (tooltips.score || {})[dim.name] || ''}
                      ingredients={dim.ingredients || []}
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
              {@html keyMomentsFeedHtml(selectedGame, tooltips)}
            </div>

            <div class="game-review-panel" id="game-review-panel-loadout" role="tabpanel" hidden={activeTab !== 'loadout'}>
              <div class="cards game-review-loadout-cards">
                <div class="card card--wide"><div class="label">Runes</div><div class="value">{@html renderGameReviewRunePageHtml(build)}</div></div>
                <div class="card card--wide game-review-loadout-utilities">
                  <div class="game-review-loadout-section"><div class="label">Summoners</div><div class="value">{@html renderGameReviewSummonersHtml(build)}</div></div>
                  <div class="game-review-loadout-section"><div class="label">Item path</div><div class="value core-cell">{@html itemsHtml || '—'}</div></div>
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
