import { AlertTriangle, Check, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface StickySaveBarProps {
  visible: boolean;
  saving?: boolean;
  error?: string | null;
  onSave: () => void;
  onDiscard: () => void;
}

/**
 * Sticky bar at the bottom of a writable section. Reveals when there are
 * pending edits in scope.
 */
export function StickySaveBar({
  visible,
  saving,
  error,
  onSave,
  onDiscard,
}: StickySaveBarProps): JSX.Element | null {
  if (!visible) return null;
  return (
    <div
      role="region"
      aria-label="Unsaved changes"
      className={cn(
        'sticky bottom-0 z-10 -mx-6 mt-4 flex items-center gap-3 border-t border-[var(--border)]',
        'bg-[color-mix(in_oklch,var(--surface-1)_92%,transparent)] px-6 py-3 backdrop-blur-md',
      )}
    >
      {error ? (
        <div
          role="alert"
          className="flex min-w-0 items-center gap-2 text-[var(--fs-xs)] text-[var(--danger)]"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
          <span className="truncate">{error}</span>
        </div>
      ) : (
        <p className="flex items-center gap-1.5 text-[var(--fs-xs)] text-[var(--fg-muted)]">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--accent)]"
          />
          Unsaved changes
        </p>
      )}
      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={onDiscard}
          disabled={saving}
          aria-label="Discard changes"
        >
          Discard
        </Button>
        <Button
          variant="default"
          size="sm"
          onClick={onSave}
          disabled={saving}
          aria-label="Save changes"
        >
          {saving ? (
            <>
              <Loader2
                className="h-3.5 w-3.5 animate-spin"
                strokeWidth={1.5}
                aria-hidden
              />
              Saving
            </>
          ) : (
            <>
              <Check className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
              Save
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
