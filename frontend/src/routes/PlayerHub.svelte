<script>
  import { onMount } from 'svelte';
  import {
    fetchPlayerStatus,
    refreshPlayer,
    regeneratePlayer,
    cancelJob,
    setPlayerWatch,
  } from '../lib/api.js';
  import { createPoller } from '../lib/poller.js';
  import Panel from '../components/Panel.svelte';
  import Chip from '../components/Chip.svelte';
  import AppNav from '../components/AppNav.svelte';
  import BuildCard from '../components/BuildCard.svelte';
  import AccountsPanel from '../components/AccountsPanel.svelte';
  import PlayerHeaderSkeleton from '../components/PlayerHeaderSkeleton.svelte';
  import ChampionsSectionSkeleton from '../components/ChampionsSectionSkeleton.svelte';
  import PlayerControlsSkeleton from '../components/PlayerControlsSkeleton.svelte';
  import JobProgress from '../components/JobProgress.svelte';
  import WatchToggle from '../components/WatchToggle.svelte';
  import SegmentedControl from '../components/SegmentedControl.svelte';
  import { winratePct } from '../lib/format.js';

  export let params = {};

  const ACTIVE_STATES = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];
  const STAGE_ORDER = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];
  const SORT_ITEMS = [
    { value: 'recent', label: 'Recent' },
    { value: 'score', label: 'Best score' },
    { value: 'games', label: 'Most games' },
    { value: 'wr', label: 'Best WR' },
  ];

  let status = null;
  let subtitle = 'Loading…';
  const poller = createPoller();
  let cancelling = false;
  let retrying = false;
  let busy = false;
  let actionHint = '';
  let watching = false;
  let watchBusy = false;
  let watchHint = '';
  let watchPending = false;
  let sortKey = 'recent';
  let jobErrorText = '';

  function formatEta(seconds) {
    if (seconds == null) return '';
    const minutes = Math.max(1, Math.round(seconds / 60));
    return `~${minutes} min estimated wait`;
  }

  async function poll() {
    try {
      const data = await fetchPlayerStatus(params.slug);
      status = data;
      if (!watchPending) watching = !!data.watch_enabled;
      const job = data.active_job;
      const active = !!(job && ACTIVE_STATES.includes(job.state));
      const builds = data.builds || [];
      const peersPending = active
        && (job.state === 'report_ready' || job.state === 'peer_running')
        && builds.length > 0;
      const anyPeersPending = peersPending && builds.some((build) => !build.peers_ready);
      if (data.has_report) {
        subtitle = active
          ? (anyPeersPending
            ? 'Reports are ready — rank comparison is still loading.'
            : 'Reports below update as the analysis progresses.')
          : data.peer_failed
            ? 'Reports ready (rank comparison failed — refresh to retry).'
            : 'Pick a champion and lane.';
      } else {
        subtitle = active
          ? 'Your report is being prepared — this page updates automatically.'
          : (job && job.state === 'failed')
            ? 'Analysis failed.'
            : 'No reports yet.';
      }
      if (!active) {
        poller.reschedule(30000);
      }
    } catch {
      subtitle = 'Could not reach the server — retrying…';
    }
  }

  function restartFastPoll() {
    return poller.start(poll, 3000);
  }

  onMount(() => {
    poller.start(poll, 3000);
  });

  async function handleRefresh() {
    busy = true;
    actionHint = '';
    try {
      await refreshPlayer(params.slug);
      await restartFastPoll();
    } catch (err) {
      actionHint = err.message || 'Refresh failed.';
    } finally {
      busy = false;
    }
  }

  async function handleWatchToggle() {
    if (watchBusy) return;
    const next = !watching;
    watchBusy = true;
    watchPending = true;
    watchHint = '';
    try {
      const result = await setPlayerWatch(params.slug, next);
      watching = !!result.watch_enabled;
    } catch (err) {
      watchHint = err.message || 'Could not update watch.';
    } finally {
      watchBusy = false;
      watchPending = false;
    }
  }

  function watchNoteLabel(interval) {
    if (!interval) return 'Checking for new games';
    const minutes = Math.round(interval / 60);
    return minutes <= 1
      ? 'Checking every minute for new games'
      : `Checking every ${minutes} min for new games`;
  }

  async function handleRegenerate() {
    busy = true;
    actionHint = '';
    try {
      await regeneratePlayer(params.slug);
      await restartFastPoll();
    } catch (err) {
      actionHint = err.message || 'Regenerate failed.';
    } finally {
      busy = false;
    }
  }

  async function handleRetry() {
    retrying = true;
    try {
      await refreshPlayer(params.slug);
      await restartFastPoll();
    } catch (err) {
      jobErrorText = err.message || 'Retry failed.';
    } finally {
      retrying = false;
    }
  }

  async function handleCancel() {
    if (!activeJobId || cancelling) return;
    cancelling = true;
    try {
      await cancelJob(activeJobId);
      await restartFastPoll();
    } catch (err) {
      jobErrorText = err.message || 'Cancel failed.';
    } finally {
      cancelling = false;
    }
  }

  $: job = status ? status.active_job : null;
  $: activeJobId = job ? job.id : null;
  $: active = !!(job && ACTIVE_STATES.includes(job.state));
  $: builds = status ? status.builds || [] : [];
  $: peersPending = !!(active && job
    && (job.state === 'report_ready' || job.state === 'peer_running') && builds.length > 0);
  $: playerLabel = status ? status.player_label : params.slug;
  $: members = status ? status.players || [] : [];
  $: showJobCard = !!(job && (active || job.state === 'failed' || job.state === 'cancelled' || job.error));
  $: showPageSkeleton = !status;
  $: showSkeleton = active && job && job.state !== 'queued' && job.state !== 'failed' && builds.length === 0;
  $: showBuildsSkeleton = showPageSkeleton || showSkeleton;
  $: navLoading = showBuildsSkeleton && builds.length === 0;
  $: showBuilds = builds.length > 0 && !showSkeleton;
  $: showRetry = !!(job && (job.state === 'failed' || job.state === 'cancelled'));
  $: showActions = !!(status && status.has_report);
  $: canWatch = !!(status && status.can_watch);
  $: watchInterval = status ? status.watch_interval_s : 0;
  $: watchError = status ? status.last_watch_error || '' : '';
  $: jobDetailText = job
    ? (job.state === 'queued' && job.queue_position != null
      ? (job.queue_position === 0 ? 'Next in line.' : `${job.queue_position} job(s) ahead of you.`)
      : (job.stage_detail || ''))
    : '';
  $: jobEtaText = job && job.state === 'queued' && job.queue_position != null ? formatEta(job.eta_s) : '';
  $: stageState = job ? (active ? job.state : (job.state === 'done' ? 'peer_running' : '')) : '';
  $: stageIdx = STAGE_ORDER.indexOf(stageState);
  $: {
    if (job && job.error) jobErrorText = job.error;
    else if (!showJobCard) jobErrorText = '';
  }
  $: statusChip = !status
    ? { tone: 'flat', label: 'Loading' }
    : (job && job.state === 'failed')
      ? { tone: 'bad', label: 'Failed' }
      : (job && job.state === 'cancelled')
        ? { tone: 'warn', label: 'Cancelled' }
        : peersPending
          ? { tone: 'info', label: 'Rank comparison' }
          : active
            ? { tone: 'info', label: 'Updating' }
            : { tone: 'good', label: 'Ready' };

  $: sortedBuilds = [...builds].sort((a, b) => {
    if (sortKey === 'wr') return (winratePct(b) || 0) - (winratePct(a) || 0);
    if (sortKey === 'score') {
      const aScore = a.score == null ? -Infinity : Number(a.score);
      const bScore = b.score == null ? -Infinity : Number(b.score);
      return bScore - aScore;
    }
    if (sortKey === 'recent') {
      return String(b.last_game_at || b.generated_at || '').localeCompare(String(a.last_game_at || a.generated_at || ''));
    }
    return (b.games || 0) - (a.games || 0);
  });

  function stageClass(i) {
    if (!job) return '';
    if (job.state === 'done') return ' is-done';
    if (stageIdx < 0) return '';
    if (i < stageIdx) return ' is-done';
    if (i === stageIdx) return ' is-active';
    return '';
  }
