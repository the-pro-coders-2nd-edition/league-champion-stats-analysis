<script>
  // Presence conditional on real data (v.fix and v.fix.title), stays wrapped
  // in Jinja. anchor already carries its own Jinja-side fallback
  // ({{ v.fix.anchor or 'coaching' }}) so it's always non-empty by the time
  // it reaches this component.
  export let anchor;
  export let title;
</script>

<a class="section-pulse-fix" href="#{anchor}">
  <span class="section-pulse-fix-kicker">Try this</span>
  <span class="section-pulse-fix-title">{title}</span>
</a>
