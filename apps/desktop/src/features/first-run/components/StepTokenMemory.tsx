import { useState } from 'react';
import { Check, FolderOpen, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { useOpenDirectoryDialogMutation } from '../queries';

interface StepTokenMemoryProps {
  onFinish: (memoryRoot: string | null) => void;
}

const DEFAULT_MEMORY_HINT = '~/Documents/Kora/_KoraMemory';

export function StepTokenMemory({ onFinish }: StepTokenMemoryProps): JSX.Element {
  const [memoryRoot, setMemoryRoot] = useState<string>('');
  const openDirectory = useOpenDirectoryDialogMutation();
  const bridgeAvailable =
    typeof window !== 'undefined' && !!window.kora?.openDirectoryDialog;

  async function pickFolder(): Promise<void> {
    if (!bridgeAvailable) return;
    try {
      const picked = await openDirectory.mutateAsync({
        title: 'Choose Kora memory root',
        defaultPath: memoryRoot || undefined,
      });
      if (picked) setMemoryRoot(picked);
    } catch {
      /* surfaced via mutation.error */
    }
  }

  function handleFinish(): void {
    onFinish(memoryRoot.trim() || null);
  }

  return (
    <section aria-labelledby="token-heading" className="space-y-6">
      <p id="token-heading" className="sr-only">
        Token and memory root setup
      </p>

      <article className="space-y-2">
        <h2 className="font-narrative text-[var(--fs-xl)] tracking-[var(--track-tight)] text-[var(--fg)]">
          Where Kora keeps things.
        </h2>
        <ul className="space-y-2 text-[var(--fs-sm)] leading-[var(--lh-narrative)] text-[var(--fg-muted)]">
          <li className="flex flex-col gap-0.5">
            <span className="text-[var(--fg)]">Auth token</span>
            <span>
              The daemon writes a local-only bearer token to{' '}
              <Mono>data/api_token</Mono>. The desktop app reads it via the
              Electron main process; it never leaves your machine.
            </span>
          </li>
          <li className="flex flex-col gap-0.5">
            <span className="text-[var(--fg)]">Memory root</span>
            <span>
              Kora&rsquo;s memory is a folder of Markdown + JSON. Choose any
              location. We default to <Mono>{DEFAULT_MEMORY_HINT}</Mono>.
            </span>
          </li>
        </ul>
      </article>

      <div className="space-y-2">
        <Label htmlFor="memory-root">Memory root path</Label>
        <div className="flex gap-2">
          <Input
            id="memory-root"
            value={memoryRoot}
            onChange={(e) => setMemoryRoot(e.target.value)}
            placeholder={DEFAULT_MEMORY_HINT}
            spellCheck={false}
            autoCorrect="off"
            autoCapitalize="off"
            className="font-mono text-[var(--fs-xs)]"
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => void pickFolder()}
            disabled={!bridgeAvailable || openDirectory.isPending}
            aria-label="Choose memory root folder"
          >
            <FolderOpen className="h-4 w-4" strokeWidth={1.5} aria-hidden />
            Browse…
          </Button>
        </div>
        {!bridgeAvailable && (
          <p className="text-[var(--fs-xs)] text-[var(--fg-subtle)]">
            File picker is only available in the packaged app. You can type the
            path manually in the meantime.
          </p>
        )}
        {openDirectory.isError && (
          <p className="text-[var(--fs-xs)] text-[var(--danger)]">
            Couldn&rsquo;t open the picker.{' '}
            <span className="font-mono">
              {openDirectory.error instanceof Error
                ? openDirectory.error.message
                : 'Unknown error.'}
            </span>
          </p>
        )}
      </div>

      <p
        className={cn(
          'flex items-start gap-2 rounded-[var(--r-2)]',
          'border border-[var(--border)] bg-[var(--surface-2)]',
          'px-4 py-3 text-[var(--fs-sm)]',
          'leading-[var(--lh-narrative)] text-[var(--fg-muted)]',
        )}
      >
        <Sparkles
          className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]"
          strokeWidth={1.5}
          aria-hidden
        />
        <span>
          You can change all of this later from{' '}
          <span className="text-[var(--fg)]">Settings → Memory</span>. The
          wizard is optional — leaving the path blank keeps the daemon&rsquo;s
          current default.
        </span>
      </p>

      <div className="flex justify-end pt-1">
        <Button
          type="button"
          onClick={handleFinish}
          aria-label="Finish first-run wizard and open Today"
        >
          <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          Finish setup
        </Button>
      </div>
    </section>
  );
}

function Mono({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <span className="rounded-[var(--r-1)] border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-px font-mono text-[var(--fs-xs)] text-[var(--fg)]">
      {children}
    </span>
  );
}
