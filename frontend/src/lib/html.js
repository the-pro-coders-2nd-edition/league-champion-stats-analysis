export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Icon + escaped-name pair with a separate name span (used in data-table cells).
export function iconCellHtml(name, iconHref) {
  if (!iconHref) return escapeHtml(name);
  return `<span class="icon-cell"><img src="${iconHref}" alt="" class="game-icon game-icon--sm"><span>${escapeHtml(name)}</span></span>`;
}

// Icon-only variant with the name as a title tooltip instead of a visible span
// (used for game-review rails/pins where space is tight).
export function soloIconCellHtml(name, iconHref) {
  const title = escapeHtml(name || '');
  if (!iconHref) return '—';
  return `<span class="icon-cell icon-cell--solo" title="${title}"><img src="${iconHref}" alt="${title}" class="game-icon game-icon--sm"></span>`;
}

// Mirrors the existing Jinja mover/table row partials, which call the `metric_label` macro
// with `row.iconify` as its positional `icon` (icon-key) argument rather than `iconify_id`
// (unlike `metric_card`, which passes it correctly). That icon-key lookup never resolves for
// real data (row.iconify already holds a resolved "prefix:name" id), so no iconify icon is
// ever rendered here in practice — only `icon_href` produces a visible icon. Replicated as-is.
function metricIconHtml(row) {
  if (!row.icon_href) return '';
  const tone = row.icon_tone || 'muted';
  return `<img src="${row.icon_href}" alt="" class="metric-icon metric-icon--asset metric-icon--${tone}" aria-hidden="true">`;
}

// Row-object variant: label + icon both come from the row (form/rank-peer delta tables).
export function metricLabelFromRow(row) {
  return `<span class="metric-label">${metricIconHtml(row)}<span>${escapeHtml(row.label)}</span></span>`;
}

// Static variant: label/iconify-key/tone are passed explicitly (table headers).
export function metricLabelWithIconify(label, iconify, tone) {
  return `<span class="metric-label"><iconify-icon icon="${iconify}" class="metric-icon metric-icon--${tone}" aria-hidden="true"></iconify-icon><span>${escapeHtml(label)}</span></span>`;
}
