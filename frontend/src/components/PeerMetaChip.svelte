<script lang="ts">
  type Modifier = '' | 'confidence';

  export let modifier: Modifier = '';
  export let text: string;
</script>

<span class="peer-meta-chip{modifier ? ' peer-meta-chip--' + modifier : ''}">{text}</span>

<style>
  :global(.peer-meta-chip) {
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
    padding: 4px 8px; border-radius: 999px; background: var(--color-surface); color: var(--color-neutral-400);
  }
  :global(.peer-meta-chip--confidence) { color: var(--color-text); }
</style>
