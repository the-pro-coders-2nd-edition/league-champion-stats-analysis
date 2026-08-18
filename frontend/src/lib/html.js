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

// Single source of truth for "which metric icon (asset image vs iconify glyph) wins, and what
// class does it get" — shared by MetricLabel.svelte (real DOM) and the string builders below
// ({@html} consumers not yet converted to components; see RFC-001 step 4). icon_href always
// wins over iconify.
export function resolveMetricIcon(iconHref, iconify) {
  if (iconHref) return { kind: 'img', src: iconHref, className: 'metric-icon metric-icon--asset' };
  if (iconify) return { kind: 'iconify', icon: iconify, className: 'metric-icon' };
  return null;
}

function metricIconTagHtml(icon) {
  if (!icon) return '';
  if (icon.kind === 'img') {
    return `<img src="${icon.src}" alt="" class="${icon.className}" aria-hidden="true">`;
  }
  return `<iconify-icon icon="${icon.icon}" class="${icon.className}" aria-hidden="true"></iconify-icon>`;
}

// Mirrors the existing Jinja mover/table row partials, which call the `metric_label` macro
// with `row.iconify` as its positional `icon` (icon-key) argument rather than `iconify_id`
// (unlike `metric_card`, which passes it correctly). That icon-key lookup never resolves for
// real data (row.iconify already holds a resolved "prefix:name" id), so no iconify icon is
// ever rendered here in practice — only `icon_href` produces a visible icon. Replicated as-is:
// `row.iconify` is deliberately never passed to resolveMetricIcon.
function metricIconHtml(row) {
  return metricIconTagHtml(resolveMetricIcon(row.icon_href, ''));
}

// Row-object variant: label + icon both come from the row (form/rank-peer delta tables).
export function metricLabelFromRow(row) {
  return `<span class="metric-label">${metricIconHtml(row)}<span>${escapeHtml(row.label)}</span></span>`;
}

// Static variant: label/iconify-key are passed explicitly (table headers).
export function metricLabelWithIconify(label, iconify) {
  return `<span class="metric-label">${metricIconTagHtml(resolveMetricIcon('', iconify))}<span>${escapeHtml(label)}</span></span>`;
}
