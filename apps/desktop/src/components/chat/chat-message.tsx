import type { ChatMessage as ChatMessageType } from '@/lib/ws/store';
import { cn } from '@/lib/utils';
import { ChatToolCall } from './chat-tool-call';
import { ArtifactCard } from '@/components/artifacts/artifact-card';
import { useChatStore } from '@/lib/ws/store';

interface ChatMessageProps {
  message: ChatMessageType;
}

export function ChatMessage({ message }: ChatMessageProps): JSX.Element {
  const artifacts = useChatStore((s) => s.artifacts);
  const isUser = message.role === 'user';

  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'flex max-w-[85%] flex-col gap-2',
          isUser ? 'items-end text-right' : 'items-start',
        )}
      >
        {!isUser && (
          <div className="flex items-center gap-2 text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--accent)]"
            />
            Kora
          </div>
        )}
        {message.content && (
          <div
            className={cn(
              'rounded-[var(--r-2)] px-3 py-2 text-[var(--fs-base)] leading-[var(--lh-narrative)]',
              isUser
                ? 'bg-[var(--accent-soft)] text-[var(--fg)]'
                : 'font-narrative text-[var(--fg)]',
            )}
            style={message.streaming ? { animation: 'kora-fade 200ms ease-out' } : undefined}
          >
            {message.content}
          </div>
        )}
        {message.tool_calls.length > 0 && (
          <div className="flex w-full flex-col gap-1.5">
            {message.tool_calls.map((tc) => (
              <ChatToolCall key={tc.call_id} call={tc} />
            ))}
          </div>
        )}
        {message.artifacts.length > 0 && (
          <div className="flex w-full flex-col gap-2">
            {message.artifacts.map((id) => {
              const a = artifacts[id];
              if (!a) return null;
              return <ArtifactCard key={id} artifact={a} />;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
