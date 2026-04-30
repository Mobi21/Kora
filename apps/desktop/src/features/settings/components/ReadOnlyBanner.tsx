import { FileText } from 'lucide-react';

/**
 * Banner shown atop every read-only section. Explains that editing
 * happens in `~/.kora/settings.toml` and a restart is required.
 */
export function ReadOnlyBanner(): JSX.Element {
  return (
    <div
      role="note"
      className={
        'flex items-start gap-3 rounded-[var(--r-2)] border border-[var(--border)] ' +
        'bg-[var(--surface-2)] px-3 py-2.5 text-[var(--fs-sm)] text-[var(--fg-muted)]'
      }
    >
      <FileText
        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--fg-subtle)]"
        strokeWidth={1.5}
        aria-hidden
      />
      <p>
        These settings are read-only in the desktop app for now. Edit them in{' '}
        <code className="font-mono text-[var(--fs-xs)] text-[var(--fg)]">
          ~/.kora/settings.toml
        </code>{' '}
        and restart the daemon.
      </p>
    </div>
  );
}
