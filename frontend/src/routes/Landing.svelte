<script>
  import { onMount } from 'svelte';
  import { link, push } from 'svelte-spa-router';
  import { submitAnalysis, fetchActivity, fetchGroups } from '../lib/api.js';
  import { createPoller } from '../lib/poller.js';
  import Button from '../components/Button.svelte';

  const MAX_PLAYERS = 8;
  const REGION_CHOICES = [
    ['EUW', 'euw1'],
    ['EUNE', 'eun1'],
    ['NA', 'na1'],
    ['KR', 'kr'],
    ['BR', 'br1'],
    ['LAN', 'la1'],
    ['LAS', 'la2'],
    ['OCE', 'oc1'],
    ['TR', 'tr1'],
    ['RU', 'ru'],
    ['JP', 'jp1'],
  ];
  const MIN_GAMES_CHOICES = [5, 10, 15, 20, 25, 30, 50];
  const DEFAULT_MIN_GAMES = 20;
  const STATE_TITLES = {
    queued: 'Queued',
    fetching: 'Downloading matches',
    analyzing: 'Analyzing',
    report_ready: 'Report ready — rank comparison loading',
    peer_running: 'Comparing to players at your rank',
  };

  function stageLabel(state) {
    return STATE_TITLES[state] || 'Analysis in progress';
  }

  let playerInputs = [''];
  let region = 'euw1';
  let minGames = DEFAULT_MIN_GAMES;
  let submitting = false;
  let error = '';

  let groups = [];
  let groupsLoaded = false;
  let searchQuery = '';
  const poller = createPoller();

  function addPlayerRow() {
    if (playerInputs.length >= MAX_PLAYERS) return;
    playerInputs = [...playerInputs, ''];
  }

  function removePlayerRow(index) {
    if (playerInputs.length <= 1) return;
    playerInputs = playerInputs.filter((_, i) => i !== index);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    error = '';
    const players = playerInputs.map((value) => value.trim()).filter(Boolean);
    if (!players.length) {
      error = 'Provide at least one Riot ID as Name#Tag.';
      return;
    }
    submitting = true;
    try {
      const data = await submitAnalysis({ players, region, minGames: Number(minGames) });
      push(`/players/${data.player_slug}`);
    } catch (err) {
      error = err.message || 'Something went wrong.';
      submitting = false;
    }
  }

  function memberSearchText(group) {
    const bits = (group.players || []).map((member) => member.label || '');
    bits.push(group.player || '', group.slug || '');
    return bits.join(' ').toLowerCase();
  }

  function clearSearch() {
    searchQuery = '';
  }

  async function loadGroups() {
    try {
      const data = await fetchGroups();
      groups = data.groups || [];
    } catch {
      // Keep whatever was previously loaded on a transient error.
    } finally {
      groupsLoaded = true;
    }
  }

  function applyActivity(items) {
    const busyBySlug = new Map(items.map((item) => [item.slug, item]));
    let needsReload = false;

    groups = groups.map((group) => {
      const item = busyBySlug.get(group.slug);
      if (item) {
        busyBySlug.delete(group.slug);
        return { ...group, busy: true, job_state: item.state };
      }
      if (group.busy) {
        if (!group.has_report) needsReload = true;
        return { ...group, busy: false, job_state: null };
      }
      return group;
    });

    const pending = [];
    busyBySlug.forEach((item, slug) => {
      pending.push({
        slug,
        player: item.player_label,
        players: item.players,
        is_group: (item.players || []).length > 1,
        build_count: 0,
        total_games: 0,
        busy: true,
        job_state: item.state,
        has_report: item.has_report,
      });
    });
    if (pending.length) {
      groups = [...pending, ...groups];
    }

    if (needsReload) loadGroups();
  }

  async function pollActivity() {
    try {
      const data = await fetchActivity();
      applyActivity(data.items || []);
    } catch {
      // Ignore transient polling errors.
    }
  }

  onMount(() => {
    loadGroups().then(() => {
      poller.start(pollActivity, 3000);
    });
  });

  $: normalizedQuery = searchQuery.trim().toLowerCase();
  $: filteredGroups = normalizedQuery
    ? groups.filter((group) => memberSearchText(group).includes(normalizedQuery))
    : groups;
  $: hasNoMatch = groupsLoaded && groups.length > 0 && normalizedQuery !== '' && filteredGroups.length === 0;
</script>

<div class="shell">
<a class="app-brand app-brand--page" href="/" use:link title="Home">
  <img src="/out/assets/brand/logo.png" alt="" class="app-logo" aria-hidden="true">
  <span class="app-brand-title">League Champion Analyser</span>
