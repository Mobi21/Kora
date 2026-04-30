import type { KoraArtifact } from '@/lib/api/types';

interface ArtifactRendererProps {
  artifact: KoraArtifact;
}

export function ArtifactRenderer({ artifact }: ArtifactRendererProps): JSX.Element {
  return (
    <div className="space-y-1.5 text-[var(--fs-sm)] text-[var(--fg-muted)]">
      <p>
        Preview unavailable for <span className="font-mono text-[var(--fg)]">{artifact.kind}</span>{' '}
        artifacts yet.
      </p>
      <p className="text-[var(--fg-subtle)]">
        Kind-specific renderers will land in the corresponding feature subagent.
      </p>
    </div>
  );
}
