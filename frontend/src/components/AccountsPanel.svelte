<script>
  import Chip from './Chip.svelte';
  import {
    hasQueueRank,
    memberRankQueues,
    queueRankLabel,
    rankDivisionText,
  } from '../lib/format.js';

  export let members = [];
  export let title = 'Accounts';
  export let variant = '';

  $: countLabel = members.length === 1 ? '1 account' : `${members.length} accounts`;
  $: hasFlex = members.some((member) => hasQueueRank(member, 'flex'));

  function rankIcon(member, queue) {
    return member[`${queue}_rank_icon`];
  }

  function rankLp(member, queue) {
    return member[`${queue}_lp`];
  }
</script>

{#if members.length}
  <div class="accounts-panel{variant ? ` accounts-panel--${variant}` : ''}{hasFlex ? ' accounts-panel--dual-rank' : ''}">
    <div class="accounts-panel-head">
      <span class="accounts-panel-label">{title}</span>
      <span class="accounts-panel-count">{countLabel}</span>
    </div>
    {#each members as member (member.label)}
      <div class="accounts-panel-row">
        {#if member.profile_icon}
          <img src={member.profile_icon} alt="" class="accounts-panel-icon">
        {:else}
          <span class="accounts-panel-icon accounts-panel-icon--placeholder" aria-hidden="true"></span>
        {/if}
        <span class="accounts-panel-name">
          {member.label}
          {#if member.is_main}
            <Chip tone="good" fill={true} density="compact" label="Main" />
          {/if}
        </span>
        <div class="accounts-panel-ranks">
          {#each memberRankQueues(member) as queue (queue)}
            {@const lp = rankLp(member, queue)}
            {@const division = rankDivisionText(member, { queue, splitLp: lp != null })}
            <div class="accounts-panel-rank-line">
              {#if hasFlex}
                <span class="accounts-panel-queue">{queueRankLabel(queue)}</span>
              {/if}
              {#if rankIcon(member, queue)}
                <img src={rankIcon(member, queue)} alt="" class="accounts-panel-rank-icon">
              {/if}
              <span class="accounts-panel-rank-division">{division || 'Unranked'}</span>
              {#if lp != null}
                <span class="accounts-panel-lp-inline">{lp} LP</span>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/each}
  </div>
{/if}
