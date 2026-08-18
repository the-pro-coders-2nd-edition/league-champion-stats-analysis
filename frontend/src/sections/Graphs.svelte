<script>
  import Pill from '../components/Pill.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';

  export let data;

  let helpOpen = false;

  function toggleHelp() {
    helpOpen = !helpOpen;
  }

  $: windowScopeOption = (data.game_window_options || []).find((o) => o.key === data.game_window_default);
  $: windowScopeLabel = windowScopeOption ? `${windowScopeOption.label} games` : 'All games';
  $: figureHints = data.figure_hints || {};
</script>

<section id="graphs" class="report-section report-section--deepdive">
  <h2 class="section-title section-title--deepdive">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>For the curious</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <p class="sub">Correlations, win predictors, and game archetypes — a data-science view for readers who want to dig deeper.</p>
  <details class="section-details section-details--advanced">
    <summary>Show advanced analytics</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-correlation_heatmap" html={data.figures?.correlation_heatmap || ''} />
      <p class="figure-caption">How stats move together — strong red/blue pairs mean two metrics rise or fall in tandem across your games.</p>
    </div>
    <div class="figure-block">
      {#if figureHints.win_correlation_bar}
        <p class="figure-header" id="hint-win_correlation_bar">{figureHints.win_correlation_bar}</p>
      {:else}
        <p class="figure-header" id="hint-win_correlation_bar" style="display:none"></p>
      {/if}
      <PlotlyFigure id="fig-win_correlation_bar" html={data.figures?.win_correlation_bar || ''} />
      <p class="figure-caption">Which stats most align with winning — longer bars (positive or negative) matter more for your results.</p>
    </div>
    <div class="figure-block">
      {#if figureHints.feature_importance}
        <p class="figure-header" id="hint-feature_importance">{figureHints.feature_importance}</p>
      {:else}
        <p class="figure-header" id="hint-feature_importance" style="display:none"></p>
      {/if}
      <PlotlyFigure id="fig-feature_importance" html={data.figures?.feature_importance || ''} />
      <p class="figure-caption">What the early-game win model weighs most — focus practice on the top features when sample size is sufficient.</p>
    </div>
    <div class="figure-block">
      <div class="figure figure--with-help">
        <button
          type="button"
          class="figure-info-btn"
          aria-expanded={helpOpen}
          aria-controls="archetype-help"
          title="How to read this chart"
          on:click={toggleHelp}
        >?</button>
        <div class="figure-info-panel" class:is-open={helpOpen} id="archetype-help" role="region" aria-label="Game archetypes guide">
          <strong>How to read this chart</strong>
          <ul>
            <li>Each dot is one game. <em>Color</em> = archetype. <em>Shape</em> = win or loss.</li>
            <li>Games with similar stats (gold @10/@15, deaths, damage, vision, length) sit near each other.</li>
            <li>The axes are a compressed view — they don't map to a single stat. Focus on color, shape, and clusters.</li>
          </ul>
          <strong style="display:block;margin-top:10px;">Archetype labels</strong>
          <ul>
            <li><em>Lane stomp win</em> — ahead ≥1k gold @15</li>
            <li><em>Comeback win</em> — behind ≥750 @15, still won</li>
            <li><em>Scaling win</em> — 32+ minutes</li>
            <li><em>Clean win</em> — other wins</li>
            <li><em>Throw</em> — ahead ≥750 @15, lost</li>
            <li><em>One-sided loss</em> — behind ≥1k @15</li>
            <li><em>Close loss</em> — other losses</li>
          </ul>
        </div>
        <PlotlyFigure id="fig-cluster_scatter" figureClass="" html={data.figures?.cluster_scatter || ''} />
      </div>
      <p class="figure-caption">Game archetypes in a compressed stat space — look for clusters of throws or comeback wins to spot patterns.</p>
    </div>
  </details>
</section>
