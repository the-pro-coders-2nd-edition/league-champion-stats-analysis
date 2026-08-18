<script>
  import HeroChip from '../components/HeroChip.svelte';
  import HeroAction from '../components/HeroAction.svelte';
  import ScoreSetItem from '../components/ScoreSetItem.svelte';
  import ReportPlayerChip from '../components/ReportPlayerChip.svelte';
  import MetricCard from '../components/MetricCard.svelte';

  export let data;

  function heroChipTone(card) {
    if (card.value_class === 'win') return 'good';
    if (card.value_class === 'loss') return 'bad';
    return 'stat';
  }

  $: heroChips = (data.overview_cards || []).slice(0, 4);
  $: topTips = data.top_tips || [];
  $: scoreComponents = data.score_components || [];
  $: showPlayerChips = data.report_players && data.report_players.length > 1;
</script>

<section id="overview" class="report-section report-section--summary">
  <div class="report-hero" id="report-hero">
    <div class="report-hero-main">
      <div class="report-hero-identity">
        {#if data.champion_icon}
          <img src={data.champion_icon} alt="" class="game-icon">
        {/if}
        <div class="report-hero-copy">
          <h1 class="report-page-title">
            <span class="build-heading">
              <span>{data.champion}</span>
              {#if data.role_icon_href}
                <img src={data.role_icon_href} alt={data.role_display || ''} title={data.role_display || ''} class="role-icon role-icon--sm">
              {:else if data.role_display}
                <span>{data.role_display}</span>
              {/if}
            </span>
          </h1>
          {#if showPlayerChips}
            <div class="report-players">
              {#each data.report_players as member}
                <ReportPlayerChip icon={member.profile_icon || ''} label={member.label} />
              {/each}
            </div>
          {:else}
            <div class="report-player-name">{data.player_name}</div>
          {/if}
          <div class="report-hero-meta" id="overview-subtitle">{data.total_games} {data.queue_label} games · patches {data.patch_range}</div>
          <div class="report-hero-chips ui-chip-row" id="hero-chips" hidden={heroChips.length === 0}>
            {#each heroChips as card}
              <HeroChip tone={heroChipTone(card)} label={card.label} value={card.value} valueColor={card.value_color || ''} />
            {/each}
          </div>
        </div>
      </div>
      <div class="hero-score-panel">
        <div class="hero-score-label">Improvement score</div>
        <div class="hero-score-value" id="score-value" data-score={data.score}>{data.score}<span>/100</span></div>
      </div>
    </div>
    <div class="hero-actions-block">
      <div class="hero-actions-title">Focus next game</div>
      <div class="hero-actions" id="hero-actions">
        {#if topTips.length}
          {#each topTips as tip, index}
            <HeroAction anchor={tip.anchor || 'coaching'} index={index + 1} label={tip.action || tip.title} />
          {/each}
        {:else}
          <p class="hero-action-empty">Play a few more games to unlock personalised coaching tips.</p>
        {/if}
      </div>
    </div>
  </div>
  <div class="score-breakdown" id="score-breakdown">
    <div class="score-breakdown-title">Score breakdown</div>
    <div class="score-comps" id="score-comps">
      {#each scoreComponents as comp}
        <ScoreSetItem
          name={comp.name}
          scoreLabel={String(Math.round(comp.score))}
          scoreValue={comp.score}
          value={comp.value}
          tone={comp.tone || 'solid'}
          verdict={comp.verdict || 'Solid'}
          hint={comp.hint}
        />
      {/each}
    </div>
  </div>
  <div id="overview-cards" class="cards">
    {#each data.overview_cards || [] as card}
      <MetricCard {card} />
    {/each}
  </div>
</section>
