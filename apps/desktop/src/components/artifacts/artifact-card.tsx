import {
  Box,
  Calendar as CalendarIcon,
  CheckCircle2,
  ClipboardList,
  Cog,
  Compass,
  FileText,
  HeartPulse,
  KeyRound,
  ListChecks,
  Pill as PillIcon,
  ScrollText,
  Sparkles,
  Wrench,
} from 'lucide-react';
import type { ArtifactKind, KoraArtifact } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { ArtifactRenderer } from './artifact-renderer';
import { ProvenanceDot } from '@/components/ui/provenance-dot';
import { formatTime } from '@/lib/dates';

const KIND_ICON: Record<ArtifactKind, typeof Sparkles> = {
  today_plan: Sparkles,
  repair_preview: Wrench,
  calendar_slice: CalendarIcon,
  calendar_edit_preview: CalendarIcon,
  medication_status: PillIcon,
  medication_log_preview: PillIcon,
  routine_status: ListChecks,
  vault_memory: ClipboardList,
  context_pack: ScrollText,
  future_bridge: Compass,
  autonomous_progress: Compass,
  settings_control: Cog,
  permission_prompt: KeyRound,
  doctor_report: HeartPulse,
};

interface ArtifactCardProps {
  artifact: KoraArtifact;
  className?: string;
}

export function ArtifactCard({ artifact, className }: ArtifactCardProps): JSX.Element {
  const Icon = KIND_ICON[artifact.kind] ?? Box;
  const provenance =
    artifact.kind === 'repair_preview' ? 'repair'
      : artifact.kind === 'vault_memory' ? 'confirmed'
      : 'local';

  return (
    <article
      className={cn(
        'overflow-hidden rounded-[var(--r-2)] border border-[var(--border)]',
        'bg-[var(--surface-1)] shadow-[var(--shadow-1)]',
        className,
      )}
    >
      <header className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2">
        <Icon className="h-4 w-4 text-[var(--fg-muted)]" strokeWidth={1.5} />
        <h3 className="flex-1 truncate text-[var(--fs-sm)] font-medium text-[var(--fg)]">
          {artifact.title}
        </h3>
        <ProvenanceDot kind={provenance} />
        <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-muted)] num-tabular">
          {formatTime(artifact.created_at)}
        </span>
      </header>
      <div className="px-3 py-3 text-[var(--fs-sm)] text-[var(--fg)]">
        <ArtifactRenderer artifact={artifact} />
      </div>
      {artifact.summary && (
        <footer className="flex items-center gap-2 border-t border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[var(--fs-xs)] text-[var(--fg-muted)]">
          <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} />
          <span className="flex-1 truncate">{artifact.summary}</span>
        </footer>
      )}
      <span className="sr-only">
        <FileText aria-hidden /> Artifact identifier {artifact.id}
      </span>
    </article>
  );
}
