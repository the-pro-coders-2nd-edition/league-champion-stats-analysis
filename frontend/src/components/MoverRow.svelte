<script>
  const TONES = ['improved', 'regressed'];

  // gapColor (#rrggbb|'') is per-instance report data (a Jinja token here,
  // not a literal) -- vocabulary enforced by interpolate_metric_color() in
  // view_models.py, same reasoning as MetricValue's valueColor.
  export let tone;
  export let label;
  export let values;
  export let gap;
  export let gapColor = '';

  if (!TONES.includes(tone)) throw new Error(`MoverRow: invalid tone "${tone}"`);
</script>

<div class="form-mover form-mover--{tone}">
  <span class="form-mover-label">{label}</span>
  <span class="form-mover-values">{values}</span>
  <span class="form-mover-gap" style="color: {gapColor}">{gap}</span>
</div>

<style>
  :global(.form-mover) {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) auto;
    gap: 8px 12px;
    align-items: center;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    border-left-width: 3px;
    background: var(--panel-2);
  }
  :global(.form-mover--improved) {
    border-left-color: var(--win);
    background: rgba(63, 182, 139, 0.06);
  }
  :global(.form-mover--regressed) {
    border-left-color: var(--loss);
    background: rgba(224, 85, 99, 0.06);
  }
  :global(.form-mover-label) { font-size: 13px; font-weight: 600; min-width: 0; }
  :global(.form-mover-values) { font-size: 12px; color: var(--muted); }
  :global(.form-mover-gap) { font-size: 14px; font-weight: 700; white-space: nowrap; }
</style>
