<script lang="ts">
  type Variant = 'driver' | 'habit' | 'action';

  const KICKERS: Record<Variant, string> = { driver: 'Driver', habit: 'Habit', action: 'Action' };

  export let variant: Variant;
  export let value: string;

  const kicker = KICKERS[variant];
</script>

<p class="form-story-{variant}"><span class="form-story-kicker">{kicker}</span> {value}</p>

<style>
  :global(.form-story-driver),
  :global(.form-story-habit),
  :global(.form-story-action) {
    margin: 0; font-size: 13px; line-height: 1.45; color: var(--text);
  }
  :global(.form-story-habit),
  :global(.form-story-driver) { color: var(--muted); }
  :global(.form-story-kicker) {
    display: inline-block; min-width: 3.6em; margin-right: 6px;
    font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    color: var(--muted);
  }
  :global(.form-story-action .form-story-kicker) { color: var(--accent); }
</style>
