<script>
  import Skeleton from './Skeleton.svelte';
  import BuildCardSkeleton from './BuildCardSkeleton.svelte';

  export let showSort = true;
  export let count = 6;
</script>

<div id="builds-section" aria-busy="true">
  <div class="section-header">
    <h2 class="section-label">Champions</h2>
    {#if showSort}
      <div class="champions-section-skeleton__sort">
        <Skeleton width="52px" height="32px" radius="999px" />
        <Skeleton width="78px" height="32px" radius="999px" />
        <Skeleton width="84px" height="32px" radius="999px" />
        <Skeleton width="62px" height="32px" radius="999px" />
      </div>
    {/if}
  </div>
  <div class="build-grid build-grid--page" id="builds-grid">
    {#each Array(count) as _}
      <BuildCardSkeleton density="page" />
    {/each}
  </div>
</div>

<style>
  .champions-section-skeleton__sort {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
</style>
