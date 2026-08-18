<script>
  import IconCellSolo from './IconCellSolo.svelte';

  export let build = {};
</script>

<div class="game-review-runes">
  <IconCellSolo name={build.keystone || 'Keystone'} icon={build.keystone_icon} />
  <span class="game-review-rune-sep" aria-hidden="true">+</span>
  <IconCellSolo name={build.secondary_tree || 'Secondary'} icon={build.secondary_tree_icon} />
</div>
