import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface WizardStep {
  /** 1-based index in the rendered indicator. */
  index: number;
  label: string;
}

interface WizardLayoutProps {
  steps: readonly WizardStep[];
  currentIndex: number;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}

/**
 * Full-viewport wizard chrome. Renders without the AppShell's sidebar or
 * chat panel — the gate routes to /first-run before the shell mounts.
 *
 * Visual: single 560px card on a soft 2-stop warm gradient. Three numbered
 * dots indicate progress. Headline uses Fraunces; body uses Inter.
 */
export function WizardLayout({
  steps,
  currentIndex,
  title,
  subtitle,
  children,
  footer,
}: WizardLayoutProps): JSX.Element {
  return (
    <div
      className={cn(
        'flex min-h-screen w-screen items-center justify-center',
        'px-6 py-12',
      )}
      style={{
        background:
          'linear-gradient(180deg, var(--bg) 0%, oklch(96% 0.020 80) 100%)',
        color: 'var(--fg)',
      }}
    >
      <div
        className={cn(
          'w-full max-w-[560px]',
          'rounded-[var(--r-3)] border border-[var(--border)] bg-[var(--surface-1)]',
          'shadow-[var(--shadow-2)]',
          'flex flex-col',
        )}
      >
        <header className="flex flex-col gap-5 px-9 pb-6 pt-9">
          <StepIndicator steps={steps} currentIndex={currentIndex} />
          <div className="space-y-2">
            <h1 className="font-narrative text-[var(--fs-3xl)] leading-tight tracking-[var(--track-tight)] text-[var(--fg)]">
              {title}
            </h1>
            {subtitle && (
              <p className="text-[var(--fs-base)] leading-[var(--lh-narrative)] text-[var(--fg-muted)]">
                {subtitle}
              </p>
            )}
          </div>
        </header>

        <div className="px-9 pb-2">{children}</div>

        {footer && (
          <footer
            className={cn(
              'mt-6 flex flex-wrap items-center justify-between gap-3',
              'border-t border-[var(--border)] bg-[var(--surface-1)]',
              'rounded-b-[var(--r-3)]',
              'px-9 py-5',
            )}
          >
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

function StepIndicator({
  steps,
  currentIndex,
}: {
  steps: readonly WizardStep[];
  currentIndex: number;
}): JSX.Element {
  return (
    <ol
      className="flex items-center gap-3"
      role="list"
      aria-label="Wizard progress"
    >
      {steps.map((step, i) => {
        const isCurrent = i === currentIndex;
        const isDone = i < currentIndex;
        return (
          <li
            key={step.index}
            className="flex items-center gap-3"
            aria-current={isCurrent ? 'step' : undefined}
          >
            <span
              aria-label={`Step ${step.index} of ${steps.length}: ${step.label}`}
              className={cn(
                'inline-flex h-7 w-7 items-center justify-center',
                'rounded-full border font-mono text-[var(--fs-xs)] num-tabular',
                'transition-[background-color,color,border-color] duration-[var(--motion)] ease-[var(--ease-out)]',
                isDone &&
                  'border-transparent bg-[var(--accent)] text-[var(--accent-fg)]',
                isCurrent &&
                  !isDone &&
                  'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]',
                !isCurrent &&
                  !isDone &&
                  'border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-subtle)]',
              )}
            >
              {step.index}
            </span>
            {i < steps.length - 1 && (
              <span
                aria-hidden
                className={cn(
                  'h-px w-6 bg-[var(--border)]',
                  isDone && 'bg-[var(--accent)]',
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
