<script>
  import { createEventDispatcher } from 'svelte';
  import Button from './Button.svelte';
  import Panel from './Panel.svelte';

  export let compact = false;

  const dispatch = createEventDispatcher();
  const MAX_PLAYERS = 8;
  const REGION_CHOICES = [
    ['EUW', 'euw1'],
    ['EUNE', 'eun1'],
    ['NA', 'na1'],
    ['KR', 'kr'],
    ['BR', 'br1'],
    ['LAN', 'la1'],
    ['LAS', 'la2'],
    ['OCE', 'oc1'],
    ['TR', 'tr1'],
    ['RU', 'ru'],
    ['JP', 'jp1'],
  ];
  const MIN_GAMES_CHOICES = [5, 10, 15, 20, 25, 30, 50];
  const DEFAULT_MIN_GAMES = 20;

  let playerInputs = [''];
  let region = 'euw1';
  let minGames = DEFAULT_MIN_GAMES;
  let submitting = false;
  let error = '';

  function addPlayerRow() {
    if (playerInputs.length >= MAX_PLAYERS) return;
    playerInputs = [...playerInputs, ''];
  }

  function removePlayerRow(index) {
    if (playerInputs.length <= 1) return;
    playerInputs = playerInputs.filter((_, i) => i !== index);
  }

  function handleSubmit(event) {
    event.preventDefault();
    error = '';
    const players = playerInputs.map((value) => value.trim()).filter(Boolean);
    if (!players.length) {
      error = 'Provide at least one Riot ID as Name#Tag.';
      return;
    }
    submitting = true;
    dispatch('submit', { players, region, minGames: Number(minGames) });
  }

  export function setSubmitting(value) {
    submitting = value;
  }

  export function setError(message) {
    error = message || '';
    submitting = false;
  }
</script>

<Panel class="panel-form{compact ? ' panel-form--compact' : ''}">
  <form id="analyze-form" class="analyze-form" on:submit={handleSubmit}>
    {#if !compact}
      <h2 class="section-label analyze-heading">New analysis</h2>
    {/if}
    <div id="player-rows" class="analyze-rows">
      {#each playerInputs as value, index (index)}
        <div class="analyze-row">
          <input
            name="riot_id"
            class="riot-input"
            placeholder="Riot ID (e.g. Faker#KR1)"
            autocomplete="off"
            bind:value={playerInputs[index]}
          >
          <Button
            variant="bare"
            size="sm"
            class="remove-player"
            hidden={playerInputs.length === 1}
            ariaLabel="Remove player"
            on:click={() => removePlayerRow(index)}
          >Remove</Button>
        </div>
      {/each}
    </div>
    <div class="analyze-toolbar">
      <Button variant="bare" size="sm" id="add-player" hidden={playerInputs.length >= MAX_PLAYERS} on:click={addPlayerRow}>
        + Add account
      </Button>
      <div class="analyze-toolbar-end">
        <select class="analyze-select" name="region" aria-label="Region" bind:value={region}>
          {#each REGION_CHOICES as [optLabel, value] (value)}
            <option {value}>{optLabel}</option>
          {/each}
        </select>
        <select class="analyze-select" name="min_games" aria-label="Minimum games for a report" bind:value={minGames}>
          {#each MIN_GAMES_CHOICES as value (value)}
            <option {value}>{value} games</option>
          {/each}
        </select>
        <Button type="submit" variant="outlined" size="sm" id="analyze-submit" class="analyze-submit" disabled={submitting}>
          {submitting ? 'Checking…' : 'Analyze'}
        </Button>
      </div>
    </div>
    {#if error}
      <div class="error" id="analyze-error">{error}</div>
    {/if}
  </form>
</Panel>
