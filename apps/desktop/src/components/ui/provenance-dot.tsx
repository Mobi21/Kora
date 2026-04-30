import { cn } from '@/lib/utils';
import type { HTMLAttributes } from 'react';

export type ProvenanceKind = 'local' | 'workspace' | 'inferred' | 'confirmed' | 'repair';

const COLOR: Record<ProvenanceKind, string> = {
  local: 'var(--provenance-local)',
  workspace: 'var(--provenance-workspace)',
  inferred: 'var(--provenance-inferred)',
  confirmed: 'var(--provenance-confirmed)',
  repair: 'var(--provenance-repair)',
};

interface ProvenanceDotProps extends HTMLAttributes<HTMLSpanElement> {
  kind: ProvenanceKind;
  size?: number;
}

export function ProvenanceDot({
  kind,
  size = 6,
  className,
  ...props
}: ProvenanceDotProps): JSX.Element {
  return (
    <span
      aria-label={`${kind} provenance`}
      className={cn('inline-block rounded-full align-middle', className)}
      style={{ width: size, height: size, background: COLOR[kind] }}
      {...props}
    />
  );
}
