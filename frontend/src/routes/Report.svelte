<script>
  import { fetchBuild, fetchAccountViews, sendChatMessage } from '../lib/api.js';
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

  export let params = {};

  let payload = null;
  let error = null;
  let report = null;

  $: fetchBuild(params.slug, params.buildSlug)
    .then((result) => {
      payload = result;
      report = createReportState(payload, {
        fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
      });
    })
    .catch((err) => { error = err; });

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

  <Overview data={$view} />
  <Coaching data={$view} />
  <FormTracker data={$view} />
  <RankPeers data={$view} />
  <Matchups data={$view} />
  <ItemsRunes data={$view} />
  <LaneObjectivesDeaths data={$view} />
  <VisionEconomyTeamfightsPositioning data={$view} />
  <GameReview data={$view} />
  <Graphs data={$view} />

  <Chatbot data={payload} sendMessage={sendChatMessage} />
{/if}
