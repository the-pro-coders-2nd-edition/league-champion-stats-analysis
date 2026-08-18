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
<div class="nav-accounts" id="account-filter-bar">
  <div class="nav-accounts-label">Accounts</div>
  <div class="nav-accounts-list" id="account-filter-menu">
    {#each members as member}
      <label class="account-toggle-row{member.games ? '' : ' is-empty'}">
        <span class="account-toggle-head">
          <span class="account-toggle-name">
            {member.riot_id || member.label}
            <span class="account-toggle-tag">#{member.tagline || ''}</span>
          </span>
          {#if member.solo_rank_label}
            <span class="account-toggle-rank">
              {#if member.solo_rank_icon}
                <img src={member.solo_rank_icon} alt="" width="16" height="16">
              {/if}
              {member.solo_rank_label}
            </span>
          {:else}
            <span class="account-toggle-rank account-toggle-rank--none">Unranked</span>
          {/if}
        </span>
        <span class="account-toggle-foot">
          {#if member.profile_icon}
            <img src={member.profile_icon} alt="" class="account-toggle-icon" width="28" height="28">
          {:else}
            <span class="account-toggle-icon account-toggle-icon--empty" aria-hidden="true"></span>
          {/if}
          <span class="account-toggle-games">{member.games || 0} game{member.games === 1 ? '' : 's'}</span>
          <input
            type="checkbox"
            class="account-toggle-input"
            data-account={member.key}
            checked={selected.has(member.key)}
            disabled={!member.games}
            on:change={(e) => toggle(member, e.currentTarget.checked)}
          >
          <span class="account-toggle-switch" aria-hidden="true"></span>
        </span>
      </label>
    {/each}
  </div>
  <p class="account-filter-notice" id="account-filter-notice" hidden={!error && !loading}>
    {loading ? 'Crunching numbers for this selection…' : error}
  </p>
</div>
{/if}
