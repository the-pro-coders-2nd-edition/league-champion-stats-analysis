<script>
  import { link } from 'svelte-spa-router';
  import Chip from './Chip.svelte';
  import StatChip from './StatChip.svelte';
  import { winratePct, formatUpdated } from '../lib/format.js';

  export let build = {};
  export let href = '';
  export let density = 'nav';
  export let active = false;
  export let peersPending = false;

  $: pct = winratePct(build);
  $: wrTone = pct == null ? 'flat' : (pct >= 50 ? 'good' : 'bad');
  $: scoreValue = build.score != null ? Math.round(Number(build.score)) : null;
  $: title = [
    build.champion,
    build.role_display || build.role,
    build.games != null ? `${build.games} games` : '',
    pct != null ? `${pct}%` : '',
    scoreValue != null ? `Score ${scoreValue}` : '',
  ].filter(Boolean).join(' · ');
</script>

<a
  class="build-card{density === 'page' ? ' build-card--page' : ''}{active ? ' is-default' : ''}"
  {href}
  use:link
  {title}
>
  {#if density === 'page'}
    {#if build.champion_icon}
      <img src={build.champion_icon} alt={build.champion || ''} class="game-icon">
    {/if}
    <div class="build-card-body">
      <div class="build-heading build-card-heading">
        <span class="build-card-champion">{build.champion || build.build_label || 'Report'}</span>
        {#if build.role_icon}
          <img src={build.role_icon} alt="" title={build.role_display || build.role} class="role-icon role-icon--sm">
        {/if}
        {#if build.role_display || build.role}
          <span class="build-card-role-label">{build.role_display || build.role}</span>
        {/if}
      </div>
      <div class="build-card-stats">
        <StatChip tone="flat" label="Games" value={build.games || 0} />
        {#if pct != null}
          <StatChip tone={wrTone} label="WR" value="{pct}%" />
        {/if}
      </div>
      {#if formatUpdated(build.generated_at)}
        <div class="meta">Updated {formatUpdated(build.generated_at)}</div>
      {/if}
      {#if peersPending && !build.peers_ready}
        <span class="build-card-badge-slot">
          <Chip tone="info" label="Rank comparison loading" caps={true} density="compact" />
        </span>
      {/if}
    </div>
    {#if scoreValue != null}
      <div class="build-card-score">
        <span class="build-card-score-value" style={build.score_color ? `color: ${build.score_color}` : null}>{scoreValue}</span>
        {#if build.score_verdict_label}
          <span class="build-card-score-verdict">{build.score_verdict_label}</span>
        {/if}
      </div>
    {/if}
  {:else}
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
        {build.games || 0} games{#if pct != null} · {pct}%{/if}
        {#if scoreValue != null} · {scoreValue}{/if}
      </div>
    </div>
  {/if}
</a>
