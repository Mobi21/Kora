import { useState } from 'react';
import { Copy, FileText, RefreshCcw } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { useDaemonLogsQuery } from '../queries';

const LINES = 80;
const BRIDGE_AVAILABLE =
  typeof window !== 'undefined' && !!window.kora?.daemon?.logs;

function LogsContent(): JSX.Element {
  const [copied, setCopied] = useState(false);
  const logs = useDaemonLogsQuery(LINES, true);

  const onCopy = async () => {
    if (!logs.data || logs.data.length === 0) return;
    try {
      await navigator.clipboard.writeText(logs.data.join('\n'));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <header className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2">
        <div className="flex items-center gap-2">
          <FileText
            aria-hidden
            className="h-4 w-4 text-[var(--fg-muted)]"
            strokeWidth={1.5}
          />
          <span className="text-[var(--fs-sm)] text-[var(--fg)]">
            Daemon logs
          </span>
          <span className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular">
            tail {LINES}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => logs.refetch()}
            aria-label="Refresh logs"
            disabled={!BRIDGE_AVAILABLE || logs.isFetching}
          >
            <RefreshCcw
              className={cn(
                'h-3.5 w-3.5',
                logs.isFetching && 'opacity-60',
              )}
              strokeWidth={1.5}
            />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCopy}
            disabled={!logs.data || logs.data.length === 0}
            aria-label="Copy logs to clipboard"
          >
            <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
      </header>

      <div className="max-h-[60vh] overflow-auto">
        {!BRIDGE_AVAILABLE ? (
          <div className="px-4 py-6 text-[var(--fs-sm)] text-[var(--fg-muted)]">
            Log tailing isn't available outside the desktop shell. Run Kora
            from the Electron app to view daemon logs here.
          </div>
        ) : logs.isLoading ? (
          <div className="space-y-1.5 px-3 py-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-3 w-full" />
            ))}
          </div>
        ) : logs.isError ? (
          <div className="px-4 py-6 text-[var(--fs-sm)] text-[var(--danger)]">
            Couldn't read logs: {logs.error.message}
          </div>
        ) : !logs.data || logs.data.length === 0 ? (
          <div className="px-4 py-6 text-[var(--fs-sm)] text-[var(--fg-muted)]">
            No log lines yet. The daemon may still be starting.
          </div>
        ) : (
          <pre
            className={cn(
              'whitespace-pre px-3 py-3 font-mono text-[var(--fs-2xs)]',
              'leading-snug text-[var(--fg)]',
            )}
          >
            {logs.data.join('\n')}
          </pre>
        )}
      </div>
    </>
  );
}

export function LogsDrawer(): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="View daemon logs">
          <FileText className="h-4 w-4" strokeWidth={1.5} />
          Logs
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(640px,92vw)] p-0"
      >
        {open && <LogsContent />}
      </PopoverContent>
    </Popover>
  );
}
