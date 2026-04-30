import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

const INSTALL_SCRIPT = `git clone https://github.com/your-org/kora.git ~/kora
cd ~/kora
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
kora doctor`;

interface InstallInstructionsProps {
  className?: string;
}

/**
 * Mono-styled command block with copy-to-clipboard. We don't pretend to
 * detect package managers — this is the canonical local install path for
 * the kora_v2 daemon and the same instructions appear in PACKAGING.md.
 */
export function InstallInstructions({ className }: InstallInstructionsProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(INSTALL_SCRIPT);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard denied — surface no error; user can select manually */
    }
  }

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-[var(--r-2)]',
        'border border-[var(--border)] bg-[var(--surface-2)]',
        className,
      )}
    >
      <pre
        aria-label="Installation commands"
        className={cn(
          'm-0 overflow-x-auto px-4 py-3.5',
          'font-mono text-[var(--fs-xs)] leading-[1.6] text-[var(--fg)]',
        )}
      >
        <code>{INSTALL_SCRIPT}</code>
      </pre>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => void handleCopy()}
        aria-label={copied ? 'Copied' : 'Copy install commands'}
        className="absolute right-2 top-2 h-7 px-2"
      >
        {copied ? (
          <>
            <Check className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            <span className="text-[var(--fs-xs)]">Copied</span>
          </>
        ) : (
          <>
            <Copy className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
            <span className="text-[var(--fs-xs)]">Copy</span>
          </>
        )}
      </Button>
    </div>
  );
}
