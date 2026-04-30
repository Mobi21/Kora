import { ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface StepWelcomeProps {
  onContinue: () => void;
}

const PREVIEW_STEPS = [
  {
    n: 1,
    title: 'Find the daemon',
    body: 'We check whether the local Kora CLI is installed and answering on 127.0.0.1.',
  },
  {
    n: 2,
    title: 'Confirm the token',
    body: 'A local-only bearer token authorizes the desktop app. Stored at data/api_token.',
  },
  {
    n: 3,
    title: 'Choose memory root',
    body: 'Pick where Kora keeps its filesystem-canonical memory store. You can change it later.',
  },
] as const;

export function StepWelcome({ onContinue }: StepWelcomeProps): JSX.Element {
  return (
    <section aria-labelledby="welcome-heading" className="space-y-6">
      <p
        id="welcome-heading"
        className="sr-only"
      >
        Welcome to Kora — first-run wizard
      </p>

      <ol className={cn('flex flex-col gap-4')}>
        {PREVIEW_STEPS.map((step) => (
          <li key={step.n} className="flex gap-3">
            <span
              aria-hidden
              className={cn(
                'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center',
                'rounded-full border border-[var(--border)] bg-[var(--surface-2)]',
                'font-mono text-[var(--fs-xs)] num-tabular text-[var(--fg-muted)]',
              )}
            >
              {step.n}
            </span>
            <div className="space-y-0.5">
              <p className="text-[var(--fs-base)] font-medium text-[var(--fg)]">
                {step.title}
              </p>
              <p className="text-[var(--fs-sm)] leading-[var(--lh-narrative)] text-[var(--fg-muted)]">
                {step.body}
              </p>
            </div>
          </li>
        ))}
      </ol>

      <p
        className={cn(
          'rounded-[var(--r-2)] border border-[var(--border)]',
          'bg-[var(--surface-2)] px-4 py-3',
          'text-[var(--fs-sm)] leading-[var(--lh-narrative)] text-[var(--fg-muted)]',
        )}
      >
        Everything stays on your machine. Kora never sends your data to a
        cloud service unless you explicitly configure one in Settings.
      </p>

      <div className="flex justify-end pt-2">
        <Button
          type="button"
          onClick={onContinue}
          aria-label="Continue to daemon check"
        >
          Continue
          <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden />
        </Button>
      </div>
    </section>
  );
}
