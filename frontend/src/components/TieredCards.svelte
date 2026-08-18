<script>
  // Plain JS, not lang="ts": MetricCard.svelte is untyped (it imports the untyped MetricLabel.svelte
  // — see MetricCard.svelte's own comment), and svelte-check flags an untyped child component
  // import as an error under lang="ts".
  import MetricCard from './MetricCard.svelte';
  import ShowMore from './ShowMore.svelte';

  export let cards = [];
  export let moreLabel = 'More stats';

  let open = false;

  $: hasHeadline = cards.some((card) => card.tier === 'headline');
  $: headline = hasHeadline ? cards.filter((card) => card.tier === 'headline') : cards;
  $: more = hasHeadline ? cards.filter((card) => card.tier === 'more') : [];
</script>

<div class="cards-tiered">
  <div class="cards-tiered-head">
    {#each headline as card}
      <MetricCard {card} />
    {/each}
  </div>
  {#if more.length}
    <ShowMore
      bind:open
      indicator="chevron"
      triggerClass="cards-more-trigger"
      label={moreLabel}
      style="--show-more-content-display: flex; --show-more-content-wrap: wrap; --show-more-content-gap: 14px; --show-more-content-margin-top: 14px;"
    >
      {#each more as card}
        <MetricCard {card} />
      {/each}
    </ShowMore>
  {/if}
</div>
