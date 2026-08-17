<script>
  const MODIFIERS = ['', 'confidence'];

  export let modifier = '';
  export let text;

  if (!MODIFIERS.includes(modifier)) throw new Error(`PeerMetaChip: invalid modifier "${modifier}"`);
</script>

<span class="peer-meta-chip{modifier ? ' peer-meta-chip--' + modifier : ''}">{text}</span>

<style>
  :global(.peer-meta-chip) {
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
    padding: 4px 8px; border-radius: 999px; background: var(--panel); color: var(--muted);
  }
  :global(.peer-meta-chip--confidence) { color: var(--text); }
</style>
