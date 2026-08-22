<script>
  import { link } from 'svelte-spa-router';
  import Chip from './Chip.svelte';
  import { formatUpdated, hasQueueRank, rankDivisionText } from '../lib/format.js';

  export let group;
  export let stageLabel = (state) => state || 'Analysis in progress';

  const PREVIEW_LIMIT = 6;

  $: members = group.players && group.players.length
    ? group.players
    : [{ label: group.player || group.slug, profile_icon: null }];
  $: previews = (group.preview_builds || []).slice(0, PREVIEW_LIMIT);
  $: remainingBuilds = Math.max(0, (group.build_count || 0) - previews.length);
  $: showChips = group.is_group || group.watch_enabled;
  $: primaryMember = members[0];
  $: soloDivision = primaryMember && hasQueueRank(primaryMember, 'solo')
    ? rankDivisionText(primaryMember, { queue: 'solo' })
    : '';
</script>

<a
  class="player-card{group.busy ? ' is-busy' : ''}"
  href="/players/{group.slug}"
  use:link
  data-slug={group.slug}
  data-has-report={group.has_report ? '1' : '0'}
  data-job-state={group.job_state || ''}
  title={group.busy ? stageLabel(group.job_state) : undefined}
>
  <div class="player-card-head">
    <span class="player-card-status" aria-hidden="true"></span>
    <div class="player-card-identity">
      <span class="player-card-icon-stack">
        {#each members.slice(0, 3) as member, index (member.label || index)}
          {#if member.profile_icon}
            <img class="player-card-icon" src={member.profile_icon} alt="" width="24" height="24" style="z-index: {3 - index}">
          {/if}
        {/each}
      </span>
      <span class="player-card-label">{group.player || group.slug}</span>
    </div>
  </div>

  {#if soloDivision || showChips}
    <div class="player-card-sub">
      {#if soloDivision}
        <div class="player-card-ranks">
          <span class="player-card-rank">
            {#if primaryMember.solo_rank_icon}
              <img class="player-card-rank-icon" src={primaryMember.solo_rank_icon} alt="" width="20" height="20">
            {/if}
            <span class="player-card-rank-label">{soloDivision}</span>
          </span>
        </div>
      {/if}
      {#if showChips}
        <span class="player-card-chips">
          {#if group.is_group}<Chip tone="plan" label="Group" caps={true} density="compact" />{/if}
          {#if group.watch_enabled}<Chip tone="info" label="Watching" caps={true} density="compact" />{/if}
        </span>
      {/if}
    </div>
  {/if}

  {#if previews.length || remainingBuilds > 0}
    <div class="player-card-previews" aria-hidden="true">
      {#each previews as build (build.slug)}
        <span class="player-card-preview" title="{build.champion} · {build.games || 0} games">
          {#if build.champion_icon}
            <img src={build.champion_icon} alt="" width="28" height="28">
          {/if}
        </span>
      {/each}
      {#if remainingBuilds > 0}
        <span class="player-card-preview player-card-preview--more" title="{remainingBuilds} more champion{remainingBuilds === 1 ? '' : 's'}">+{remainingBuilds}</span>
      {/if}
    </div>
  {/if}

  {#if group.has_report && formatUpdated(group.last_updated)}
    <div class="player-card-updated">{formatUpdated(group.last_updated)}</div>
  {/if}

  {#if group.busy}
    <div class="player-card-stage">{stageLabel(group.job_state)}</div>
  {/if}
</a>
