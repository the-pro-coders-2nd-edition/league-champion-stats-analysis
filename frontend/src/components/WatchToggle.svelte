<script>
  export let watching = false;
  export let disabled = false;
  export let intervalS = 0;
  export let id = 'watch-btn';

  function watchLabel(interval) {
    if (!interval) return 'Checking for new games';
    const minutes = Math.round(interval / 60);
    return minutes <= 1
      ? 'Checking every minute for new games'
      : `Checking every ${minutes} min for new games`;
  }
</script>

<button
  class="watch-toggle{watching ? ' is-on' : ''}"
  {id}
  type="button"
  role="switch"
  aria-checked={watching}
  on:click
  {disabled}
  title={watching ? watchLabel(intervalS) : 'Refresh automatically after each new game'}
>
  <span class="watch-toggle-track switch-track" aria-hidden="true"><span class="watch-toggle-knob switch-track-knob"></span></span>
  <span class="watch-toggle-label">{watching ? 'Watching this player' : 'Watch this player'}</span>
</button>
