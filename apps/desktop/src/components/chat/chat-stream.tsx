import { useEffect, useRef } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { EmptyState } from '@/components/ui/empty-state';
import { Sparkles } from 'lucide-react';
import { useChatStore } from '@/lib/ws/store';
import { ChatMessage } from './chat-message';

export function ChatStream(): JSX.Element {
  const messages = useChatStore((s) => s.messages);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={Sparkles}
          title="Say hi to Kora"
          description="Type below to start a conversation. Kora can inspect any screen, preview changes, and help repair the day."
        />
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 px-4 py-4">
        {messages.map((m) => (
          <ChatMessage key={m.id} message={m} />
        ))}
        <div ref={endRef} />
      </div>
    </ScrollArea>
  );
}
