<script>
  // Driver/Habit/Action are the same "kicker label + value" shape, just three
  // instances -- variant is static per generated output, not per-instance
  // data. Habit's presence is conditional on real data (story.habit), so its
  // {% if %} wrap stays in the Jinja macro, same as every other component's
  // per-instance-optional pieces.
  export let variant; // 'driver' | 'habit' | 'action'
  export let kicker; // 'Driver' | 'Habit' | 'Action'
  export let value;
</script>

<p class="form-story-{variant}"><span class="form-story-kicker">{kicker}</span> {value}</p>
