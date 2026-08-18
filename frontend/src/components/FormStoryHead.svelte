<script lang="ts">
  type Label = 'Fix' | 'Keep';

  export let label: Label;
  export let title: string;
</script>

<span class="form-story-label">{label}</span>
<strong class="form-story-title">{title}</strong>

<style>
  :global(.form-story-label) {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  }
  :global(.form-story--keep .form-story-label) { color: var(--win); }
  :global(.form-story--fix .form-story-label) { color: var(--loss); }
  :global(.form-story-title) { font-size: 15px; font-weight: 700; line-height: 1.3; }
</style>
