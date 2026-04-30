import { useState } from 'react';
import { ArrowRight, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { CHANGE_TYPES } from '../queries';

interface WhatChangedStepProps {
  initialReason: string | null;
  isSubmitting: boolean;
  errorMessage: string | null;
  onSubmit: (changeType: string, note: string) => void;
}

export function WhatChangedStep({
  initialReason,
  isSubmitting,
  errorMessage,
  onSubmit,
}: WhatChangedStepProps): JSX.Element {
  const [selected, setSelected] = useState<string | null>(initialReason);
  const [note, setNote] = useState('');

  const canSubmit = selected !== null && !isSubmitting;

  return (
    <section
      aria-labelledby="repair-what-changed"
      className="flex flex-col gap-6"
    >
      <h2
        id="repair-what-changed"
        className="font-narrative text-[var(--fs-2xl)] tracking-[var(--track-tight)] text-[var(--fg)]"
      >
        What changed?
      </h2>

      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="What changed today">
        {CHANGE_TYPES.map((option) => {
          const Icon = option.icon;
          const isSelected = selected === option.id;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => setSelected(option.id)}
              className={cn(
                'group relative inline-flex items-center gap-2 rounded-[var(--r-2)]',
                'border border-[var(--border)] bg-[var(--surface-1)] px-4 py-2.5',
                'text-[var(--fs-sm)] text-[var(--fg)]',
                'transition-[background-color,border-color,box-shadow]',
                'duration-[var(--motion-fast)] ease-[var(--ease-out)]',
                'hover:bg-[var(--surface-2)]',
                'focus-visible:outline-none focus-visible:ring-2',
                'focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2',
                'focus-visible:ring-offset-[var(--bg)]',
              )}
              style={
                isSelected
                  ? {
                      backgroundColor: 'var(--accent-soft)',
                      borderColor: 'transparent',
                      boxShadow: 'inset 4px 0 0 var(--accent)',
                    }
                  : undefined
              }
            >
              <Icon
                className={cn(
                  'h-4 w-4',
                  isSelected ? 'text-[var(--accent)]' : 'text-[var(--fg-muted)]',
                )}
                strokeWidth={1.5}
                aria-hidden
              />
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="repair-note" className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          Anything else? (optional)
        </Label>
        <Input
          id="repair-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Context that helps Kora understand…"
          maxLength={400}
        />
      </div>

      {errorMessage && (
        <p
          role="alert"
          className="text-[var(--fs-sm)] text-[var(--danger)]"
        >
          {errorMessage}
        </p>
      )}

      <div className="flex items-center justify-end">
        <Button
          variant="default"
          size="lg"
          disabled={!canSubmit}
          onClick={() => {
            if (selected) onSubmit(selected, note.trim());
          }}
          className="min-w-[220px]"
        >
          {isSubmitting ? (
            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.5} aria-hidden />
          ) : (
            <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden />
          )}
          {isSubmitting ? 'Looking…' : 'Show me what to change'}
        </Button>
      </div>
    </section>
  );
}
