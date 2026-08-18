<script>
  export let data = {};
  export let accountKey = 'all';
  export let loading = false;
  export let error = '';
  export let onChange = () => {};

  $: members = data.members || [];
  $: selected = new Set(
    accountKey === 'all' ? members.map((m) => m.key) : accountKey.split('|')
  );

  function keyFromSelection(selectedSet) {
    const keys = members.filter((m) => selectedSet.has(m.key)).map((m) => m.key);
    if (!keys.length || keys.length === members.length) return 'all';
    return keys.slice().sort().join('|');
  }

  function toggle(member, checked) {
    const next = new Set(selected);
    if (checked) {
      next.add(member.key);
    } else if (next.size > 1) {
      next.delete(member.key);
    } else {
      // At least one account must stay selected.
      return;
    }
    onChange(keyFromSelection(next));
  }
</script>

{#if members.length > 1}
<div class="filter-group account-filter-group" id="account-filter-bar">
  <span class="game-window-label">Accounts</span>
  <div class="account-filter-list" id="account-filter-menu">
    {#each members as member}
      <label class="account-filter-chip{member.games ? '' : ' is-empty'}{selected.has(member.key) ? ' is-active' : ''}">
        {#if member.profile_icon}
          <img src={member.profile_icon} alt="" class="account-filter-icon" width="22" height="22">
        {:else}
          <span class="account-filter-icon account-filter-icon--empty" aria-hidden="true"></span>
        {/if}
        <span class="account-filter-name">
          {member.riot_id || member.label}<span class="account-filter-tag">#{member.tagline || ''}</span>
        </span>
        <input
          type="checkbox"
          class="account-filter-input"
          data-account={member.key}
          checked={selected.has(member.key)}
          disabled={!member.games}
          on:change={(e) => toggle(member, e.currentTarget.checked)}
        >
        <span class="account-filter-switch" aria-hidden="true"></span>
      </label>
    {/each}
  </div>
  {#if error || loading}
    <p class="account-filter-notice" id="account-filter-notice">
      {loading ? 'Crunching numbers for this selection…' : error}
    </p>
  {/if}
</div>
{/if}
