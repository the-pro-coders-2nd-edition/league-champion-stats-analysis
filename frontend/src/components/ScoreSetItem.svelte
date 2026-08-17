<script>
  // tone ('strong'|'focus'|'solid') and verdict are per-instance report data
  // (Jinja tokens here, not literals) -- vocabulary enforced by the Python
  // producer, same reasoning as RecCardHead's badge.
  export let name;
  export let scoreLabel;
  export let scoreValue;
  export let value;
  export let tone;
  export let verdict;
  export let hint;
</script>

<div class="comp comp--{tone}" title="{hint}">
  <span class="comp-verdict">{verdict}</span>
  <div class="comp-head">
    <span class="comp-name">{name}</span>
    <span class="comp-score">{scoreLabel}</span>
  </div>
  <div class="comp-value">{value}</div>
  <div class="bar"><i style="width: {scoreValue}%"></i></div>
</div>

<style>
  :global(.comp) {
    position: relative;
    background: var(--panel-2); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 14px 12px;
  }
  :global(.comp--strong) {
    border-color: rgba(63, 182, 139, 0.45);
    background: linear-gradient(180deg, rgba(63, 182, 139, 0.12), var(--panel-2));
  }
  :global(.comp--focus) {
    border-color: rgba(224, 85, 99, 0.45);
    background: linear-gradient(180deg, rgba(224, 85, 99, 0.12), var(--panel-2));
  }
  :global(.comp--solid) {
    border-color: rgba(154, 160, 181, 0.40);
    background: linear-gradient(180deg, rgba(154, 160, 181, 0.10), var(--panel-2));
  }
  :global(.comp-verdict) {
    position: absolute; top: 10px; right: 10px;
    font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 999px;
    background: var(--panel); color: var(--muted); border: 1px solid var(--border);
  }
  :global(.comp--strong .comp-verdict) {
    color: var(--win); background: rgba(63, 182, 139, 0.14);
    border-color: rgba(63, 182, 139, 0.35);
  }
  :global(.comp--focus .comp-verdict) {
    color: var(--loss); background: rgba(224, 85, 99, 0.14);
    border-color: rgba(224, 85, 99, 0.35);
  }
  :global(.comp--solid .comp-verdict) {
    color: #b0b6c9; background: rgba(154, 160, 181, 0.12);
    border-color: rgba(154, 160, 181, 0.30);
  }
  :global(.comp-head) {
    display: flex; align-items: baseline; gap: 8px;
    padding-right: 78px;
  }
  :global(.comp-name) { font-size: 13px; font-weight: 600; color: var(--text); min-width: 0; }
  :global(.comp-score) { font-size: 13px; font-weight: 700; color: var(--accent); flex-shrink: 0; }
  :global(.comp-value) { font-size: 12px; color: var(--muted); line-height: 1.35; margin-top: 4px; }
  :global(.comp .bar) {
    height: 6px; background: rgba(0, 0, 0, 0.28); border-radius: 3px;
    margin-top: 8px; overflow: hidden; container-type: inline-size;
  }
  :global(.comp .bar i) {
    display: block; height: 100%;
    background-image: var(--score-fill-gradient);
    background-size: 100cqw 100%;
    background-repeat: no-repeat;
  }
</style>
