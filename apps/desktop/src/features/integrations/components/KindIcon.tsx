import { Briefcase, Globe, Library, Server, Terminal, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { IntegrationKind } from '../queries';

interface KindMeta {
  icon: LucideIcon;
  // Theme-variable color used for the icon glyph and a soft background tint
  // around it. Keeps a per-kind identity without departing from the palette.
  color: string;
}

const META: Record<IntegrationKind, KindMeta> = {
  workspace: { icon: Briefcase, color: 'var(--provenance-workspace)' },
  vault: { icon: Library, color: 'var(--provenance-confirmed)' },
  browser: { icon: Globe, color: 'var(--provenance-inferred)' },
  claude_code: { icon: Terminal, color: 'var(--provenance-repair)' },
  mcp: { icon: Server, color: 'var(--provenance-local)' },
};

interface KindIconProps {
  kind: IntegrationKind;
  size?: number;
  className?: string;
}

export function KindIcon({ kind, size = 32, className }: KindIconProps): JSX.Element {
  const { icon: Icon, color } = META[kind];
  const inner = Math.round(size * 0.5);
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-[var(--r-2)]',
        'border border-[var(--border)]',
        className,
      )}
      style={{
        width: size,
        height: size,
        // Soft kind-tinted surface with a fallback to surface-2 if color-mix
        // is unsupported (older Electron). The border keeps it grounded.
        background: `color-mix(in oklch, ${color} 12%, var(--surface-2))`,
        color,
      }}
    >
      <Icon width={inner} height={inner} strokeWidth={1.5} />
    </span>
  );
}

export function getKindColor(kind: IntegrationKind): string {
  return META[kind].color;
}
