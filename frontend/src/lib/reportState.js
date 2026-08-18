import { writable, derived, get } from 'svelte/store';

// A "bundle" is one queue+window slice from `report_views[queue].windows[window]`.
// Field names here mirror `bundle_to_template_context` in pipeline/bundles.py exactly,
// so switching queue/window client-side reproduces what the Jinja re-render used to do.
function bundleFields(bundle) {
  const peer = bundle.peer || null;
  return {
    total_games: bundle.total_games,
    patch_range: bundle.patch_range,
    queue_label: bundle.queue_label,
    overview: bundle.overview,
    score: bundle.score,
    score_color: bundle.score_color,
    score_verdict_label: bundle.score_verdict_label,
    score_components: bundle.score_components,
    figures: bundle.figures,
    overview_cards: bundle.overview_cards || [],
    section_verdicts: bundle.section_verdicts || {},
    lane_cards: bundle.lane_cards,
    early_section_title: bundle.early_section_title || 'Laning',
    section_order: bundle.section_order || [],
    economy_cards: bundle.economy_cards,
    vision_cards: bundle.vision_cards,
    death_cards: bundle.death_cards,
    positioning_cards: bundle.positioning_cards || [],
    positioning_hints: bundle.positioning_hints || [],
    teamfight_cards: bundle.teamfight_cards,
    objective_macro_cards: bundle.objective_macro_cards || [],
    objective_rows: bundle.objective_rows,
    objectives_section_icon: bundle.objectives_section_icon,
    blind_spots: bundle.blind_spots,
    build_paths: bundle.build_paths,
    rune_rows: bundle.rune_rows,
    matchup_rows: bundle.matchup_rows,
    positive_recommendations: bundle.positive_recommendations,
    negative_recommendations: bundle.negative_recommendations,
    top_tips: bundle.top_tips || [],
    figure_hints: bundle.figure_hints || {},
    has_peer_comparison: !!peer,
    peer_comparison: peer
      ? {
          tier: peer.tier,
          rank_label: peer.rank_label,
          rank_badge: peer.rank_badge,
          build_label: peer.build_label,
          source: peer.source,
          peer_games: peer.peer_games,
          peer_players: peer.peer_players,
          confidence: peer.confidence,
          strengths: peer.strengths,
          weaknesses: peer.weaknesses,
        }
      : null,
    peer_rows: peer ? peer.rows || [] : [],
    peer_rank_icon: peer ? peer.rank_icon : null,
    career: bundle.career || null,
  };
}

// Mirrors `progression_to_template_context` in pipeline/progression.py.
function progressionFields(preset) {
  const snap = (preset && preset.snapshot) || {};
  return {
    form_available: (preset && preset.available) || false,
    form_insufficient_reason: preset ? preset.insufficient_reason : null,
    form_sample_subtitle: preset ? preset.sample_subtitle : null,
    form_snapshot: snap,
    form_delta_rows: (preset && preset.delta_rows) || [],
    form_top_improved: (preset && preset.top_improved) || [],
    form_top_regressed: (preset && preset.top_regressed) || [],
    form_stories: (preset && preset.stories) || [],
    form_figures: (preset && preset.figures) || {},
  };
}

// One "source" bundles everything that switches together when the account filter
// changes: report_views / progression_views / game_review, keyed by queue.
function normalizeBaseSource(payload) {
  return {
    queue_filter_default: payload.queue_filter_default,
    report_views: payload.report_views || {},
    progression_views: payload.progression_views || {},
    game_review: payload.game_review || {},
  };
}

// Account-subset views (from account_filter.views[key] or the on-demand
// account-views endpoint) use `game_review_views` instead of `game_review` —
// normalize to the same shape as the base source.
function normalizeAccountSource(views) {
  return {
    queue_filter_default: views.queue_filter_default,
    report_views: views.report_views || {},
    progression_views: views.progression_views || {},
    game_review: views.game_review_views || {},
  };
}

export function resolveQueueKey(source, queueKey) {
  const view = source.report_views[queueKey];
  if (view && view.total_games) return queueKey;
  return source.queue_filter_default || queueKey;
}

export function resolveWindowKey(queueView, windowKey) {
  const options = (queueView && queueView.window_options) || [];
  const match = options.find((option) => option.key === windowKey && option.enabled);
  if (match) return windowKey;
  return queueView ? queueView.default_window : windowKey;
}

const EMPTY_QUEUE_VIEW = { windows: {}, window_options: [], total_games: 0, default_window: null };

export function buildEffectiveView(payload, source, queueKey, windowKey) {
  const resolvedQueue = resolveQueueKey(source, queueKey);
  const queueView = source.report_views[resolvedQueue] || EMPTY_QUEUE_VIEW;
  const resolvedWindow = resolveWindowKey(queueView, windowKey);
  const bundle = queueView.windows[resolvedWindow] || {};

  const progressionView = source.progression_views[resolvedQueue];
  const presetKey = progressionView && progressionView.default_preset;
  const preset = progressionView && presetKey ? progressionView.presets[presetKey] : null;

  return {
    ...payload,
    ...bundleFields(bundle),
    ...progressionFields(preset),
    game_review: source.game_review,
    queue_filter_default: resolvedQueue,
    game_window_default: resolvedWindow,
    game_window_total: queueView.total_games,
    game_window_options: queueView.window_options || [],
  };
}

/**
 * Shared reactive queue/window/account-filter state for one report payload.
 * `fetchAccountViews(accountKeys)` is called only for account subsets that
 * aren't already precomputed in `payload.account_filter.views`.
 */
export function createReportState(payload, { fetchAccountViews } = {}) {
  const baseSource = normalizeBaseSource(payload);
  const accountFilter = payload.account_filter || {};

  const initialCache = { all: baseSource };
  Object.entries(accountFilter.views || {}).forEach(([key, views]) => {
    initialCache[key] = normalizeAccountSource(views);
  });

  const queue = writable(payload.queue_filter_default);
  const gameWindow = writable(payload.game_window_default);
  const accountKey = writable(accountFilter.default_key || 'all');
  const accountViewsCache = writable(initialCache);
  const accountLoading = writable(false);
  const accountError = writable('');

  const activeSource = derived(
    [accountKey, accountViewsCache],
    ([$accountKey, $cache]) => $cache[$accountKey] || $cache.all
  );

  const view = derived(
    [queue, gameWindow, activeSource],
    ([$queue, $gameWindow, $source]) => buildEffectiveView(payload, $source, $queue, $gameWindow)
  );

  function selectQueue(key) {
    queue.set(key);
  }

  function selectWindow(key) {
    gameWindow.set(key);
  }

  async function selectAccountKey(key) {
    accountError.set('');
    if (get(accountViewsCache)[key]) {
      accountKey.set(key);
      return;
    }
    if (!fetchAccountViews) {
      accountError.set('This account combination is not available in this report.');
      return;
    }
    accountLoading.set(true);
    try {
      const views = await fetchAccountViews(key.split('|'));
      accountViewsCache.update((cache) => ({ ...cache, [key]: normalizeAccountSource(views) }));
      accountKey.set(key);
    } catch (err) {
      accountError.set('Could not load this account combination. Try again.');
    } finally {
      accountLoading.set(false);
    }
  }

  return {
    queue,
    gameWindow,
    accountKey,
    accountViewsCache,
    accountLoading,
    accountError,
    activeSource,
    view,
    selectQueue,
    selectWindow,
    selectAccountKey,
  };
}
