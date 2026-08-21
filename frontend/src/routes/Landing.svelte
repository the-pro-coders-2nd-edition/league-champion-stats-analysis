<script>
  import { onMount } from 'svelte';
  import { push } from 'svelte-spa-router';
  import { submitAnalysis, fetchActivity, fetchGroups } from '../lib/api.js';
  import { createPoller } from '../lib/poller.js';
  import AppNav from '../components/AppNav.svelte';
  import AnalyzeForm from '../components/AnalyzeForm.svelte';
  import PlayerCard from '../components/PlayerCard.svelte';
  import SegmentedControl from '../components/SegmentedControl.svelte';

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

  let analyzeForm;
  let groups = [];
  let groupsLoaded = false;
  let searchQuery = '';
  let libraryFilter = 'all';
  const poller = createPoller();

  const FILTER_ITEMS = [
    { value: 'all', label: 'All' },
    { value: 'busy', label: 'In progress' },
    { value: 'groups', label: 'Groups' },
  ];

  async function handleSubmit(event) {
    const { players, region, minGames } = event.detail;
    try {
      const data = await submitAnalysis({ players, region, minGames });
      push(`/players/${data.player_slug}`);
    } catch (err) {
      analyzeForm?.setError(err.message || 'Something went wrong.');
    }
  }

  function memberSearchText(group) {
    const bits = (group.players || []).map((member) => member.label || '');
    bits.push(group.player || '', group.slug || '');
    for (const build of group.preview_builds || []) {
      bits.push(build.champion || '', build.slug || '');
    }
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
        preview_builds: [],
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
  $: filteredGroups = groups.filter((group) => {
    if (libraryFilter === 'busy' && !group.busy) return false;
    if (libraryFilter === 'groups' && !group.is_group) return false;
    if (normalizedQuery && !memberSearchText(group).includes(normalizedQuery)) return false;
    return true;
  });
  $: hasNoMatch = groupsLoaded && groups.length > 0 && filteredGroups.length === 0;
  $: hasLibrary = groupsLoaded && groups.length > 0;
</script>

<div class="layout">
<AppNav libraryItems={groups} listLabel="Reports" />
<main class="library-main">

<AnalyzeForm bind:this={analyzeForm} compact={hasLibrary} on:submit={handleSubmit} />

{#if hasLibrary}
  <div class="section-header" id="reports-heading">
    <h2 class="section-label">Your reports</h2>
    <SegmentedControl
      items={FILTER_ITEMS}
      value={libraryFilter}
      variant="pill"
      size="sm"
      ariaLabel="Filter reports"
      on:select={(event) => { libraryFilter = event.detail.value; }}
    />
    <div class="reports-search">
      <input
        type="search"
        class="reports-search-input"
        id="reports-search"
        placeholder="Search players or champions…"
        autocomplete="off"
        aria-label="Filter reports by player or champion"
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
{/if}

<p class="reports-empty" id="reports-empty" hidden={!groupsLoaded || groups.length > 0}>
  <strong>No reports yet</strong>
  Enter a Riot ID above to generate your first coaching report.
</p>
<p class="reports-no-match" id="reports-no-match" hidden={!hasNoMatch}>
  No reports match your search.
</p>
<section class="player-cards" id="recent-reports" aria-label="Recent reports" hidden={groups.length === 0 || hasNoMatch}>
  {#each filteredGroups as group (group.slug)}
    <PlayerCard {group} {stageLabel} />
  {/each}
</section>
</main>
</div>
