<script>
  import Skeleton from './Skeleton.svelte';

  export let density = 'page';
</script>

<div
  class="build-card{density === 'page' ? ' build-card--page' : ''} build-card--skeleton"
  aria-hidden="true"
>
  {#if density === 'page'}
    <Skeleton width="56px" height="56px" radius="12px" />
    <div class="build-card-body">
      <div class="build-card-heading build-card-skeleton-heading">
        <Skeleton width="92px" height="15px" radius="6px" />
        <Skeleton width="52px" height="12px" radius="6px" />
      </div>
      <div class="build-card-stats">
        <Skeleton width="72px" height="22px" radius="999px" />
        <Skeleton width="58px" height="22px" radius="999px" />
      </div>
      <div class="build-card-skeleton-meta">
        <Skeleton width="88px" height="11px" radius="4px" />
      </div>
    </div>
    <div class="build-card-score">
      <Skeleton width="36px" height="24px" radius="6px" />
      <Skeleton width="44px" height="10px" radius="4px" />
    </div>
  {:else}
    <Skeleton width="24px" height="24px" radius="5px" />
    <div class="build-card-body">
      <Skeleton width="108px" height="12px" radius="4px" />
      <div class="build-card-skeleton-meta">
        <Skeleton width="76px" height="11px" radius="4px" />
      </div>
    </div>
  {/if}
</div>

<style>
  .build-card-skeleton-heading {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .build-card-skeleton-meta {
    margin-top: 6px;
  }
  :global(.build-card--page) .build-card-skeleton-meta {
    margin-top: 8px;
  }
</style>
