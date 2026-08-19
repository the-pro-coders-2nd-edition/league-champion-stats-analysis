<script>
  import { onMount, setContext, tick } from 'svelte';
  import { createReportNav, REPORT_NAV_KEY } from '../lib/reportNav.js';
  import { computeWindowScopeLabel, WINDOW_SCOPE_KEY } from '../lib/windowScope.js';
  import { resolveCareerView } from '../lib/careerView.js';
  import { get, writable } from 'svelte/store';
  import { link } from 'svelte-spa-router';
  import {
    fetchBuild,
    fetchAccountViews,
    fetchPlayerStatus,
    refreshPlayer,
    sendChatMessage,
  } from '../lib/api.js';
  import { createReportState } from '../lib/reportState.js';
  import { getCachedBuild, setCachedBuild, invalidateIfStale } from '../lib/buildCache.js';
  import { createPoller } from '../lib/poller.js';
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
  import ReportSkeleton from '../components/ReportSkeleton.svelte';
  import { bindPlotlyDetailsResize, resizePlotlySoon } from '../lib/plotlyResize.js';

  export let params = {};

  const NAV_COLLAPSE_KEY = 'report-nav-collapsed';
  const ACTIVE_JOB_STATES = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];
  const ACTIVE_PEER_STATES = ['report_ready', 'peer_running'];

  const poller = createPoller();
  let peerStageDetail = '';
  let peerFailed = false;
  let peerUnavailable = false;
  let statusBannerVisible = false;
  let statusBannerText = '';
  let refreshing = false;
  let jobActive = false;
  let careerPendingSlot = null;

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
    // This runs when a background poll detects the report was regenerated -- the cached
    // entry for this exact build must be refreshed too, or switching away and back would
    // show the stale pre-regeneration data instead of what just landed.
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

  async function pollStatus() {
    const slug = statusSlugFromEndpoint(payload?.status_endpoint);
    if (!slug || !payload?.status_endpoint) return;
    try {
      const data = await fetchPlayerStatus(slug);
      playerBuilds = data.builds || [];
      peerFailed = !!data.peer_failed;

      // This status call is cheap (metadata only) and already carries a fresh
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
        poller.reschedule(30000);
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
        poller.reschedule(3000);
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
        poller.reschedule(30000);
        return;
      }

      if (data.peer_failed) {
        peerStageDetail = '';
        poller.reschedule(30000);
        return;
      }

      if (data.peer_completed_at && !build?.peers_ready) {
        peerUnavailable = true;
        peerStageDetail = '';
      }

      const peerPending = !payload.has_peer_comparison && !peerFailed && !peerUnavailable;
      poller.reschedule(peerPending || refreshing ? 3000 : 30000);
    } catch {
      // Transient polling errors — keep trying.
    }
  }

  function startStatusPoll(intervalMs = 10000) {
    if (!payload?.status_endpoint) return;
    poller.start(pollStatus, intervalMs);
  }

  function resetStatusPollState() {
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
    // changes once that run rewrites the report. pollStatus already reloads on a
    // new generated_at; this just drops it to the fast cadence so the new block
    // appears on its own instead of waiting out the 30s idle poll. Until then the
    // dropped slot renders as a skeleton rather than a block that is already gone.
    careerPendingSlot = result?.dropped_slot ?? null;
    jobActive = true;
    statusBannerVisible = true;
    statusBannerText = result?.job?.stage_detail || 'Rebuilding your Career ladder…';
    startStatusPoll(3000);
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
      startStatusPoll(3000);
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
  let playerPageHref = null;
  let navCollapsed = false;
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
      resetStatusPollState();
      const applyPayload = (result) => {
        payload = result;
        report = createReportState(payload, {
          fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
        });
        if (payload.status_endpoint) {
          startStatusPoll();
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
    fetchPlayerStatus(params.slug)
      .then((status) => {
        playerBuilds = status.builds || [];
        playerPageHref = `/players/${params.slug}`;
      })
      .catch(() => {
        playerBuilds = [];
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
    try {
      navCollapsed = localStorage.getItem(NAV_COLLAPSE_KEY) === '1';
    } catch (err) {
      // Private mode: collapse state lives for this page only.
    }
    const unbindPlotlyResize = bindPlotlyDetailsResize();
    window.addEventListener('scroll', syncStickyOffset, { passive: true });
    window.addEventListener('resize', syncStickyOffset);
    return () => {
      unbindPlotlyResize();
      window.removeEventListener('scroll', syncStickyOffset);
      window.removeEventListener('resize', syncStickyOffset);
      stickyResizeObserver?.disconnect();
      document.documentElement.classList.remove('report-nav-collapsed');
    };
  });

  $: if (stickyHeaderEl && (statusBannerVisible !== undefined || payload)) {
    tick().then(syncStickyOffset);
  }

  // CSS for the collapsed state is scoped to html.report-nav-collapsed (matching
  // the original report.html script), not a class on any element this component owns.
  $: if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('report-nav-collapsed', navCollapsed);
  }

  function toggleNav() {
    navCollapsed = !navCollapsed;
    try {
      localStorage.setItem(NAV_COLLAPSE_KEY, navCollapsed ? '1' : '0');
    } catch (err) {
      // Private mode: collapse state lives for this page only.
    }
  }

  function winratePct(build) {
    return build.winrate != null ? Math.round(build.winrate * 100) : null;
  }

  const REPORT_CATEGORIES = [
    { value: 'summary', label: 'Summary' },
    { value: 'games', label: 'Games' },
    { value: 'career', label: 'Career' },
    { value: 'performance', label: 'Performance' },
    { value: 'champion', label: 'Champion' },
    { value: 'deepdive', label: 'Deepdive' },
  ];
  let activeCategory = 'summary';
  $: categoryTabs = REPORT_CATEGORIES.map((category) => ({
    ...category,
    ariaControls: `category-${category.value}`,
  }));
  function selectCategory(value) {
    activeCategory = value;
    tick().then(() => {
      const panel = document.getElementById(`category-${value}`);
      if (panel) resizePlotlySoon(panel);
    });
  }

  setContext(REPORT_NAV_KEY, createReportNav(selectCategory));

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
<nav class="report-nav{navCollapsed ? ' is-collapsed' : ''}" id="report-nav" aria-label="Report navigation">
  <div class="nav-header">
    <a class="app-brand app-brand--nav" href="/" use:link title="Home">
      <img src="/out/assets/brand/logo.png" alt="" class="app-logo" aria-hidden="true">
      <span class="app-brand-title">League Champion Analyser</span>
    </a>
    <button
      type="button"
      class="nav-fold-btn"
      id="nav-fold-btn"
      aria-expanded={!navCollapsed}
      aria-controls="nav-builds-panel"
      title={navCollapsed ? 'Expand champions menu' : 'Collapse champions menu'}
      on:click={toggleNav}
    >
      <iconify-icon class="nav-fold-icon nav-fold-icon--collapse" icon="mdi:chevron-left" width="18" height="18" aria-hidden="true"></iconify-icon>
      <iconify-icon class="nav-fold-icon nav-fold-icon--expand" icon="mdi:chevron-right" width="18" height="18" aria-hidden="true"></iconify-icon>
      <span class="nav-fold-label">{navCollapsed ? 'Show' : 'Hide'}</span>
    </button>
  </div>
  <div class="nav-back">
    {#if playerPageHref}
      <a href={playerPageHref} use:link>← Player page</a>
    {:else}
      <a href="/" use:link>← Home</a>
    {/if}
  </div>
  {#if playerBuilds.length}
    <div class="nav-builds" id="nav-builds-panel">
      <div class="nav-builds-label">Champions</div>
      <div class="build-grid">
        {#each playerBuilds as build (build.slug)}
          <a
            class="build-card{build.slug === params.buildSlug ? ' is-default' : ''}"
            href="/players/{params.slug}/{build.slug}"
            use:link
            title="{build.champion}{build.role_display ? ' · ' + build.role_display : ''} · {build.games} games · {winratePct(build)}%"
          >
            {#if build.champion_icon}
              <img src={build.champion_icon} alt={build.champion} class="game-icon">
            {/if}
            <div class="build-card-body">
              <strong>{build.champion}{#if build.role_display}<span class="build-card-role">{#if build.role_icon}<img src={build.role_icon} alt="" class="role-icon role-icon--sm">{/if} {build.role_display}</span>{/if}</strong>
              <div class="meta">{build.games} games · {winratePct(build)}%</div>
            </div>
          </a>
        {/each}
      </div>
    </div>
  {/if}
</nav>
<main>

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
      <Overview data={$view} career={careerLadder} onGoToCareer={() => selectCategory('career')} />
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

    <Chatbot data={payload} sendMessage={sendChatMessage} />
  {/if}
{/if}

</main>
</div>