</script>

<div class="layout">
<AppNav
  builds={builds}
  playerSlug={params.slug}
  backHref="/"
  backLabel="← Reports"
  loading={navLoading}
/>
<main class="library-main">

<header class="page-header player-workspace-header">
  {#if showPageSkeleton}
    <PlayerHeaderSkeleton />
  {:else if members.length}
    <AccountsPanel {members} title={playerLabel} variant="hero" />
  {:else}
    <h1 class="page-title" id="player-title">{playerLabel}</h1>
  {/if}
</header>

{#if showJobCard}
  <Panel class="panel-job" id="job-card">
    <JobProgress
      {job}
      {active}
      {showRetry}
      {retrying}
      {cancelling}
      errorText={jobErrorText}
      detailText={jobDetailText}
      etaText={jobEtaText}
      {stageClass}
      on:cancel={handleCancel}
      on:retry={handleRetry}
    />
  </Panel>
{/if}

{#if showBuildsSkeleton}
  <ChampionsSectionSkeleton showSort={showPageSkeleton} count={showPageSkeleton ? 6 : 3} />
{:else if showBuilds}
  <div id="builds-section">
    <div class="section-header">
      <h2 class="section-label">Champions</h2>
      <SegmentedControl
        items={SORT_ITEMS}
        value={sortKey}
        variant="pill"
        size="sm"
        ariaLabel="Sort champions"
        on:select={(event) => { sortKey = event.detail.value; }}
      />
    </div>
    <div class="build-grid build-grid--page" id="builds-grid">
      {#each sortedBuilds as build (build.slug)}
        <BuildCard
          {build}
          href="/players/{params.slug}/{build.slug}"
          density="page"
          {peersPending}
        />
      {/each}
    </div>
  </div>
{/if}

{#if showPageSkeleton}
  <PlayerControlsSkeleton />
{:else}
<section class="player-workspace-controls" aria-label="Player actions">
  <div class="player-header-meta">
    <Chip tone={statusChip.tone} label={statusChip.label} caps={true} density="compact" />
    <p class="page-sub" id="player-subtitle">{subtitle}</p>
  </div>
  {#if showActions || canWatch}
    <div class="player-header-actions">
      {#if showActions && !active}
        <button
          type="button"
          class="report-refresh-btn"
          id="refresh-btn"
          title="Fetch latest games and rebuild all champion reports"
          on:click={handleRefresh}
          disabled={busy}
        >
          <iconify-icon icon="mdi:refresh" width="16" height="16" aria-hidden="true"></iconify-icon>
          <span>{busy ? 'Refreshing…' : 'Refresh with latest games'}</span>
        </button>
        <button
          type="button"
          class="report-refresh-btn"
          id="regenerate-btn"
          title="Rebuild all champion reports from the games already downloaded"
          on:click={handleRegenerate}
          disabled={busy}
        >
          <iconify-icon icon="mdi:sync" width="16" height="16" aria-hidden="true"></iconify-icon>
          <span>{busy ? 'Regenerating…' : 'Regenerate with same games'}</span>
        </button>
      {/if}
      {#if canWatch}
        <WatchToggle
          {watching}
          disabled={watchBusy}
          intervalS={watchInterval}
          on:click={handleWatchToggle}
        />
      {/if}
      <span class="muted" id="refresh-hint">{actionHint}</span>
    </div>
    {#if canWatch && (watching || watchHint || watchError)}
      <div class="watch-note" id="watch-note">
        {#if watchHint}
          <span class="error-text">{watchHint}</span>
        {:else if watchError}
          <span class="error-text">Watch paused after an error: {watchError}</span>
        {:else}
          <span class="muted">{watchNoteLabel(watchInterval)} — the report refreshes on its own.</span>
        {/if}
      </div>
    {/if}
  {/if}
</section>
{/if}

</main>
</div>
