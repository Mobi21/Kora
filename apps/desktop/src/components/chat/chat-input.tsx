import { ArrowUp } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { useChatStore } from '@/lib/ws/store';
import { cn } from '@/lib/utils';

export function ChatInput(): JSX.Element {
  const [value, setValue] = useState('');
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const append = useChatStore((s) => s.appendUserMessage);
  const connectionState = useChatStore((s) => s.connectionState);
  const messages = useChatStore((s) => s.messages);
  const lastStreaming = messages[messages.length - 1]?.streaming ?? false;
  const disabled = connectionState !== 'open' || lastStreaming;

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    append(trimmed);
    setValue('');
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div
      className={cn(
        'flex items-end gap-2 rounded-[var(--r-2)] border border-[var(--border)]',
        'bg-[var(--surface-1)] p-2 focus-within:border-[var(--border-strong)]',
      )}
    >
      <textarea
        ref={taRef}
        rows={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKey}
        placeholder="Ask Kora…"
        aria-label="Message Kora"
        className={cn(
          'flex-1 resize-none bg-transparent px-1 py-1.5 text-[var(--fs-base)] text-[var(--fg)]',
          'placeholder:text-[var(--fg-subtle)] outline-none',
        )}
      />
      <Button
        type="button"
        size="icon"
        aria-label="Send message"
        disabled={disabled || value.trim().length === 0}
        onClick={submit}
      >
        <ArrowUp className="h-4 w-4" strokeWidth={1.5} />
      </Button>
    </div>
  );
}
