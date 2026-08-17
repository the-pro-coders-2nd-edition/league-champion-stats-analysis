<script>
  // Shared label+title header for both callout shapes this design system
  // concept currently covers in this codebase: peer-callout (weaknesses/
  // strengths vs rank peers) and form-story (recent-form keep/fix cards).
  // prefix is static per generated output ('peer-callout' | 'form-story'),
  // not per-instance data -- same pattern as Pill's scope_kind variants.
  export let prefix;
  export let label;
  export let title;
</script>

<span class="{prefix}-label">{label}</span>
<strong class="{prefix}-title">{title}</strong>