</a>
<p class="home-lead">Coaching reports from your ranked games.</p>

<div class="panel panel--form panel--elevated">
  <form id="analyze-form" on:submit={handleSubmit}>
    <div id="player-rows">
      {#each playerInputs as value, index (index)}
        <div class="form-row player-row">
          <input
            name="riot_id"
            class="riot-input"
            placeholder="Riot ID (e.g. Faker#KR1)"
            autocomplete="off"
            bind:value={playerInputs[index]}
          >
          <Button
            variant="bare"
            size="sm"
            class="remove-player"
            hidden={playerInputs.length === 1}
            ariaLabel="Remove player"
            on:click={() => removePlayerRow(index)}
          >Remove</Button>
        </div>
      {/each}
    </div>
    <div class="form-actions">
      <Button variant="bare" size="sm" id="add-player" hidden={playerInputs.length >= MAX_PLAYERS} on:click={addPlayerRow}>
        + Add another account
      </Button>
      <div class="form-row-end">
        <select name="region" aria-label="Region" bind:value={region}>
          {#each REGION_CHOICES as [label, value] (value)}
            <option value={value}>{label}</option>
          {/each}
        </select>
        <select name="min_games" aria-label="Minimum games for a report" bind:value={minGames}>
          {#each MIN_GAMES_CHOICES as value (value)}
            <option value={value}>{value} games</option>
          {/each}
        </select>
        <Button type="submit" id="analyze-submit" disabled={submitting}>
          {submitting ? 'Checking…' : 'Analyze'}
        </Button>
      </div>
    </div>
    <p class="hint">Same region for every account. The games menu sets the minimum ranked games a champion and lane need before a report is created.</p>
    <div class="error" id="analyze-error">{error}</div>
  </form>
</div>

<div class="section-header" id="reports-heading" hidden={!groupsLoaded || groups.length === 0}>
  <h2 class="section-label">Recent reports</h2>
  <div class="reports-search">
    <input
      type="search"
      class="reports-search-input"
      id="reports-search"
      placeholder="Search reports…"
      autocomplete="off"
      aria-label="Filter reports by player name"
      bind:value={searchQuery}
    >
    <button
      type="button"
      class="reports-search-clear"
      id="reports-search-clear"
      hidden={!searchQuery}
      aria-label="Clear search"
      on:click={clearSearch}
    >×</button>
  </div>
</div>
<p class="reports-empty" id="reports-empty" hidden={!groupsLoaded || groups.length > 0}>
  <strong>No reports yet</strong>
  Enter a Riot ID above to generate your first coaching report.
</p>
<p class="reports-no-match" id="reports-no-match" hidden={!hasNoMatch}>
  No reports match your search.
</p>
<section class="player-cards" id="recent-reports" aria-label="Recent reports" hidden={groups.length === 0 || hasNoMatch}>
  {#each filteredGroups as group (group.slug)}
    <a
      class="player-card{group.busy ? ' is-busy' : ''}"
      href="/players/{group.slug}"
      use:link
      data-slug={group.slug}
      data-search={memberSearchText(group)}
      data-has-report={group.has_report ? '1' : '0'}
      data-job-state={group.job_state || ''}
      title={group.busy ? stageLabel(group.job_state) : undefined}
    >
      <div class="player-card-name">
        <span class="player-card-status" aria-hidden="true"></span>
        <div class="player-card-members">
          {#each (group.players && group.players.length ? group.players : [{ label: group.player || group.slug, profile_icon: null }]) as member (member.label)}
            <div class="player-card-member">
              {#if member.profile_icon}
                <img class="player-card-icon" src={member.profile_icon} alt="" width="24" height="24">
              {/if}
              <span class="player-card-label">{member.label}</span>
              {#if member.solo_rank_label}
                <span class="player-card-rank">
                  {#if member.solo_rank_icon}
                    <img class="player-card-rank-icon" src={member.solo_rank_icon} alt="" width="20" height="20">
                  {/if}
                  <span class="player-card-rank-label">{member.solo_rank_label}</span>
                </span>
              {/if}
            </div>
          {/each}
        </div>
        {#if group.is_group}<span class="badge-group">Group</span>{/if}
      </div>
      <div class="player-card-meta">
        {#if group.has_report}
          {group.build_count} report{group.build_count !== 1 ? 's' : ''} · {group.total_games} games
        {:else}
          Queued for analysis…
        {/if}
      </div>
      {#if group.busy}
        <div class="player-card-stage">{stageLabel(group.job_state)}</div>
      {/if}
    </a>
  {/each}
</section>
</div>
