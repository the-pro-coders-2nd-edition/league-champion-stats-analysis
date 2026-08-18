<script>
  import RecCard from '../components/RecCard.svelte';
  import RecExtendButton from '../components/RecExtendButton.svelte';
  import SectionHeader from '../components/SectionHeader.svelte';

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
          <div class="rec-more" id="rec-more-positive" class:is-open={showAllPositive}>
            {#each positiveMore as rec (rec.anchor || rec.title)}
              <RecCard {rec} />
            {/each}
          </div>
          <RecExtendButton
            targetId="rec-more-positive"
            label={positiveMoreLabel}
            expanded={showAllPositive}
            on:click={() => (showAllPositive = !showAllPositive)}
          />
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
          <div class="rec-more" id="rec-more-negative" class:is-open={showAllNegative}>
            {#each negativeMore as rec (rec.anchor || rec.title)}
              <RecCard {rec} />
            {/each}
          </div>
          <RecExtendButton
            targetId="rec-more-negative"
            label={negativeMoreLabel}
            expanded={showAllNegative}
            on:click={() => (showAllNegative = !showAllNegative)}
          />
        {/if}
      {:else}
        <p class="rec-empty">Nothing critical flagged — solid performance overall.</p>
      {/if}
    </div>
  </div>
</section>
