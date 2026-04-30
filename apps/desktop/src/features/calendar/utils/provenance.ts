import type { ProvenanceKind } from '@/components/ui/provenance-dot';

const KNOWN: ProvenanceKind[] = [
  'local',
  'workspace',
  'inferred',
  'confirmed',
  'repair',
];

/** Pick the dominant ProvenanceKind from a free-form list of source tags. */
export function pickProvenance(prov: readonly string[] | undefined): ProvenanceKind {
  if (!prov || prov.length === 0) return 'local';
  for (const tag of prov) {
    const lower = tag.toLowerCase();
    for (const k of KNOWN) {
      if (lower.includes(k)) return k;
    }
  }
  return 'local';
}

/** Return every ProvenanceKind referenced in `prov` (de-duplicated, in order). */
export function classifyProvenance(prov: readonly string[] = []): ProvenanceKind[] {
  const seen = new Set<ProvenanceKind>();
  for (const t of prov) {
    const lower = t.toLowerCase();
    for (const k of KNOWN) {
      if (lower.includes(k)) seen.add(k);
    }
  }
  if (seen.size === 0) seen.add('local');
  return Array.from(seen);
}

export const PROV_VARS: Record<ProvenanceKind, string> = {
  local: 'var(--provenance-local)',
  workspace: 'var(--provenance-workspace)',
  inferred: 'var(--provenance-inferred)',
  confirmed: 'var(--provenance-confirmed)',
  repair: 'var(--provenance-repair)',
};
