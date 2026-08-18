export function pct(value) {
  const num = Number(value);
  if (value == null || !Number.isFinite(num)) return '—';
  return Math.round(num * 100) + '%';
}

// Mirrors Python's str(float) for legacy Jinja-template parity (e.g. "1200.0", not "1200").
export function pyFloatStr(value) {
  const num = Number(value);
  return Number.isInteger(num) ? `${num}.0` : String(num);
}

export function formatGameTime(minutes) {
  const totalSec = Math.max(0, Math.round(Number(minutes) * 60));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, '0')}`;
}
