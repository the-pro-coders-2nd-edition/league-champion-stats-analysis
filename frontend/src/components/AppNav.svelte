<script>
  import { onMount } from 'svelte';
  import { link } from 'svelte-spa-router';
  import BuildCard from './BuildCard.svelte';
  import BuildCardSkeleton from './BuildCardSkeleton.svelte';
  import Chip from './Chip.svelte';

  export let builds = [];
  export let libraryItems = [];
  export let backHref = '';
  export let backLabel = '';
  export let playerSlug = '';
  export let activeBuildSlug = '';
  export let activeSlug = '';
  export let listLabel = 'Champions';
  export let loading = false;
  export let skeletonCount = 6;

  const NAV_COLLAPSE_KEY = 'report-nav-collapsed';

  let navCollapsed = true;

  onMount(() => {
    try {
      navCollapsed = localStorage.getItem(NAV_COLLAPSE_KEY) !== '0';
    } catch {
      // Private mode: collapse state lives for this page only.
    }
    return () => {
      document.documentElement.classList.remove('report-nav-collapsed');
    };
  });

  $: if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('report-nav-collapsed', navCollapsed);
  }

  function toggleNav() {
    navCollapsed = !navCollapsed;
    try {
      localStorage.setItem(NAV_COLLAPSE_KEY, navCollapsed ? '1' : '0');
    } catch {
      // Private mode: collapse state lives for this page only.
    }
  }

  $: foldTitle = navCollapsed
    ? (libraryItems.length || loading ? 'Expand reports menu' : 'Expand champions menu')
    : (libraryItems.length || loading ? 'Collapse reports menu' : 'Collapse champions menu');
  $: showNavSkeleton = loading && !builds.length && !libraryItems.length;
</script>

<nav class="report-nav{navCollapsed ? ' is-collapsed' : ''}" id="report-nav" aria-label="App navigation">
  <div class="nav-header">
    <a class="app-brand app-brand--nav" href="/" use:link title="Home">
      <img src="/out/assets/brand/logo.png" alt="" class="app-logo" aria-hidden="true">
      <span class="app-brand-title">League Champion Analyser</span>
    </a>
    <button
      type="button"
      class="nav-edge-toggle"
      id="nav-fold-btn"
      aria-expanded={!navCollapsed}
      aria-controls="nav-builds-panel"
      title={foldTitle}
      on:click={toggleNav}
    >
      <iconify-icon
        class="nav-edge-toggle-icon"
        icon={navCollapsed ? 'mdi:chevron-right' : 'mdi:chevron-left'}
        width="16"
        height="16"
        aria-hidden="true"
      ></iconify-icon>
    </button>
  </div>
  {#if backHref}
    <div class="nav-back">
      <a href={backHref} use:link>{backLabel}</a>
    </div>
  {/if}
  {#if builds.length}
    <div class="nav-builds" id="nav-builds-panel">
      <div class="nav-builds-label">{listLabel}</div>
      <div class="build-grid">
        {#each builds as build (build.slug)}
          <BuildCard
            {build}
            href="/players/{playerSlug}/{build.slug}"
            density="nav"
            active={build.slug === activeBuildSlug}
          />
        {/each}
      </div>
    </div>
  {:else if libraryItems.length}
    <div class="nav-builds" id="nav-builds-panel">
      <div class="nav-builds-label">{listLabel}</div>
      <div class="build-grid">
        {#each libraryItems as group (group.slug)}
          <a
            class="build-card{group.slug === activeSlug ? ' is-default' : ''}{group.busy ? ' is-busy' : ''}"
            href="/players/{group.slug}"
            use:link
            title={group.player || group.slug}
          >
            {#if group.players && group.players[0] && group.players[0].profile_icon}
              <img src={group.players[0].profile_icon} alt="" class="game-icon">
            {/if}
            <div class="build-card-body">
              <strong>{group.player || group.slug}</strong>
              <div class="meta">
                {#if group.has_report}
                  {group.build_count || 0} report{(group.build_count || 0) !== 1 ? 's' : ''}
                {:else}
                  Queued
                {/if}
              </div>
              {#if group.watch_enabled}
                <Chip tone="info" label="Watching" caps={true} density="compact" />
              {/if}
            </div>
          </a>
        {/each}
      </div>
    </div>
  {:else if showNavSkeleton}
    <div class="nav-builds" id="nav-builds-panel" aria-busy="true">
      <div class="nav-builds-label">{listLabel}</div>
      <div class="build-grid">
        {#each Array(skeletonCount) as _}
          <BuildCardSkeleton density="nav" />
        {/each}
      </div>
    </div>
  {/if}
</nav>
