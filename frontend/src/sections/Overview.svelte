<script>
  import HeroChip from '../components/HeroChip.svelte';
  import NavLink from '../components/NavLink.svelte';
  import Meter from '../components/Meter.svelte';
  import ReportPlayerChip from '../components/ReportPlayerChip.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import CareerNode from '../components/CareerNode.svelte';
  import Pill from '../components/Pill.svelte';

  export let data;
  export let onGoToCareer = () => {};

  function heroChipTone(card) {
    if (card.value_class === 'win') return 'good';
    if (card.value_class === 'loss') return 'bad';
    return 'stat';
  }

  function scorePercent(score) {
    return Math.max(0, Math.min(100, Math.round(Number(score) || 0)));
  }

  function toneTextColor(tone) {
    const resolved = tone || 'flat';
    return resolved === 'flat' ? 'var(--color-text)' : `var(--tone-${resolved}-fg)`;
  }

  // `score_color` already encodes the Strength/Solid/Focus verdict decided in Python;
  // this just maps that string back to a tone name for the Meter fill.
  function heroScoreTone(colorVar) {
    if (colorVar === 'var(--tone-good-fg)') return 'good';
    if (colorVar === 'var(--tone-bad-fg)') return 'bad';
    return 'flat';
  }

  $: heroChips = (data.overview_cards || []).slice(0, 4);
  $: topTips = data.top_tips || [];
  $: scoreComponents = data.score_components || [];
  $: showPlayerChips = data.report_players && data.report_players.length > 1;
  $: scoreColor = data.score_color || 'var(--color-text)';
  $: scoreVerdictLabel = data.score_verdict_label || 'Solid';
  $: scorePct = scorePercent(data.score);
  $: heroTone = heroScoreTone(scoreColor);
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
    </div>
    <div class="hero-actions-block">
      <div class="hero-actions-title">Focus next game</div>
      <div class="hero-actions" id="hero-actions">
        {#if topTips.length}
          {#each topTips as tip, index}
            <NavLink anchor={tip.anchor || 'coaching'} index={index + 1} label={tip.action || tip.title} variant="row" />
          {/each}
        {:else}
          <p class="hero-action-empty">Play a few more games to unlock personalised coaching tips.</p>
        {/if}
      </div>
    </div>
  </div>

  <div class="summary-grid">
    <div class="summary-grid-main">
      {#if showPlayerChips}
        <div class="accounts-panel">
          <div class="accounts-panel-head">
            <span class="accounts-panel-label">Accounts in this pool</span>
            <span class="accounts-panel-count">{data.report_players.length} accounts</span>
          </div>
          {#each data.report_players as member}
            <div class="accounts-panel-row">
              {#if member.profile_icon}
                <img src={member.profile_icon} alt="" class="accounts-panel-icon">
              {:else}
                <span class="accounts-panel-icon accounts-panel-icon--placeholder" aria-hidden="true"></span>
              {/if}
              <span class="accounts-panel-name">
                {member.label}
                {#if member.is_main}
                  <Pill tone="good" variant="soft" dot={false} label="Main" />
                {/if}
              </span>
              <span class="accounts-panel-rank">{member.solo_rank_division || 'Unranked'}</span>
              <span class="accounts-panel-lp">{member.solo_lp != null ? `${member.solo_lp} LP` : '—'}</span>
              <span class="accounts-panel-region">{member.region || '—'}</span>
            </div>
          {/each}
        </div>
      {/if}

      <h2 class="score-breakdown-title" id="score-breakdown">Score breakdown</h2>
      <div class="score-set" id="score-comps">
        {#each scoreComponents as comp}
          {@const textColor = toneTextColor(comp.tone)}
          <div class="score-set-item" title={comp.hint || null}>
            <div class="score-set-item-head">
              <span class="score-set-item-name">{comp.name}</span>
              <span class="score-set-item-score" style="color: {textColor}">{scorePercent(comp.score)}</span>
            </div>
            <Meter value={comp.score} tone={comp.tone} size="md" reference={50} />
            <div class="score-set-item-foot">
              <span class="score-set-item-verdict" style="color: {textColor}">{comp.verdict || 'Solid'}</span>
              {#if comp.value}<span class="score-set-item-sub">{comp.value}</span>{/if}
            </div>
          </div>
        {/each}
      </div>
    </div>

    <div class="summary-grid-side">
      <div class="hero-score-panel" id="improvement-score-card">
        <div class="hero-score-head">
          <div class="hero-score-label">Improvement score</div>
          <span id="score-verdict-label" class="hero-score-verdict" style="color: {scoreColor}">{scoreVerdictLabel}</span>
        </div>
        <div class="hero-score-value" id="score-value" data-score={data.score} style="color: {scoreColor}">{scorePct}<span>/100</span></div>
        <Meter value={data.score} tone={heroTone} size="md" reference={50} --meter-margin="8px 0 0" />
      </div>

      {#if data.career && data.career.has_career}
        <div class="career-widget" id="career-widget">
          <div class="career-widget-head">
            <span class="career-widget-label">Live block</span>
            <button type="button" class="career-widget-link" id="career-widget-link" on:click={onGoToCareer}>
              All goals
            </button>
          </div>
          {#each data.career.widget as item, index (index)}
            <CareerNode
              compact={true}
              state={item.state}
              stateClass={item.state_class}
              tone={item.tone}
              pct={item.pct}
              mark={item.mark}
              text={item.text}
              note={item.note}
              count={item.count}
              last={index === data.career.widget.length - 1}
            />
          {/each}
        </div>
      {/if}
    </div>
  </div>

  <div id="overview-cards" class="cards">
    {#each data.overview_cards || [] as card}
      <MetricCard {card} />
    {/each}
  </div>
</section>
