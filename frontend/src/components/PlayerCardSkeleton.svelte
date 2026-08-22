<script>
  import Skeleton from './Skeleton.svelte';
</script>

<div class="player-card player-card--skeleton" aria-hidden="true">
  <div class="player-card-head">
    <div class="player-card-identity">
      <Skeleton width="24px" height="24px" radius="4px" />
      <Skeleton width="min(160px, 72%)" height="14px" radius="6px" />
    </div>
  </div>
  <div class="player-card-sub">
    <Skeleton width="88px" height="12px" radius="6px" />
  </div>
  <div class="player-card-previews player-card-skeleton-previews">
    {#each Array(5) as _}
      <Skeleton width="28px" height="28px" radius="6px" />
    {/each}
  </div>
  <div class="player-card-skeleton-updated">
    <Skeleton width="72px" height="11px" radius="4px" />
  </div>
</div>

<style>
  .player-card-skeleton-previews {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
  .player-card-skeleton-updated {
    margin-top: 8px;
  }
</style>
