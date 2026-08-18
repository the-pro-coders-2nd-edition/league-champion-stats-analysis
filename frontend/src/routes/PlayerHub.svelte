<script>
  import { onMount } from 'svelte';
  import { link } from 'svelte-spa-router';
  import {
    fetchPlayerStatus,
    refreshPlayer,
    regeneratePlayer,
    cancelJob,
    setPlayerWatch,
  } from '../lib/api.js';
  import { createPoller } from '../lib/poller.js';
  import Button from '../components/Button.svelte';
  import Panel from '../components/Panel.svelte';
  import Chip from '../components/Chip.svelte';

  export let params = {};

  const STATE_LABELS = {
    queued: 'Queued',
    fetching: 'Downloading matches',
    analyzing: 'Analyzing',
    report_ready: 'Report ready — comparing you to players at your rank',
    peer_running: 'Comparing you to players at your rank',
    done: 'Complete',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };
  const ACTIVE_STATES = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];
  const STAGE_ORDER = ['queued', 'fetching', 'analyzing', 'report_ready', 'peer_running'];

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
  // The poller overwrites `watching` from the server on every tick, which would
  // snap the toggle back while a click is still in flight.
  let watchPending = false;

  function formatEta(seconds) {
    if (seconds == null) return '';
    const minutes = Math.max(1, Math.round(seconds / 60));
    return `~${minutes} min estimated wait`;
  }

  function formatUpdated(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
    try {
      return date.toLocaleString('en-US', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch {
      return date.toISOString().slice(0, 16).replace('T', ' ');
    }
  }

  function winratePct(build) {
    return build.winrate != null ? Math.round(build.winrate * 100) : null;
  }

  function winrateClass(build) {
    const pct = winratePct(build);
    return pct == null ? '' : (pct >= 50 ? 'win' : 'loss');
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
            ? 'Reports are ready — the comparison to players at your rank is still loading.'
            : 'Reports below update as the analysis progresses.')
          : data.peer_failed
            ? 'Reports ready (rank comparison failed — refresh to retry).'
            : 'Pick a champion + lane report below.';
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

  function watchLabel(interval) {
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

  let jobErrorText = '';

  $: job = status ? status.active_job : null;
  $: activeJobId = job ? job.id : null;
  $: active = !!(job && ACTIVE_STATES.includes(job.state));
  $: builds = status ? status.builds || [] : [];
  $: peersPending = !!(active && job
    && (job.state === 'report_ready' || job.state === 'peer_running') && builds.length > 0);
  $: playerLabel = status ? status.player_label : params.slug;
  $: members = status ? status.players || [] : [];
  $: showJobCard = !!(job && (active || job.state === 'failed' || job.state === 'cancelled' || job.error));
  $: showSkeleton = active && job && job.state !== 'queued' && job.state !== 'failed' && builds.length === 0;
  $: showBuilds = builds.length > 0 && !showSkeleton;
  $: showRetry = !!(job && (job.state === 'failed' || job.state === 'cancelled'));
  // The watch toggle only needs a report to exist -- it shouldn't wait for the whole
  // pipeline (through peer comparison) to finish just to let someone opt into
  // auto-refresh. Refresh/regenerate stay gated on !active below: no point letting
  // someone kick off another run while one is already in flight.
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

  function stageClass(i) {
    if (!job) return '';
    if (job.state === 'done') return ' is-done';
    if (stageIdx < 0) return '';
    if (i < stageIdx) return ' is-done';
    if (i === stageIdx) return ' is-active';
    return '';
  }
</script>

<div class="shell">
<a class="app-brand app-brand--page" href="/" use:link title="Home">
  <img src="/out/assets/brand/logo.png" alt="" class="app-logo" aria-hidden="true">
  <span class="app-brand-title">League Champion Analyser</span>
</a>
<nav class="breadcrumb" aria-label="Breadcrumb">
  <a href="/" use:link>Home</a>
  <span class="breadcrumb-sep" aria-hidden="true">/</span>
  <span class="breadcrumb-current" id="breadcrumb-current">{playerLabel}</span>
</nav>
<header class="page-header">
  <h1 class="page-title" id="player-title" hidden={members.length > 0}>{playerLabel}</h1>
  <div class="player-members" id="player-members" hidden={members.length === 0}>
    {#each members as member (member.label)}
      <div class="player-member">
        {#if member.profile_icon}
          <img class="player-member-icon" src={member.profile_icon} alt="" width="32" height="32">
        {/if}
        <span class="player-member-label">{member.label}</span>
        {#if member.solo_rank_label}
          <span class="player-member-rank">
            {#if member.solo_rank_icon}
              <img class="player-member-rank-icon" src={member.solo_rank_icon} alt="" width="40" height="40">
            {/if}
            <span class="player-member-rank-label">{member.solo_rank_label}</span>
          </span>
        {/if}
      </div>
    {/each}
  </div>
  <p class="page-sub" id="player-subtitle">{subtitle}</p>
</header>

{#if showJobCard}
  <Panel class="panel-job" id="job-card">
    <div class="status-line">
      <span class="dot{active ? ' pulse' : (job.state === 'failed' || job.state === 'cancelled') ? ' err' : ' ok'}" id="job-dot"></span>
      <strong id="job-state">{STATE_LABELS[job.state] || job.state}</strong>
      <span class="muted" id="job-eta">{jobEtaText}</span>
      {#if active}
        <Button variant="bare" id="cancel-btn" on:click={handleCancel} disabled={cancelling}>
          {cancelling ? 'Cancelling…' : 'Cancel run'}
        </Button>
      {/if}
    </div>
    <div class="muted job-detail" id="job-detail">{jobDetailText}</div>
    <ol class="job-stages" id="job-stages" aria-label="Analysis progress">
      <li class="job-stage{stageClass(0)}" data-stage="queued">Queued</li>
      <li class="job-stage{stageClass(1)}" data-stage="fetching">Download matches</li>
      <li class="job-stage{stageClass(2)}" data-stage="analyzing">Analyze reports</li>
      <li class="job-stage{stageClass(3)}" data-stage="report_ready">Report ready</li>
      <li class="job-stage{stageClass(4)}" data-stage="peer_running">Rank comparison</li>
    </ol>
    {#if job.stage_current != null && job.stage_total}
      <div class="progress-track" id="job-progress">
        <div class="progress-fill" id="job-progress-fill" style="width: {Math.min(100, 100 * job.stage_current / job.stage_total)}%"></div>
      </div>
    {/if}
    <div class="error" id="job-error">{jobErrorText}</div>
    {#if showRetry}
      <div class="actions-row" id="retry-row">
        <Button id="retry-btn" on:click={handleRetry} disabled={retrying}>Retry analysis</Button>
      </div>
    {/if}
  </Panel>
{/if}

{#if showSkeleton}
  <div id="builds-section">
    <h2 class="section-label">Reports</h2>
    <div class="build-grid" id="builds-grid">
      <div class="build-card build-card--skeleton" aria-hidden="true"></div>
      <div class="build-card build-card--skeleton" aria-hidden="true"></div>
      <div class="build-card build-card--skeleton" aria-hidden="true"></div>
    </div>
  </div>
{:else if showBuilds}
  <div id="builds-section">
    <h2 class="section-label">Reports</h2>
    <div class="build-grid" id="builds-grid">
      {#each builds as build (build.slug)}
        <a class="build-card" href="/players/{params.slug}/{build.slug}" use:link>
          {#if build.champion_icon}
            <img src={build.champion_icon} alt="" class="game-icon">
          {/if}
          <div class="build-card-body">
            <strong>
              {build.champion || build.build_label || 'Report'}
              {#if build.role_display || build.role}
                <span class="build-card-role">
                  {#if build.role_icon}
                    <img src={build.role_icon} alt="" title={build.role_display || build.role} class="role-icon role-icon--sm">
                  {/if}
                  {build.role_display || build.role}
                </span>
              {/if}
            </strong>
            <div class="meta">
              {build.games || 0} games
              {#if build.winrate != null}
                · <span class={winrateClass(build)}>{winratePct(build)}% WR</span>
              {/if}
            </div>
            {#if formatUpdated(build.generated_at)}
              <div class="build-card-updated">Updated {formatUpdated(build.generated_at)}</div>
            {/if}
            {#if peersPending && !build.peers_ready}
              <span class="build-card-badge-slot"><Chip tone="info" label="Ready — rank comparison loading" caps={true} density="compact" /></span>
            {/if}
          </div>
        </a>
      {/each}
    </div>
  </div>
{/if}

{#if showActions}
  <Panel id="actions-card">
    <div class="actions-row">
      {#if !active}
        <Button id="refresh-btn" on:click={handleRefresh} disabled={busy}>Refresh with latest games</Button>
        <Button variant="bare" id="regenerate-btn" on:click={handleRegenerate} disabled={busy}>Regenerate with same games</Button>
      {/if}
      {#if canWatch}
        <button
          class="watch-toggle{watching ? ' is-on' : ''}"
          id="watch-btn"
          type="button"
          role="switch"
          aria-checked={watching}
          on:click={handleWatchToggle}
          disabled={watchBusy}
          title={watching ? watchLabel(watchInterval) : 'Refresh automatically after each new game'}
        >
          <span class="watch-toggle-track switch-track" aria-hidden="true"><span class="watch-toggle-knob switch-track-knob"></span></span>
          <span class="watch-toggle-label">{watching ? 'Watching this player' : 'Watch this player'}</span>
        </button>
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
          <span class="muted">{watchLabel(watchInterval)} — the report refreshes on its own.</span>
        {/if}
      </div>
    {/if}
  </Panel>
{/if}
</div>
