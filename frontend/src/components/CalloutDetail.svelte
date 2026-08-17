<script lang="ts">
  export let detail: string;
</script>

<span class="peer-callout-detail">{detail}</span>

<style>
  :global(.peer-callout-detail) { font-size: 12px; color: var(--muted); line-height: 1.4; }
</style>
