// Shared formatting for Game Review metric values (Overview rows, Career goal
// values in GameReview and RecapModal) so a metric reads the same everywhere.
import { pct } from './format.js';

export function formatMetricValue(value) {
  if (value === null || value === undefined) return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (Math.abs(num - Math.round(num)) < 1e-9) return String(Math.round(num));
  const rounded1 = Math.round(num * 10) / 10;
  if (Math.abs(num - rounded1) < 1e-9) return rounded1.toFixed(1);
  return (Math.round(num * 100) / 100).toFixed(2);
}

export function isShareOrParticipationMetric(name) {
  const key = String(name || '').toLowerCase();
  return key.indexOf('share') !== -1 || key.indexOf('participation') !== -1 ||
    key.slice(-5) === '_rate' || key === 'objectives_present_rate';
}

export function isGoldDiffMetric(name) {
  const key = String(name || '').toLowerCase();
  return key === 'gd10' || key === 'gd15' || key.indexOf('gold_diff') !== -1 || key.indexOf('gold diff') !== -1;
}

export function formatGameReviewMetricValue(metric, value) {
  if (value === null || value === undefined) return '—';
  if (isShareOrParticipationMetric(metric)) return pct(value);
  if (isGoldDiffMetric(metric)) {
    const gold = Math.round(Number(value));
    if (!Number.isFinite(gold)) return String(value);
    return (gold > 0 ? '+' : '') + gold;
  }
  return formatMetricValue(value);
}
