<script lang="ts">
  export let icon: string = '';
  export let label: string;
</script>

{#if icon}<img class="peer-rank-icon" src="{icon}" alt="" width="48" height="48">{/if}
<span class="peer-rank-label" id="peer-rank-label">{label}</span>

<style>
  .peer-rank-icon {
    width: 48px; height: 48px; object-fit: contain; flex: none;
  }
  .peer-rank-label { min-width: 0; }
</style>
