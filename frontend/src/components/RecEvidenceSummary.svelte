<script lang="ts">
  export let text: string;
</script>

<p class="rec-evidence-summary">{text}</p>

<style>
  :global(.rec-evidence-summary) { margin: 0 0 4px; font-size: 12.5px; color: var(--text); }
</style>
