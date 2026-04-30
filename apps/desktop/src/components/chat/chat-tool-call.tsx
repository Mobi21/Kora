import { Check, Loader2, X } from 'lucide-react';
import type { ChatToolCallView } from '@/lib/ws/store';
import { cn } from '@/lib/utils';

export function ChatToolCall({ call }: { call: ChatToolCallView }): JSX.Element {
  const Icon = call.status === 'running' ? Loader2 : call.status === 'ok' ? Check : X;
  const tone =
    call.status === 'ok'
      ? 'text-[var(--ok)]'
      : call.status === 'error'
        ? 'text-[var(--danger)]'
        : 'text-[var(--fg-muted)]';
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-[var(--r-1)] border border-[var(--border)]',
        'bg-[var(--surface-2)] px-2.5 py-1.5 text-[var(--fs-xs)]',
      )}
    >
      <Icon
        className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', tone, call.status === 'running' && 'animate-spin')}
        strokeWidth={1.5}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="font-mono text-[var(--fg)]">{call.tool_name}</span>
          {call.arguments_summary && (
            <span className="truncate text-[var(--fg-muted)]">{call.arguments_summary}</span>
          )}
        </div>
        {call.result_summary && (
          <p className="mt-0.5 text-[var(--fg-muted)]">{call.result_summary}</p>
        )}
      </div>
    </div>
  );
}
