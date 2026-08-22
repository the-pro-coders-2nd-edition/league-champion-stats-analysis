<script>
  import Skeleton from './Skeleton.svelte';
</script>

<div class="section-header library-header-skeleton" aria-hidden="true">
  <div class="library-header-skeleton__label">
    <Skeleton width="128px" height="17px" radius="6px" />
  </div>
  <div class="library-header-skeleton__filters">
    <Skeleton width="42px" height="32px" radius="999px" />
    <Skeleton width="88px" height="32px" radius="999px" />
    <Skeleton width="62px" height="32px" radius="999px" />
  </div>
  <div class="library-header-skeleton__search">
    <Skeleton width="100%" height="40px" radius="8px" />
  </div>
</div>

<style>
  .library-header-skeleton__label {
    flex: 1;
    min-width: 0;
  }
  .library-header-skeleton__filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .library-header-skeleton__search {
    flex: 1;
    min-width: 180px;
    max-width: 280px;
  }
</style>
