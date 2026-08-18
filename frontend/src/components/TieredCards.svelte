<script lang="ts">
  import MetricCard from './MetricCard.svelte';

  export let cards: Array<Record<string, any>> = [];
  export let moreLabel: string = 'More stats';

  $: hasHeadline = cards.some((card) => card.tier === 'headline');
  $: headline = hasHeadline ? cards.filter((card) => card.tier === 'headline') : cards;
  $: more = hasHeadline ? cards.filter((card) => card.tier === 'more') : [];
</script>

<div class="cards-tiered">
  <div class="cards-tiered-head">
    {#each headline as card}
      <MetricCard {card} />
    {/each}
    {#if more.length}
      <details class="cards-more">
        <summary>{moreLabel}</summary>
      </details>
    {/if}
  </div>
  {#if more.length}
    <div class="cards-tiered-more">
      {#each more as card}
        <MetricCard {card} />
      {/each}
    </div>
  {/if}
</div>
