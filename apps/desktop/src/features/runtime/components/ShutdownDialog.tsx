import { useEffect, useState } from 'react';
import { Power } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useShutdownDaemon } from '../queries';

const CONFIRM_PHRASE = 'SHUTDOWN';

interface ShutdownDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShutdownComplete?: () => void;
}

export function ShutdownDialog({
  open,
  onOpenChange,
  onShutdownComplete,
}: ShutdownDialogProps): JSX.Element {
  const [phrase, setPhrase] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const shutdown = useShutdownDaemon();

  useEffect(() => {
    if (!open) {
      setPhrase('');
      setErrorMsg(null);
      shutdown.reset();
    }
  }, [open, shutdown]);

  const canConfirm = phrase === CONFIRM_PHRASE && !shutdown.isPending;

  const onConfirm = async () => {
    setErrorMsg(null);
    try {
      await shutdown.mutateAsync();
      onOpenChange(false);
      onShutdownComplete?.();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-2)] text-[var(--danger)]"
            >
              <Power className="h-5 w-5" strokeWidth={1.5} />
            </span>
            <DialogTitle>Stop the Kora daemon?</DialogTitle>
          </div>
          <DialogDescription>
            This will end the current session for this device. You can restart
            the daemon afterwards from the menu bar or by relaunching the app.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-2 space-y-2">
          <Label htmlFor="shutdown-confirm">
            Type{' '}
            <span className="font-mono text-[var(--fs-xs)] text-[var(--fg)]">
              {CONFIRM_PHRASE}
            </span>{' '}
            to confirm
          </Label>
          <Input
            id="shutdown-confirm"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            placeholder={CONFIRM_PHRASE}
            className="font-mono"
            aria-invalid={phrase.length > 0 && !canConfirm && !shutdown.isPending}
          />
          {errorMsg && (
            <p
              role="alert"
              className="text-[var(--fs-sm)] text-[var(--danger)]"
            >
              {errorMsg}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={shutdown.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={onConfirm}
            disabled={!canConfirm}
            aria-label="Confirm daemon shutdown"
          >
            {shutdown.isPending ? 'Stopping…' : 'Stop daemon'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
