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

export function winratePct(build) {
  if (!build || build.winrate == null) return null;
  const num = Number(build.winrate);
  if (!Number.isFinite(num)) return null;
  return Math.round(num * 100);
}

export function formatUpdated(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  try {
    return date.toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return date.toISOString().slice(0, 16).replace('T', ' ');
  }
}

export function scoreTone(scoreColor) {
  if (scoreColor === 'var(--tone-good-fg)') return 'good';
  if (scoreColor === 'var(--tone-bad-fg)') return 'bad';
  if (scoreColor === 'var(--tone-solid-fg)') return 'solid';
  if (scoreColor === 'var(--tone-warn-fg)') return 'warn';
  return 'flat';
}

export function formatGameTime(minutes) {
  const totalSec = Math.max(0, Math.round(Number(minutes) * 60));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, '0')}`;
}

/** Rank column text for AccountsPanel (division only when LP is split out). */
export function rankDivisionText(member, { queue = 'solo', splitLp = false } = {}) {
  const division = member[`${queue}_rank_division`];
  if (division) return division;
  const label = member[`${queue}_rank_label`];
  if (!label) return queue === 'solo' ? 'Unranked' : '';
  if (!splitLp) return label;
  const stripped = label.replace(/\s*-\s*\d+LP$/i, '').trim();
  return stripped || label;
}

export function memberRankQueues(member) {
  const queues = ['solo'];
  if (hasQueueRank(member, 'flex')) {
    queues.push('flex');
  }
  return queues;
}

export function queueRankLabel(queue) {
  return queue === 'flex' ? 'Flex' : 'Solo';
}

export function hasQueueRank(member, queue) {
  return Boolean(
    member[`${queue}_rank_icon`]
    || member[`${queue}_rank_division`]
    || member[`${queue}_rank_label`]
    || member[`${queue}_tier`]
    || member[`${queue}_lp`] != null
  );
}
