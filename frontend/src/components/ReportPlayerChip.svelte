<script lang="ts">
  export let icon: string = '';
  export let label: string;
</script>

<span class="report-player-chip">
  {#if icon}<img src="{icon}" alt="" class="report-player-icon" width="22" height="22">{/if}
  <span>{label}</span>
</span>

<style>
  .report-player-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 8px 3px 3px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--panel-2); color: var(--muted); font-size: 12px; font-weight: 600;
  }
  .report-player-icon {
    width: 22px; height: 22px; border-radius: 4px; object-fit: cover;
    border: 1px solid var(--border); flex: none;
  }
</style>
