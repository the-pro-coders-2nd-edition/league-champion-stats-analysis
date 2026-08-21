<script>
  import { createEventDispatcher } from 'svelte';
  import Button from './Button.svelte';

  export let job;
  export let active = false;
  export let showRetry = false;
  export let retrying = false;
  export let cancelling = false;
  export let errorText = '';
  export let detailText = '';
  export let etaText = '';
  export let stageClass;

  const dispatch = createEventDispatcher();

  const STATE_LABELS = {
    queued: 'Queued',
    fetching: 'Downloading matches',
    analyzing: 'Analyzing',
    report_ready: 'Reports ready',
    peer_running: 'Comparing you to players at your rank',
    done: 'Complete',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };

  const PEER_STATES = ['report_ready', 'peer_running'];

  $: phaseLabel = !job
    ? ''
    : PEER_STATES.includes(job.state)
      ? 'Comparing to your rank'
      : (job.state === 'failed' || job.state === 'cancelled')
        ? (STATE_LABELS[job.state] || job.state)
        : 'Preparing reports';
</script>

<div class="status-line">
  <span class="dot{active ? ' pulse' : (job.state === 'failed' || job.state === 'cancelled') ? ' err' : ' ok'}" id="job-dot"></span>
  <strong id="job-state">{phaseLabel}</strong>
  <span class="muted" id="job-eta">{etaText}</span>
  {#if active}
    <Button variant="bare" id="cancel-btn" on:click={() => dispatch('cancel')} disabled={cancelling}>
      {cancelling ? 'Cancelling…' : 'Cancel run'}
    </Button>
  {/if}
</div>
<div class="muted job-detail" id="job-detail">{detailText}</div>
{#if job.stage_current != null && job.stage_total}
  <div class="progress-track" id="job-progress">
    <div class="progress-fill" id="job-progress-fill" style="width: {Math.min(100, 100 * job.stage_current / job.stage_total)}%"></div>
  </div>
{/if}
<ol class="job-stages" id="job-stages" aria-label="Analysis progress">
  <li class="job-stage{stageClass(0)}" data-stage="queued">Queued</li>
  <li class="job-stage{stageClass(1)}" data-stage="fetching">Download matches</li>
  <li class="job-stage{stageClass(2)}" data-stage="analyzing">Analyze reports</li>
  <li class="job-stage{stageClass(3)}" data-stage="report_ready">Report ready</li>
  <li class="job-stage{stageClass(4)}" data-stage="peer_running">Rank comparison</li>
</ol>
<div class="error" id="job-error">{errorText}</div>
{#if showRetry}
  <div class="actions-row" id="retry-row">
    <Button id="retry-btn" on:click={() => dispatch('retry')} disabled={retrying}>{retrying ? 'Retrying…' : 'Retry analysis'}</Button>
  </div>
{/if}
