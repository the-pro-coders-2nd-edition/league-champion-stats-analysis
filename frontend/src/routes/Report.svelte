<script>
  import { onMount } from 'svelte';
  import { link } from 'svelte-spa-router';
  import { fetchBuild, fetchAccountViews, fetchPlayerStatus, sendChatMessage } from '../lib/api.js';
  import { createReportState } from '../lib/reportState.js';
  import FilterButton from '../components/FilterButton.svelte';
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
  import TabBar from '../components/TabBar.svelte';

  export let params = {};

  const NAV_COLLAPSE_KEY = 'report-nav-collapsed';

  let payload = null;
  let error = null;
  let report = null;
  let playerBuilds = [];
  let playerPageHref = null;
  let navCollapsed = false;

  // `params` is a fresh object on every route match, so a bare `$:` on its fields
  // would refire on any unrelated reactivity tick; only refetch when the actual
  // slug/build pair changes.
  let loadedKey = '';
  $: {
    const key = `${params.slug}/${params.buildSlug}`;
    if (key !== loadedKey) {
      loadedKey = key;
      fetchBuild(params.slug, params.buildSlug)
        .then((result) => {
          payload = result;
          report = createReportState(payload, {
            fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
          });
        })
        .catch((err) => { error = err; });
    }
  }

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

  onMount(() => {
    try {
      navCollapsed = localStorage.getItem(NAV_COLLAPSE_KEY) === '1';
    } catch (err) {
      // Private mode: collapse state lives for this page only.
    }
    return () => {
      document.documentElement.classList.remove('report-nav-collapsed');
    };
  });

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
    { value: 'career', label: 'Career' },
    { value: 'performance', label: 'Performance' },
    { value: 'games', label: 'Games' },
    { value: 'champion', label: 'Champion' },
    { value: 'deepdive', label: 'Deepdive' },
  ];
  let activeCategory = 'summary';
  $: categoryTabs = REPORT_CATEGORIES.map((category) => ({
    ...category,
    modifierClass: category.value,
    active: category.value === activeCategory,
    id: `tab-${category.value}`,
    ariaControls: `category-${category.value}`,
  }));
  function selectCategory(value) {
    activeCategory = value;
  }

  $: queue = report ? report.queue : null;
  $: gameWindow = report ? report.gameWindow : null;
  $: accountKey = report ? report.accountKey : null;
  $: accountLoading = report ? report.accountLoading : null;
  $: accountError = report ? report.accountError : null;
  $: activeSource = report ? report.activeSource : null;
  $: view = report ? report.view : null;

  $: queueButtons = (payload && $activeSource)
    ? (payload.queue_filter_options || []).map((option) => ({
        ...option,
        enabled: !!($activeSource.report_views[option.key] && $activeSource.report_views[option.key].total_games),
      }))
    : [];

  function selectQueue(option) {
    if (!option.enabled) return;
    report.selectQueue(option.key);
  }

  function selectWindow(option) {
    if (!option.enabled) return;
    report.selectWindow(option.key);
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
  <p class="report-loading">Loading…</p>
{:else}
  <div class="report-filter-bar" id="report-filter-bar">
    <div class="filter-group" id="queue-filter-bar">
      <span class="game-window-label">Queue</span>
      {#each queueButtons as option}
        <FilterButton
          dataAttr="data-queue"
          buttonClass="queue-filter-btn game-window-btn"
          value={option.key}
          label={option.label}
          activeClass={option.key === $view.queue_filter_default ? 'is-active' : ''}
          disabled={!option.enabled}
          on:click={() => selectQueue(option)}
        />
      {/each}
    </div>
    <div class="filter-group" id="game-window-bar">
      <span class="game-window-label">Games</span>
      {#each $view.game_window_options || [] as option}
        <FilterButton
          dataAttr="data-window"
          buttonClass="game-window-btn"
          value={option.key}
          label={option.label}
          activeClass={option.key === $view.game_window_default ? 'is-active' : ''}
          disabled={!option.enabled}
          on:click={() => selectWindow(option)}
        />
      {/each}
    </div>
  </div>

  <AccountFilter
    data={payload.account_filter || {}}
    accountKey={$accountKey}
    loading={$accountLoading}
    error={$accountError}
    onChange={(key) => report.selectAccountKey(key)}
  />

  <TabBar
    containerId="report-category-tabs"
    ariaLabel="Report categories"
    buttonClass="report-tab"
    dataAttr="data-category"
    tabs={categoryTabs}
    on:select={(event) => selectCategory(event.detail)}
  />

  <div class="report-category-panel{activeCategory === 'summary' ? ' is-active' : ''}" id="category-summary" data-category="summary">
    <Overview data={$view} onGoToCareer={() => selectCategory('career')} />
    <Coaching data={$view} />
  </div>

  <div class="report-category-panel{activeCategory === 'career' ? ' is-active' : ''}" id="category-career" data-category="career">
    <CareerMode data={$view} />
  </div>

  <div class="report-category-panel{activeCategory === 'performance' ? ' is-active' : ''}" id="category-performance" data-category="performance">
    <FormTracker data={$view} />
    <RankPeers data={$view} />
  </div>

  <div class="report-category-panel{activeCategory === 'games' ? ' is-active' : ''}" id="category-games" data-category="games">
    <GameReview data={$view} />
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

</main>
</div>
