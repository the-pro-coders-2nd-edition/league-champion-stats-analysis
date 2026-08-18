<script>
  import SectionHeader from '../components/SectionHeader.svelte';
  import Disclosure from '../components/Disclosure.svelte';
  import SectionPulse from '../components/SectionPulse.svelte';
  import TieredCards from '../components/TieredCards.svelte';
  import Callout from '../components/Callout.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';

  export let data;

  $: positioningHints = data.positioning_hints || [];
</script>

<section id="vision" class="report-section report-section--deepdive">
  <SectionHeader id="vision" title="Vision" icon="eye" />
  <SectionPulse sectionId="vision" verdict={data.section_verdicts?.vision} />
  <div id="vision-cards"><TieredCards cards={data.vision_cards || []} moreLabel="More vision stats" /></div>
  <Disclosure variant="pill" chevron="leading">
    <svelte:fragment slot="summary">Show charts</svelte:fragment>
    <div class="figure-block">
      <PlotlyFigure id="fig-vision_trend" html={data.figures?.vision_trend || ''} />
      <p class="figure-caption">Vision score per minute over time — compare win and loss trajectories to spot when vision drops off.</p>
    </div>
  </Disclosure>
</section>

<section id="economy" class="report-section report-section--deepdive">
  <SectionHeader id="economy" title="Economy" icon="coins" />
  <SectionPulse sectionId="economy" verdict={data.section_verdicts?.economy} />
  <div id="economy-cards"><TieredCards cards={data.economy_cards || []} moreLabel="More economy stats" /></div>
  <Disclosure variant="pill" chevron="leading">
    <svelte:fragment slot="summary">Show charts</svelte:fragment>
    <div class="figure-block">
      <PlotlyFigure id="fig-dpm_scatter" html={data.figures?.dpm_scatter || ''} />
      <p class="figure-caption">Damage vs gold income per game — dots above the trend line punch above their gold weight.</p>
    </div>
  </Disclosure>
</section>

<section id="teamfights" class="report-section report-section--deepdive">
  <SectionHeader id="teamfights" title="Teamfights" icon="users" />
  <SectionPulse sectionId="teamfights" verdict={data.section_verdicts?.teamfights} />
  <div id="teamfight-cards"><TieredCards cards={data.teamfight_cards || []} moreLabel="More teamfight stats" /></div>
</section>

<section id="positioning" class="report-section report-section--deepdive">
  <SectionHeader
    id="positioning"
    title="Positioning"
    icon="footprints"
    lead="Mid/late game map habits from timeline frames (post 14 min). Distances use LoL landmarks (1 screen ≈ 3000). Lower means closer."
  />
  {#if positioningHints.length}
    <div class="positioning-hints" id="positioning-hints">
      {#each positioningHints as hint}
        <Callout tone={hint.tone === 'positive' ? 'good' : 'bad'} edge body={hint.text} />
      {/each}
    </div>
  {:else}
    <div class="positioning-hints" id="positioning-hints" style="display:none"></div>
  {/if}
  <div id="positioning-cards"><TieredCards cards={data.positioning_cards || []} moreLabel="More positioning stats" /></div>
</section>
