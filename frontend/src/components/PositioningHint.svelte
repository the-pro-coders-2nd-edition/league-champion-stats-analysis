<script lang="ts">
  type Tone = 'positive' | 'negative';

  export let tone: Tone;
  export let text: string;
</script>

<p class="positioning-hint positioning-hint--{tone}">{text}</p>

<style>
  :global(.positioning-hint) {
    margin: 0; padding: 10px 14px; border-radius: 10px; font-size: 13px; line-height: 1.45;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
  }
  :global(.positioning-hint--positive) { border-color: rgba(63, 182, 139, 0.35); }
  :global(.positioning-hint--negative) { border-color: rgba(224, 85, 99, 0.35); }
</style>
