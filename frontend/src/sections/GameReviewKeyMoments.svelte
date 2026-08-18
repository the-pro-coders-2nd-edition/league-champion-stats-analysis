<script>
  export let game;
  export let tooltips = {};

  const MAP_SIZE = 14870;

  let selectedMomentIndex = 0;
  let frameIndex = 0;

  // Reset selection whenever the underlying game changes (new row picked in the rail).
  $: game, (selectedMomentIndex = 0);
  $: selectedMomentIndex, (frameIndex = 0);

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function iconCellHtml(name, iconHref) {
    const title = escapeHtml(name || '');
    if (!iconHref) return '—';
    return `<span class="icon-cell icon-cell--solo" title="${title}"><img src="${iconHref}" alt="${title}" class="game-icon game-icon--sm"></span>`;
  }

  function formatGameTime(minutes) {
    const totalSec = Math.max(0, Math.round(Number(minutes) * 60));
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    return `${min}:${String(sec).padStart(2, '0')}`;
  }

  function mapCoordToPct(x, y) {
    return {
      left: (x / MAP_SIZE) * 100,
      top: (1 - y / MAP_SIZE) * 100,
    };
  }

  function participantIsAlly(participant, gameSide) {
    const allyTeamId = gameSide === 'blue' ? 100 : 200;
    return participant.team_id === allyTeamId;
  }

  function keyMomentReason(moment) {
    const reasons = {
      baron: 'Baron secured — major map control',
      elder: 'Elder Dragon secured',
      dragon_soul: 'Dragon soul point',
      dragon: 'Dragon take',
      herald: 'Rift Herald secured',
      grubs: 'Void grubs secured',
      first_tower: 'First tower — early map lead',
      split_push: 'Split push structure',
      inhibitor: 'Inhibitor destroyed',
      nexus: 'Game-ending push',
      teamfight: 'Multi-kill teamfight',
      gold_swing: 'Large team gold swing',
    };
    const parts = [];
    const reason = reasons[moment.kind] || String(moment.kind || '').replace(/_/g, ' ');
    if (reason) parts.push(reason);
    if (moment.gold_swing) parts.push('+' + Math.round(moment.gold_swing / 100) / 10 + 'k gold');
    return parts.join(' · ');
  }

  $: moments = (game && game.key_moments) || [];
  $: hints = tooltips.key_moments || {};
  $: interpolationHint = hints.interpolation || 'Positions update on kills, wards, and minute snapshots.';
  $: moment = moments[selectedMomentIndex];
  $: frames = (moment && moment.frames) || [];
  $: frame = frames[frameIndex];
  $: scrubberMax = Math.max(0, frames.length - 1);
  $: mapBgStyle = game && game.map_background ? `background-image:url(${game.map_background});` : '';
  $: frameLabel = frame && frame.label ? ' · ' + frame.label : '';
  $: timeReadoutText = frame && frame.timestamp_ms != null
    ? `${frameIndex + 1}/${frames.length} · ${formatGameTime(frame.timestamp_ms / 60000)}${frameLabel}`
    : '0:00';
</script>

{#if !moments.length}
  <p class="sub">No high-impact team moments detected for this game.</p>
{:else}
  <p class="sub key-moment-note" title={interpolationHint}>{interpolationHint}</p>
  <div class="key-moment-layout">
    <div class="key-moment-list">
      {#each moments as m, index}
        <button
          type="button"
          class="key-moment-card{index === selectedMomentIndex ? ' is-selected' : ''}"
          data-moment-index={index}
          on:click={() => (selectedMomentIndex = index)}
        >
          <span class="key-moment-time">{formatGameTime(m.anchor_minute)}</span>
          <span class="key-moment-headline">{m.headline}</span>
          <span class="key-moment-badge {m.beneficiary === 'ally' ? 'key-moment-badge--ally' : 'key-moment-badge--enemy'}">{m.beneficiary}</span>
          <span class="key-moment-sub">{keyMomentReason(m)}</span>
        </button>
      {/each}
    </div>
    <div class="key-moment-viewer">
      <div id="key-moment-map-host">
        {#if frame}
          <div class="key-moment-map" style={mapBgStyle}>
            {#each frame.objectives || [] as objective}
              {@const coords = mapCoordToPct(objective.x, objective.y)}
              {@const status = objective.available ? 'up' : 'down'}
              <div
                class="map-pin map-pin--objective{objective.highlighted ? ' map-pin--objective-highlight' : ''} map-pin--objective-{status}"
                data-objective-kind={objective.kind}
                style="left:{coords.left}%;top:{coords.top}%;"
                title="{objective.kind} ({status})"
              >
                {@html iconCellHtml(objective.kind, objective.objective_icon)}
              </div>
            {/each}
            {#each frame.participants || [] as participant}
              {@const coords = mapCoordToPct(participant.x, participant.y)}
              {@const ally = participantIsAlly(participant, game.side)}
              <div
                class="map-pin map-pin--champion map-pin--{ally ? 'ally' : 'enemy'}{participant.dead ? ' map-pin--dead' : ''}"
                data-participant-id={participant.participant_id}
                style="left:{coords.left}%;top:{coords.top}%;"
                title="{participant.dead ? participant.champion + ' (dead)' : participant.champion}"
              >
                {@html iconCellHtml(participant.champion, participant.champion_icon)}
              </div>
            {/each}
          </div>
        {/if}
      </div>
      <div class="key-moment-controls">
        <label class="key-moment-scrub-label" for="key-moment-scrubber">Drag to scrub minutes</label>
        <input
          type="range"
          id="key-moment-scrubber"
          class="key-moment-scrubber"
          min="0"
          max={scrubberMax}
          bind:value={frameIndex}
          aria-label="Drag to scrub minute snapshots"
        />
        <span id="key-moment-time-readout" class="key-moment-time-readout">{timeReadoutText}</span>
      </div>
    </div>
  </div>
{/if}
