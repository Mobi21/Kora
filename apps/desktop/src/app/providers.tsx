import { QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import { createQueryClient } from './query-client';
import { ThemeProvider } from '@/lib/theme/provider';
import { useConnectionStore } from '@/lib/api/connection';
import { useChatStore } from '@/lib/ws/store';
import { WSChatClient } from '@/lib/ws/chat';
import { loadDemoChatMessages, demoChatNotice } from '@/lib/demo/chat';
import { isDemoMode } from '@/lib/demo/mode';

function ConnectionBoot({ children }: { children: ReactNode }): JSX.Element {
  const load = useConnectionStore((s) => s.load);
  useEffect(() => {
    if (isDemoMode()) {
      void load();
      return;
    }
    void load();
  }, [load]);
  return <>{children}</>;
}

function ChatBoot({ children }: { children: ReactNode }): JSX.Element {
  const connection = useConnectionStore((s) => s.connection);
  const attachClient = useChatStore((s) => s.attachClient);
  const detachClient = useChatStore((s) => s.detachClient);
  const loadReadOnlyTranscript = useChatStore((s) => s.loadReadOnlyTranscript);
  const ref = useRef<WSChatClient | null>(null);

  useEffect(() => {
    if (isDemoMode()) {
      let cancelled = false;
      void loadDemoChatMessages()
        .then((messages) => {
          if (cancelled) return;
          loadReadOnlyTranscript(messages.length ? messages : [demoChatNotice()]);
        })
        .catch(() => {
          if (!cancelled) loadReadOnlyTranscript([demoChatNotice()]);
        });
      return () => {
        cancelled = true;
      };
    }
    if (!connection) return;
    const client = new WSChatClient(connection, { autoReconnect: true });
    ref.current = client;
    attachClient(client);
    client.connect();
    return () => {
      detachClient();
      ref.current = null;
    };
  }, [connection, attachClient, detachClient, loadReadOnlyTranscript]);

  return <>{children}</>;
}

export function Providers({ children }: { children: ReactNode }): JSX.Element {
  const queryClient = useMemo(() => createQueryClient(), []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ConnectionBoot>
          <ChatBoot>{children}</ChatBoot>
        </ConnectionBoot>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
