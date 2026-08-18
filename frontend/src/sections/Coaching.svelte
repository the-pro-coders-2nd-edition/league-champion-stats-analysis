<script>
  import RecCard from '../components/RecCard.svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import ShowMore from '../components/ShowMore.svelte';

  export let data;

  let showAllPositive = false;
  let showAllNegative = false;

  $: positive = data.positive_recommendations || [];
  $: negative = data.negative_recommendations || [];
  $: visibleCount = data.recommendation_visible_count;

  $: positiveVisible = positive.slice(0, visibleCount);
  $: positiveMore = positive.slice(visibleCount);
  $: negativeVisible = negative.slice(0, visibleCount);
  $: negativeMore = negative.slice(visibleCount);

  $: positiveMoreLabel = `Show ${positiveMore.length} more strength${positiveMore.length !== 1 ? 's' : ''}`;
  $: negativeMoreLabel = `Show ${negativeMore.length} more area${negativeMore.length !== 1 ? 's' : ''} to improve`;
</script>

<section id="coaching" class="report-section report-section--summary">
  <SectionHeader
    id="coaching"
    title="Coaching"
    icon="lightbulb"
    lead="Strengths on the left, areas to improve on the right — ranked by what matters most for your next games."
  />
  <div class="rec-columns" id="recommendations-root">
    <div class="rec-column rec-column-positive" id="rec-positive-column">
      <h3>Keep doing</h3>
      {#if positive.length}
        {#each positiveVisible as rec (rec.anchor || rec.title)}
          <RecCard {rec} />
        {/each}
        {#if positiveMore.length}
          <ShowMore bind:open={showAllPositive} triggerClass="rec-extend" label={positiveMoreLabel} openLabel="Show less" id="rec-more-positive">
            {#each positiveMore as rec (rec.anchor || rec.title)}
              <RecCard {rec} />
            {/each}
          </ShowMore>
        {/if}
      {:else}
        <p class="rec-empty">No clear strengths surfaced yet — keep playing more games.</p>
      {/if}
    </div>
    <div class="rec-column rec-column-negative" id="rec-negative-column">
      <h3>Work on</h3>
      {#if negative.length}
        {#each negativeVisible as rec (rec.anchor || rec.title)}
          <RecCard {rec} />
        {/each}
        {#if negativeMore.length}
          <ShowMore bind:open={showAllNegative} triggerClass="rec-extend" label={negativeMoreLabel} openLabel="Show less" id="rec-more-negative">
            {#each negativeMore as rec (rec.anchor || rec.title)}
              <RecCard {rec} />
            {/each}
          </ShowMore>
        {/if}
      {:else}
        <p class="rec-empty">Nothing critical flagged — solid performance overall.</p>
      {/if}
    </div>
  </div>
</section>
