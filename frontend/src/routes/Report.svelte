<script>
  import { onDestroy, onMount, setContext, tick } from 'svelte';
  import { createReportNav, REPORT_NAV_KEY } from '../lib/reportNav.js';
  import { computeWindowScopeLabel, WINDOW_SCOPE_KEY } from '../lib/windowScope.js';
  import { resolveCareerView } from '../lib/careerView.js';
  import { get, writable } from 'svelte/store';
  import { replace } from 'svelte-spa-router';
  import {
    fetchBuild,
    fetchAccountViews,
    fetchPlayerStatus,
    subscribePlayerStatus,
    refreshPlayer,
    sendChatMessage,
    ackCareerRecap,
  } from '../lib/api.js';
  import { createReportState } from '../lib/reportState.js';
  import { getCachedBuild, setCachedBuild, invalidateIfStale } from '../lib/buildCache.js';
  import SegmentedControl from '../components/SegmentedControl.svelte';
  import AccountFilter from '../sections/AccountFilter.svelte';
  import Chatbot from '../sections/Chatbot.svelte';
  import Overview from '../sections/Overview.svelte';
  import Coaching from '../sections/Coaching.svelte';
  import FormTracker from '../sections/FormTracker.svelte';
  import RankPeers from '../sections/RankPeers.svelte';
  import Matchups from '../sections/Matchups.svelte';
  import ItemsRunes from '../sections/ItemsRunes.svelte';
  import LaneObjectivesDeaths from '../sections/LaneObjectivesDeaths.svelte';
  import VisionEconomyTeamfightsPositioning from '../sections/VisionEconomyTeamfightsPositioning.svelte';
  import GameReview from '../sections/GameReview.svelte';
  import Graphs from '../sections/Graphs.svelte';
  import CareerMode from '../sections/CareerMode.svelte';
  import RecapModal from '../components/RecapModal.svelte';
  import WelcomeBackToast from '../components/WelcomeBackToast.svelte';
  import ReportSkeleton from '../components/ReportSkeleton.svelte';
  import AppNav from '../components/AppNav.svelte';
  import { bindPlotlyDetailsResize, resizePlotlySoon } from '../lib/plotlyResize.js';

  export let params = {};

  const ACTIVE_JOB_STATES = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];
  const ACTIVE_PEER_STATES = ['report_ready', 'peer_running'];

  let unsubscribeStatus = null;
  let subscribedStatusSlug = null;
  let peerStageDetail = '';
  let peerFailed = false;
  let peerUnavailable = false;
  let statusBannerVisible = false;
  let statusBannerText = '';
  let refreshing = false;
  let jobActive = false;
  let careerPendingSlot = null;
  let dismissedRecapId = null;
  // The most recent non-null `welcome_back` payload from a status push -- server-side
  // it's a consume-on-read cache (see `WelcomeBackCache.get`), so it is only ever
  // handed to the client once; kept here until the toast itself dismisses it.
  let welcomeBack = null;

  const RECAP_DISMISS_PREFIX = 'recap-dismissed:';

  function loadRecapDismissal(key) {
    try {
      return localStorage.getItem(RECAP_DISMISS_PREFIX + key);
    } catch {
      return null;
    }
  }

  function saveRecapDismissal(key, matchId) {
    try {
      localStorage.setItem(RECAP_DISMISS_PREFIX + key, matchId);
    } catch {
      // Storage unavailable (private browsing, quota) -- the ack still hits the
      // server; only cross-reload suppression on this device is lost.
    }
  }

  function statusSlugFromEndpoint(endpoint) {
    if (!endpoint) return null;
    const match = endpoint.match(/\/api\/players\/([^/]+)$/);
    return match ? match[1] : null;
  }

  async function reloadBuild() {
    const prevQueue = report ? get(report.queue) : null;
    const prevWindow = report ? get(report.gameWindow) : null;
    const prevAccount = report ? get(report.accountKey) : null;
    const result = await fetchBuild(params.slug, params.buildSlug);
    // This runs when a pushed status update detects the report was regenerated -- the
    // cached entry for this exact build must be refreshed too, or switching away and back
    // would show the stale pre-regeneration data instead of what just landed.
    setCachedBuild(`${params.slug}/${params.buildSlug}`, result);
    payload = result;
    report = createReportState(payload, {
      fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
    });
    careerPendingSlot = null;
    if (prevQueue) report.selectQueue(prevQueue);
    if (prevWindow) report.selectWindow(prevWindow);
    if (prevAccount) await report.selectAccountKey(prevAccount);
  }

  async function applyStatusMessage(data) {
    const slug = statusSlugFromEndpoint(payload?.status_endpoint);
    if (!slug) return;
    try {
      playerBuilds = data.builds || [];
      peerFailed = !!data.peer_failed;
      if (data.welcome_back) welcomeBack = data.welcome_back;

      // This status push is cheap (metadata only) and already carries a fresh
      // generated_at for every build, not just the one on screen -- use it to drop any
      // OTHER cached build whose data just went stale, so a later switch to it re-fetches
      // instead of serving pre-regeneration data. The active build's own staleness is
      // still handled below via reloadBuild(), which also writes the cache through.
      (data.builds || []).forEach((entry) => {
        if (entry.slug !== params.buildSlug) {
          invalidateIfStale(`${slug}/${entry.slug}`, entry.generated_at);
        }
      });

      const build = (data.builds || []).find((entry) => entry.slug === params.buildSlug);
      const job = data.active_job;
      jobActive = !!(job && ACTIVE_JOB_STATES.includes(job.state));

      if (build?.generated_at && payload.generated_at && build.generated_at !== payload.generated_at) {
        await reloadBuild();
        refreshing = false;
        statusBannerVisible = false;
        peerStageDetail = '';
        return;
      }

      if (jobActive) {
        statusBannerVisible = true;
        statusBannerText = job.stage_detail || (
          job.state === 'report_ready' || job.state === 'peer_running'
            ? 'Comparing you to players at your rank…'
            : 'Analysis in progress…'
        );
        if (ACTIVE_PEER_STATES.includes(job.state)) {
          peerStageDetail = job.stage_detail || '';
          peerUnavailable = false;
        }
        return;
      }

      if (!refreshing) {
        statusBannerVisible = false;
      }

      if (build?.peers_ready && !payload.has_peer_comparison) {
        await reloadBuild();
        peerStageDetail = '';
        peerFailed = false;
        peerUnavailable = false;
        return;
      }

      if (data.peer_failed) {
        peerStageDetail = '';
        return;
      }

      if (data.peer_completed_at && !build?.peers_ready) {
        peerUnavailable = true;
        peerStageDetail = '';
      }
    } catch {
      // Transient errors -- the next pushed snapshot will still arrive.
    }
  }

  function ensureStatusStream() {
    const slug = statusSlugFromEndpoint(payload?.status_endpoint);
    if (!slug || slug === subscribedStatusSlug) return;
    if (unsubscribeStatus) unsubscribeStatus();
    subscribedStatusSlug = slug;
    unsubscribeStatus = subscribePlayerStatus(slug, applyStatusMessage);
  }

  onDestroy(() => {
    if (unsubscribeStatus) unsubscribeStatus();
  });

  function resetStatusBannerState() {
    peerStageDetail = '';
    peerFailed = false;
    peerUnavailable = false;
    statusBannerVisible = false;
    statusBannerText = '';
    refreshing = false;
    jobActive = false;
  }

  function handleCareerDropped(result) {
    // The drop is performed by the regenerate job it enqueued, so the ladder only
    // changes once that run rewrites the report. `applyStatusMessage` already
    // reloads on a new generated_at, pushed the moment that job's enqueue publishes
    // -- no explicit reconnect/refresh needed here. Until then the dropped slot
    // renders as a skeleton rather than a block that is already gone.
    careerPendingSlot = result?.dropped_slot ?? null;
    jobActive = true;
    statusBannerVisible = true;
    statusBannerText = result?.job?.stage_detail || 'Rebuilding your Career ladder…';
  }


  async function handleRecapClose() {
    const recap = careerLadder?.pending_recap;
    if (!recap) return;
    dismissedRecapId = recap.newest_match_id;
    saveRecapDismissal(loadedKey, recap.newest_match_id);
    const live = (careerLadder.blocks || []).find((b) => b.is_active);
    const hits = {};
    (live?.goals || []).forEach((goal) => {
      hits[goal.column] = goal.hit;
    });
    try {
      await ackCareerRecap(params.slug, params.buildSlug, {
        matchId: recap.newest_match_id,
        gameMs: recap.newest_game_ms,
        hits,
        trackKey: live?.track_key || '',
      });
    } catch {
      // A failed ack just means the recap can resurface next load; not fatal.
    }
  }

  async function handleRefresh() {
    if (refreshing || jobActive || !payload?.refresh_champion || !payload?.refresh_role) return;
    refreshing = true;
    statusBannerVisible = true;
    statusBannerText = 'Queueing refresh…';
    try {
      const result = await refreshPlayer(params.slug, {
        champion: payload.refresh_champion,
        role: payload.refresh_role,
      });
      statusBannerText = result.job?.stage_detail || 'Fetching latest games…';
      refreshing = false;
    } catch (err) {
      refreshing = false;
      statusBannerText = err.message || 'Refresh failed.';
      setTimeout(() => {
        if (!jobActive) statusBannerVisible = false;
      }, 5000);
    }
  }

  let payload = null;
  let error = null;
  let report = null;
  // True from the moment a build switch starts until its fetch resolves. `payload`/`report`
  // deliberately keep the previous build's data during that window (see RFC-003) so a
  // fast rebuild doesn't flash empty -- this flag alone gates the skeleton instead.
  let switchingBuild = false;
  let playerBuilds = [];
  let playerBuildsLoading = true;
  let playerPageHref = null;
  let stickyHeaderEl = null;

  function syncStickyOffset() {
    if (!stickyHeaderEl) return;
    const height = Math.ceil(stickyHeaderEl.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--report-sticky-offset', `${height}px`);
    stickyHeaderEl.classList.toggle('is-scrolled', window.scrollY > 8);
  }

  // `params` is a fresh object on every route match, so a bare `$:` on its fields
  // would refire on any unrelated reactivity tick; only refetch when the actual
  // slug/build pair changes.
  let loadedKey = '';
  $: {
    const key = `${params.slug}/${params.buildSlug}`;
    if (key !== loadedKey) {
      loadedKey = key;
      resetStatusBannerState();
      // Career's on-disk pending_recap only clears the next time this build's
      // report actually rebuilds (a new game, or a regenerate) -- a watch tick
      // with nothing new for THIS build re-serves the same stale report.json,
      // ack or not. Remembering the dismissal here keeps a reload from
      // re-showing a recap the reader already closed.
      dismissedRecapId = loadRecapDismissal(key);
      const applyPayload = (result) => {
        payload = result;
        report = createReportState(payload, {
          fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
        });
        if (payload.status_endpoint) {
          ensureStatusStream();
        }
      };
      // A build already visited this session is served from the in-memory cache --
      // instant, no skeleton, no re-paying the fetch cost for something already had.
      const cached = getCachedBuild(key);
      if (cached) {
        // A still-in-flight fetch from a previous switch may have left this true; the
        // content we're about to show is ready right now, so the skeleton must not
        // stay up waiting for that unrelated fetch to settle.
        switchingBuild = false;
        applyPayload(cached);
      } else {
        switchingBuild = true;
        const slug = params.slug;
        const buildSlug = params.buildSlug;
        fetchBuild(slug, buildSlug)
          .then((result) => {
            setCachedBuild(key, result);
            // A switch back to an earlier build (served instantly from cache) can
            // resolve before this fetch does; without this check the late response
            // would still land and silently overwrite whatever the user is looking at
            // now with stale data for a build they've already navigated away from.
            if (key === loadedKey) applyPayload(result);
          })
          .catch((err) => { if (key === loadedKey) error = err; })
          .finally(() => { if (key === loadedKey) switchingBuild = false; });
      }
    }
  }

  $: if ($view?.has_peer_comparison) {
    peerStageDetail = '';
    peerUnavailable = false;
  }

  $: showRefresh = !!(
    payload?.status_endpoint && payload?.refresh_champion && payload?.refresh_role
  );
  $: refreshDisabled = refreshing || jobActive;

  let loadedStatusSlug = '';
  $: if (params.slug !== loadedStatusSlug) {
    loadedStatusSlug = params.slug;
    playerBuildsLoading = true;
    fetchPlayerStatus(params.slug)
      .then((status) => {
        playerBuilds = status.builds || [];
        playerPageHref = `/players/${params.slug}`;
        if (status.welcome_back) welcomeBack = status.welcome_back;
      })
      .catch(() => {
        playerBuilds = [];
      })
      .finally(() => {
        playerBuildsLoading = false;
      });
  }

  let stickyResizeObserver = null;

  function attachStickyHeader(el) {
    if (stickyResizeObserver) {
      stickyResizeObserver.disconnect();
      stickyResizeObserver = null;
    }
    stickyHeaderEl = el;
    if (!el) return;
    syncStickyOffset();
    if (typeof ResizeObserver !== 'undefined') {
      stickyResizeObserver = new ResizeObserver(syncStickyOffset);
      stickyResizeObserver.observe(el);
    }
  }

  function stickyHeaderAction(node) {
    attachStickyHeader(node);
    return {
      destroy() {
        attachStickyHeader(null);
      },
    };
  }

  onMount(() => {
    const unbindPlotlyResize = bindPlotlyDetailsResize();
    window.addEventListener('scroll', syncStickyOffset, { passive: true });
    window.addEventListener('resize', syncStickyOffset);
    return () => {
      unbindPlotlyResize();
      window.removeEventListener('scroll', syncStickyOffset);
      window.removeEventListener('resize', syncStickyOffset);
      stickyResizeObserver?.disconnect();
    };
  });

  $: if (stickyHeaderEl && (statusBannerVisible !== undefined || payload)) {
    tick().then(syncStickyOffset);
  }

  const REPORT_CATEGORIES = [
    { value: 'summary', label: 'Summary' },
    { value: 'games', label: 'Games' },
    { value: 'career', label: 'Career' },
    { value: 'performance', label: 'Performance' },
    { value: 'champion', label: 'Champion' },
    { value: 'deepdive', label: 'Deepdive' },
  ];
  // The URL's tab segment is these exact category values -- no separate slug table
  // to keep in sync with them (the Career copy drifted from its own constants once
  // already; this file doesn't need a second version of that mistake).
  const TAB_VALUES = REPORT_CATEGORIES.map((category) => category.value);

  /** A stale/hand-edited/garbage tab segment must render, not crash -- fall back to
   *  summary rather than leaving the page blank. */
  function tabFromParams(rawTab) {
    return TAB_VALUES.includes(rawTab) ? rawTab : 'summary';
  }

  let activeCategory = tabFromParams(params.tab);
  $: categoryTabs = REPORT_CATEGORIES.map((category) => ({
    ...category,
    ariaControls: `category-${category.value}`,
  }));
  function selectCategory(value, { updateUrl = true } = {}) {
    activeCategory = value;
    if (updateUrl && params.slug && params.buildSlug) {
      // replace(), not push(): a tab switch should not add a browser-history entry
      // (the back button should leave the report, not step back through tabs) --
      // it only needs the address bar to reflect the tab so it can be bookmarked.
      replace(`/players/${params.slug}/${params.buildSlug}/${value}`);
    }
    tick().then(() => {
      const panel = document.getElementById(`category-${value}`);
      if (panel) resizePlotlySoon(panel);
    });
  }

  // Keeps activeCategory in sync when the URL's tab segment changes without this
  // component remounting -- a real navigation (typed URL, `use:link` anchor) to a
  // different tab, or to a different build whose URL omits one entirely. Guarded
  // on the raw params.tab *value*, not activeCategory: selectCategory's own
  // `activeCategory = value` assignment would otherwise re-trigger this same block
  // on every tab click, reading the not-yet-updated params.tab and immediately
  // reverting the click.
  let lastSyncedTab = params.tab;
  $: if (params.tab !== lastSyncedTab) {
    lastSyncedTab = params.tab;
    selectCategory(tabFromParams(params.tab), { updateUrl: false });
  }

  function resolveGameReviewMatch(matchId) {
    if (!report) return;
    const viewData = get(report.view);
    const review = viewData?.game_review || {};
    const currentQueue = get(report.queue);
    if (review[currentQueue]?.games?.some((game) => game.match_id === matchId)) return;
    for (const [key, bundle] of Object.entries(review)) {
      if (bundle?.games?.some((game) => game.match_id === matchId)) {
        report.selectQueue(key);
        return;
      }
    }
  }

  const reportNav = createReportNav(selectCategory, { resolveGameReviewMatch });
  setContext(REPORT_NAV_KEY, reportNav);

  const windowScopeLabel = writable('');
  setContext(WINDOW_SCOPE_KEY, windowScopeLabel);

  $: queue = report ? report.queue : null;
  $: gameWindow = report ? report.gameWindow : null;
  $: accountKey = report ? report.accountKey : null;
  $: accountLoading = report ? report.accountLoading : null;
  $: accountError = report ? report.accountError : null;
  $: activeSource = report ? report.activeSource : null;
  $: view = report ? report.view : null;
  $: windowScopeLabel.set($view ? computeWindowScopeLabel($view) : '');
  // Reports generated before every slice carried the ladder only have one in
  // the all-ranked views, so resolve it from the payload rather than the slice.
  $: careerLadder = resolveCareerView(payload, $view ? $view.career : null);
  // Dismissed locally the instant the modal closes, so the recap does not
  // reopen before the ack round-trip lands and the server stops sending it.
  $: recapCareer = careerLadder?.pending_recap?.newest_match_id === dismissedRecapId
    ? null
    : careerLadder;
  $: gameReviewAll = $view ? $view.game_review?.all : null;

  $: queueItems = (payload && $activeSource)
    ? (payload.queue_filter_options || []).map((option) => ({
        value: option.key,
        label: option.label,
        disabled: !($activeSource.report_views[option.key] && $activeSource.report_views[option.key].total_games),
      }))
    : [];

  $: windowItems = ($view ? $view.game_window_options || [] : []).map((option) => ({
    value: option.key,
    label: option.label,
    disabled: !option.enabled,
  }));

  function selectQueue(option) {
    report.selectQueue(option.value);
  }

  function selectWindow(option) {
    report.selectWindow(option.value);
  }
</script>

<div class="layout">
<AppNav
  builds={playerBuilds}
  playerSlug={params.slug}
  activeBuildSlug={params.buildSlug}
  backHref={playerPageHref || '/'}
  backLabel={playerPageHref ? '← All champions' : '← Reports'}
  loading={playerBuildsLoading && playerBuilds.length === 0}
/>
<main>

<WelcomeBackToast data={welcomeBack} onDismiss={() => { welcomeBack = null; }} />

{#if error}
  <p class="report-error">Failed to load this report.</p>
{:else if payload === null || !$view}
  <ReportSkeleton category={activeCategory} />
{:else}
  <div
    class="report-sticky-header"
    id="report-sticky-header"
    use:stickyHeaderAction
  >
    {#if payload.status_endpoint && statusBannerVisible}
      <div id="web-status-banner">
        <span id="web-status-banner-text">{statusBannerText}</span>
      </div>
    {/if}

    <div class="report-filter-bar" id="report-filter-bar">
      <div class="filter-group" id="queue-filter-bar">
        <span class="game-window-label">Queue</span>
        <SegmentedControl
          items={queueItems}
          value={$view.queue_filter_default}
          variant="pill"
          size="sm"
          on:select={(event) => selectQueue(event.detail)}
        />
      </div>
      <div class="filter-group" id="game-window-bar">
        <span class="game-window-label">Games</span>
        <SegmentedControl
          items={windowItems}
          value={$view.game_window_default}
          variant="pill"
          size="sm"
          on:select={(event) => selectWindow(event.detail)}
        />
      </div>
      <AccountFilter
        data={payload.account_filter || {}}
        accountKey={$accountKey}
        loading={$accountLoading}
        error={$accountError}
        onChange={(key) => report.selectAccountKey(key)}
      />
      {#if showRefresh}
        <div class="filter-group filter-group--actions" id="report-refresh-bar">
          <button
            type="button"
            class="report-refresh-btn"
            id="report-refresh-btn"
            title="Fetch latest games and rebuild this champion report"
            on:click={handleRefresh}
            disabled={refreshDisabled}
          >
            <iconify-icon icon="mdi:refresh" width="16" height="16" aria-hidden="true"></iconify-icon>
            <span>Refresh</span>
          </button>
        </div>
      {/if}
    </div>

    <SegmentedControl
      id="report-category-tabs"
      ariaLabel="Report categories"
      variant="underline"
      as="tablist"
      dataAttr="data-category"
      items={categoryTabs}
      value={activeCategory}
      on:select={(event) => selectCategory(event.detail.value)}
    />
  </div>

  {#if switchingBuild}
    <ReportSkeleton category={activeCategory} />
  {:else}
    <div class="report-category-panel{activeCategory === 'summary' ? ' is-active' : ''}" id="category-summary" data-category="summary">
      <Overview data={$view} career={careerLadder} onGoToCareer={() => reportNav.scrollToSection('career')} />
      <Coaching data={$view} />
    </div>

    <div class="report-category-panel{activeCategory === 'games' ? ' is-active' : ''}" id="category-games" data-category="games">
      <GameReview data={$view} career={careerLadder} />
    </div>

    <div class="report-category-panel{activeCategory === 'career' ? ' is-active' : ''}" id="category-career" data-category="career">
      <CareerMode
        career={careerLadder}
        playerSlug={params.slug}
        buildSlug={params.buildSlug}
        busy={jobActive || refreshing}
        pendingSlot={careerPendingSlot}
        onDropped={handleCareerDropped}
      />
    </div>

    <div class="report-category-panel{activeCategory === 'performance' ? ' is-active' : ''}" id="category-performance" data-category="performance">
      <FormTracker data={$view} />
      <RankPeers
        data={$view}
        peerStageDetail={peerStageDetail}
        peerFailed={peerFailed}
        peerUnavailable={peerUnavailable}
      />
    </div>

    <div class="report-category-panel{activeCategory === 'champion' ? ' is-active' : ''}" id="category-champion" data-category="champion">
      <Matchups data={$view} />
      <ItemsRunes data={$view} />
    </div>

    <div class="report-category-panel{activeCategory === 'deepdive' ? ' is-active' : ''}" id="category-deepdive" data-category="deepdive">
      <LaneObjectivesDeaths data={$view} />
      <VisionEconomyTeamfightsPositioning data={$view} />
      <Graphs data={$view} />
    </div>

    <Chatbot
      data={payload}
      sendMessage={sendChatMessage}
      activeTab={activeCategory}
      view={$view}
      career={careerLadder}
    />
    <RecapModal career={recapCareer} {gameReviewAll} onClose={handleRecapClose} />
  {/if}
{/if}

</main>
</div>
