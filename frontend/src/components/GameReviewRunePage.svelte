<script>
  import IconCellSolo from './IconCellSolo.svelte';
  import GameReviewIconRow from './GameReviewIconRow.svelte';
  import GameReviewRuneDuo from './GameReviewRuneDuo.svelte';

  export let build = {};

  $: hasFull = !!((build.primary_runes || []).length || (build.secondary_runes || []).length || (build.shards || []).length);
</script>

{#if !hasFull}
  <GameReviewRuneDuo {build} />
{:else}
  <div class="game-review-rune-page">
    <div class="game-review-rune-tree">
      <div class="game-review-rune-tree-head">
        <IconCellSolo name={build.primary_tree || 'Primary'} icon={build.primary_tree_icon} />
        <span class="game-review-rune-tree-label">Primary</span>
      </div>
      <div class="game-review-rune-row game-review-rune-row--keystone">
        <IconCellSolo name={build.keystone || 'Keystone'} icon={build.keystone_icon} />
      </div>
      <div class="game-review-rune-row">
        <GameReviewIconRow names={build.primary_runes} icons={build.primary_rune_icons} />
      </div>
    </div>
    <div class="game-review-rune-tree">
      <div class="game-review-rune-tree-head">
        <IconCellSolo name={build.secondary_tree || 'Secondary'} icon={build.secondary_tree_icon} />
        <span class="game-review-rune-tree-label">Secondary</span>
      </div>
      <div class="game-review-rune-row">
        <GameReviewIconRow names={build.secondary_runes} icons={build.secondary_rune_icons} />
      </div>
    </div>
    {#if (build.shards || []).length}
      <div class="game-review-rune-shards">
        <span class="game-review-rune-tree-label">Shards</span>
        <div class="game-review-rune-row">
          <GameReviewIconRow names={build.shards} icons={build.shard_icons} />
        </div>
      </div>
    {/if}
  </div>
{/if}
