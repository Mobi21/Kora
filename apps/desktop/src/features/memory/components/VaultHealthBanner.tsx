import { useNavigate } from 'react-router-dom';
import type { VaultState } from '@/lib/api/types';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Settings } from 'lucide-react';
import { cn } from '@/lib/utils';

interface VaultHealthBannerProps {
  vault: VaultState;
}

/**
 * Sage-bordered banner shown when vault.health !== 'ok'. Quietly nudges the
 * user to settings without shouting at them.
 */
export function VaultHealthBanner({ vault }: VaultHealthBannerProps): JSX.Element {
  const navigate = useNavigate();
  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-3 rounded-[var(--r-2)] border border-l-[3px] px-4 py-3',
        'border-[var(--border)] border-l-[var(--provenance-confirmed)]',
        'bg-[var(--surface-1)]',
      )}
    >
      <AlertTriangle
        aria-hidden
        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--warn)]"
        strokeWidth={1.5}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-[var(--fs-sm)] text-[var(--fg)]">
          Vault {vault.health}
        </p>
        <p className="text-[var(--fs-xs)] text-[var(--fg-muted)]">
          {vault.message || 'Configure your vault to keep memory in sync.'}
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={() => navigate('/settings#vault')}
        className="shrink-0"
        aria-label="Open vault settings"
      >
        <Settings className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        Open Settings
      </Button>
    </div>
  );
}
