import { formatRelative } from '@/lib/dates';

export type Certainty = 'confirmed' | 'guess' | 'correction' | 'stale' | 'unknown';

export interface CertaintyVisual {
  color: string;
  label: string;
}

export const CERTAINTY_VISUAL: Record<Certainty, CertaintyVisual> = {
  confirmed: { color: 'var(--provenance-confirmed)', label: 'Confirmed' },
  correction: { color: 'var(--provenance-repair)', label: 'Correction' },
  guess: { color: 'var(--provenance-inferred)', label: 'Guess' },
  stale: { color: 'var(--warn)', label: 'Stale' },
  unknown: { color: 'var(--fg-subtle)', label: 'Unknown' },
};

export function certaintyVisual(c: string | null | undefined): CertaintyVisual {
  if (c && c in CERTAINTY_VISUAL) return CERTAINTY_VISUAL[c as Certainty];
  return CERTAINTY_VISUAL.unknown;
}

/**
 * Format a vault path for display. If too long, keep the last two segments
 * and truncate the middle so the rightmost (most-meaningful) part is visible.
 */
export function truncateMiddle(path: string, max = 48): string {
  if (path.length <= max) return path;
  const tail = path.slice(-Math.floor(max * 0.6));
  const head = path.slice(0, Math.max(4, max - tail.length - 1));
  return `${head}…${tail}`;
}

/** Returns a relative time string ("3 hours ago"), or "—" if absent. */
export function formatRelativeOr(input: string | null | undefined, fallback = '—'): string {
  if (!input) return fallback;
  try {
    return formatRelative(input);
  } catch {
    return fallback;
  }
}

/** Trim a snippet to a soft cap so 2-line truncation stays consistent. */
export function softTrim(snippet: string, maxChars = 220): string {
  if (snippet.length <= maxChars) return snippet;
  return `${snippet.slice(0, maxChars - 1).trimEnd()}…`;
}
