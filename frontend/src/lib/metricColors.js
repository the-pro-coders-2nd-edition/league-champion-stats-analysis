/** JS mirror of src/league_stats/presentation/metric_colors.py (interpolate only). */

const LOSS_HEX = '#e05563';
const NEUTRAL_HEX = '#9aa8b1';
const MINT_HEX = '#7ed4c0';
const WIN_HEX = '#41b78c';
const JADE_HEX = '#2a9f7a';
const POS_MINT = (38 - 16) / (100 - 16);
const POS_TEAL = (68 - 16) / (100 - 16);

const STOPS = [
  [-1.0, LOSS_HEX],
  [0.0, NEUTRAL_HEX],
  [POS_MINT, MINT_HEX],
  [POS_TEAL, WIN_HEX],
  [1.0, JADE_HEX],
];

function clamp(value, low = -1, high = 1) {
  return Math.max(low, Math.min(high, value));
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function hexToRgb(hex) {
  const value = hex.replace('#', '');
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function rgbToHex(r, g, b) {
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

/** Map a normalized score in [-1, 1] along the shared metric ramp. */
export function interpolateMetricColor(score) {
  const s = clamp(score);
  for (let index = 1; index < STOPS.length; index += 1) {
    const [leftScore, leftHex] = STOPS[index - 1];
    const [rightScore, rightHex] = STOPS[index];
    if (s <= rightScore || index === STOPS.length - 1) {
      const span = rightScore - leftScore;
      const t = span === 0 ? 0 : (s - leftScore) / span;
      const [r1, g1, b1] = hexToRgb(leftHex);
      const [r2, g2, b2] = hexToRgb(rightHex);
      return rgbToHex(
        Math.round(lerp(r1, r2, t)),
        Math.round(lerp(g1, g2, t)),
        Math.round(lerp(b1, b2, t)),
      );
    }
  }
  return JADE_HEX;
}

/** Flat fill color for a 0–100 game-review score bar (50 = neutral). */
export function gameReviewScoreColor(pct) {
  const width = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  return interpolateMetricColor((width - 50) / 50);
}

/** Inline style for a score bar fill: width + flat ramp color. */
export function gameReviewBarStyle(pct) {
  const width = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  return `width:${width}%;background:${gameReviewScoreColor(width)}`;
}
