import { ArrowRight, CheckCircle2, Loader2, RefreshCcw, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useFirstRunProbeQuery } from '../queries';
import { InstallInstructions } from './InstallInstructions';

interface StepDaemonCheckProps {
  onAdvance: () => void;
  onSkip: () => void;
}

export function StepDaemonCheck({ onAdvance, onSkip }: StepDaemonCheckProps): JSX.Element {
  const probe = useFirstRunProbeQuery();
  const data = probe.data;
  const isLoading = probe.isLoading || probe.isFetching;
  const ready = !!data?.ready;

  return (
    <section aria-labelledby="daemon-heading" className="space-y-5">
      <p id="daemon-heading" className="sr-only">
        Daemon and CLI check
      </p>

      {isLoading && !data ? (
        <ProbeRow
          tone="pending"
          icon={<Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          title="Looking for the Kora daemon…"
          body="We're checking your PATH and probing 127.0.0.1 for a running daemon."
        />
      ) : ready ? (
        <ProbeRow
          tone="ok"
          icon={<CheckCircle2 className="h-4 w-4" aria-hidden />}
          title="Daemon is responding."
          body={
            <span className="space-y-1.5 block">
              {data?.cliPath && (
                <span className="block">
                  <Label>cli</Label>
                  <Mono>{data.cliPath}</Mono>
                </span>
              )}
              {(data?.host || data?.port) && (
                <span className="block">
                  <Label>endpoint</Label>
                  <Mono>{`http://${data?.host ?? '127.0.0.1'}:${data?.port ?? '—'}`}</Mono>
                </span>
              )}
              {data?.version && (
                <span className="block">
                  <Label>version</Label>
                  <Mono>{data.version}</Mono>
                </span>
              )}
            </span>
          }
        />
      ) : (
        <div className="space-y-4">
          <ProbeRow
            tone="warn"
            icon={<Terminal className="h-4 w-4" aria-hidden />}
            title={data?.cliAvailable ? 'CLI installed but daemon is asleep.' : 'Kora isn\u2019t installed yet.'}
            body={
              data?.cliAvailable
                ? 'Run `kora` in a terminal to start the daemon, then click Try again.'
                : 'Install the kora_v2 Python daemon, then come back here.'
            }
          />

          {!data?.cliAvailable && <InstallInstructions />}

          <div className="space-y-1 text-[var(--fs-xs)] text-[var(--fg-subtle)]">
            <p>
              The Kora desktop app does not bundle Python. It always talks to a
              system-installed kora_v2 daemon over a local-only HTTP API.
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void probe.refetch()}
          disabled={isLoading}
          aria-label="Re-probe the daemon"
        >
          <RefreshCcw
            className={cn('h-4 w-4', isLoading && 'animate-spin')}
            strokeWidth={1.5}
            aria-hidden
          />
          Try again
        </Button>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onSkip}
            className={cn(
              'text-[var(--fs-sm)] text-[var(--fg-muted)]',
              'underline-offset-4 hover:underline',
              'focus-visible:outline-none focus-visible:ring-2',
              'focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2',
              'focus-visible:ring-offset-[var(--bg)] rounded-[var(--r-1)] px-1',
            )}
          >
            I&rsquo;ll do this later
          </button>
          <Button
            type="button"
            onClick={onAdvance}
            disabled={!ready}
            aria-label="Continue to token and memory step"
          >
            Continue
            <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden />
          </Button>
        </div>
      </div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <span className="mr-2 font-mono text-[var(--fs-2xs)] uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
      {children}
    </span>
  );
}

function Mono({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <span className="break-all font-mono text-[var(--fs-xs)] text-[var(--fg)]">
      {children}
    </span>
  );
}

function ProbeRow({
  tone,
  icon,
  title,
  body,
}: {
  tone: 'ok' | 'warn' | 'pending';
  icon: React.ReactNode;
  title: string;
  body: React.ReactNode;
}): JSX.Element {
  const toneCls =
    tone === 'ok'
      ? 'border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[var(--accent)]'
      : tone === 'warn'
        ? 'border-[var(--border-strong)] bg-[var(--surface-2)] text-[var(--warn)]'
        : 'border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-muted)]';
  return (
    <div
      className={cn(
        'rounded-[var(--r-2)] border px-4 py-3',
        'flex gap-3',
        toneCls,
      )}
    >
      <span
        aria-hidden
        className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center"
      >
        {icon}
      </span>
      <div className="space-y-1">
        <p className="text-[var(--fs-base)] font-medium text-[var(--fg)]">{title}</p>
        <div className="text-[var(--fs-sm)] leading-[var(--lh-narrative)] text-[var(--fg-muted)]">
          {body}
        </div>
      </div>
    </div>
  );
}
