<script>
  const PREFIXES = ['peer-callout', 'form-story'];

  // label ('Fix'|'Keep') is a Jinja conditional expression at generation
  // time, not a literal, so it can't be validated here.
  export let prefix;
  export let label;
  export let title;

  if (!PREFIXES.includes(prefix)) throw new Error(`CalloutHead: invalid prefix "${prefix}"`);
</script>

<span class="{prefix}-label">{label}</span>
<strong class="{prefix}-title">{title}</strong>

<style>
  :global(.peer-callout-label) {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  }
  :global(.peer-callout--keep .peer-callout-label) { color: var(--win); }
  :global(.peer-callout--fix .peer-callout-label) { color: var(--loss); }
  :global(.peer-callout-title) { font-size: 14px; font-weight: 700; line-height: 1.3; }

  :global(.form-story-label) {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  }
  :global(.form-story--keep .form-story-label) { color: var(--win); }
  :global(.form-story--fix .form-story-label) { color: var(--loss); }
  :global(.form-story-title) { font-size: 15px; font-weight: 700; line-height: 1.3; }
</style>
