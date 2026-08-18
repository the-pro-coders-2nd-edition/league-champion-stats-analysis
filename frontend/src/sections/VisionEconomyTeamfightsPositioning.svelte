<script>
  import Pill from '../components/Pill.svelte';
  import SectionPulse from '../components/SectionPulse.svelte';
  import TieredCards from '../components/TieredCards.svelte';
  import PositioningHint from '../components/PositioningHint.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';

  export let data;

  $: windowScopeOption = (data.game_window_options || []).find((o) => o.key === data.game_window_default);
  $: windowScopeLabel = windowScopeOption ? `${windowScopeOption.label} games` : 'All games';

  $: positioningHints = data.positioning_hints || [];
</script>

<section id="vision" class="report-section report-section--deepdive">
  <h2 class="section-title section-title--deepdive">
    <iconify-icon icon="lucide:eye" class="metric-icon metric-icon--blue" aria-hidden="true"></iconify-icon>
    <span>Vision</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <SectionPulse sectionId="vision" verdict={data.section_verdicts?.vision} />
  <div id="vision-cards"><TieredCards cards={data.vision_cards || []} moreLabel="More vision stats" /></div>
  <details class="section-details">
    <summary>Show charts</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-vision_trend" html={data.figures?.vision_trend || ''} />
      <p class="figure-caption">Vision score per minute over time — compare win and loss trajectories to spot when vision drops off.</p>
    </div>
  </details>
</section>

<section id="economy" class="report-section report-section--deepdive">
  <h2 class="section-title section-title--deepdive">
    <iconify-icon icon="lucide:coins" class="metric-icon metric-icon--gold" aria-hidden="true"></iconify-icon>
    <span>Economy</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <SectionPulse sectionId="economy" verdict={data.section_verdicts?.economy} />
  <div id="economy-cards"><TieredCards cards={data.economy_cards || []} moreLabel="More economy stats" /></div>
  <details class="section-details">
    <summary>Show charts</summary>
    <div class="figure-block">
      <PlotlyFigure id="fig-dpm_scatter" html={data.figures?.dpm_scatter || ''} />
      <p class="figure-caption">Damage vs gold income per game — dots above the trend line punch above their gold weight.</p>
    </div>
  </details>
</section>

<section id="teamfights" class="report-section report-section--deepdive">
  <h2 class="section-title section-title--deepdive">
    <iconify-icon icon="lucide:users" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Teamfights</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <SectionPulse sectionId="teamfights" verdict={data.section_verdicts?.teamfights} />
  <div id="teamfight-cards"><TieredCards cards={data.teamfight_cards || []} moreLabel="More teamfight stats" /></div>
</section>

<section id="positioning" class="report-section report-section--deepdive">
  <h2 class="section-title section-title--deepdive">
    <iconify-icon icon="lucide:footprints" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Positioning</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <p class="sub">Mid/late game map habits from timeline frames (post 14 min). Distances use LoL landmarks (1 screen ≈ 3000). Lower means closer.</p>
  {#if positioningHints.length}
    <div class="positioning-hints" id="positioning-hints">
      {#each positioningHints as hint}
        <PositioningHint tone={hint.tone} text={hint.text} />
      {/each}
    </div>
  {:else}
    <div class="positioning-hints" id="positioning-hints" style="display:none"></div>
  {/if}
  <div id="positioning-cards"><TieredCards cards={data.positioning_cards || []} moreLabel="More positioning stats" /></div>
</section>
