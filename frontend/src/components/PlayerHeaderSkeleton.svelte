<script>
  import Skeleton from './Skeleton.svelte';
</script>

<div class="accounts-panel accounts-panel--hero player-header-skeleton" aria-hidden="true">
  <div class="accounts-panel-head">
    <Skeleton width="72px" height="10px" radius="4px" />
    <Skeleton width="56px" height="12px" radius="4px" />
  </div>
  <div class="accounts-panel-row">
    <Skeleton width="20px" height="20px" radius="4px" />
    <Skeleton width="min(180px, 58%)" height="12px" radius="4px" />
    <Skeleton width="76px" height="10px" radius="4px" />
  </div>
</div>
