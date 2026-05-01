import { create } from 'zustand';
import type { KoraArtifact } from '../api/types';
import type {
  ChatAuthPromptEvent,
  ChatDecisionEvent,
  ChatEvent,
  WSConnectionState,
  WSChatClient,
} from './chat';

export interface ChatToolCallView {
  call_id: string;
  tool_name: string;
  arguments_summary?: string;
  status: 'running' | 'ok' | 'error';
  result_summary?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming: boolean;
  created_at: string;
  tool_calls: ChatToolCallView[];
  artifacts: string[];
}

export interface ChatState {
  client: WSChatClient | null;
  connectionState: WSConnectionState;
  panelOpen: boolean;
  panelWidth: number;
  messages: ChatMessage[];
  artifacts: Record<string, KoraArtifact>;
  pendingDecisions: ChatDecisionEvent[];
  pendingAuthPrompts: ChatAuthPromptEvent[];
  lastError: string | null;
  attachClient: (client: WSChatClient) => void;
  detachClient: () => void;
  applyEvent: (event: ChatEvent) => void;
  appendUserMessage: (content: string) => void;
  loadReadOnlyTranscript: (messages: ChatMessage[]) => void;
  setPanelOpen: (open: boolean) => void;
  togglePanel: () => void;
  setPanelWidth: (width: number) => void;
  resolveDecision: (id: string) => void;
  resolveAuthPrompt: (id: string) => void;
  clear: () => void;
}

function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

export const useChatStore = create<ChatState>((set, get) => ({
  client: null,
  connectionState: 'idle',
  panelOpen: false,
  panelWidth: 380,
  messages: [],
  artifacts: {},
  pendingDecisions: [],
  pendingAuthPrompts: [],
  lastError: null,

  attachClient: (client) => {
    set({ client });
    client.onState((connectionState) => set({ connectionState }));
    client.on((event) => get().applyEvent(event));
  },

  detachClient: () => {
    const c = get().client;
    if (c) c.close();
    set({ client: null, connectionState: 'closed' });
  },

  appendUserMessage: (content) => {
    const message: ChatMessage = {
      id: uuid(),
      role: 'user',
      content,
      streaming: false,
      created_at: new Date().toISOString(),
      tool_calls: [],
      artifacts: [],
    };
    set((s) => ({ messages: [...s.messages, message] }));
    const c = get().client;
    c?.send(content);
  },

  loadReadOnlyTranscript: (messages) => {
    set({
      client: null,
      connectionState: 'closed',
      messages,
      pendingDecisions: [],
      pendingAuthPrompts: [],
      lastError: null,
    });
  },

  applyEvent: (event) => {
    set((s) => {
      const next = { ...s };
      switch (event.type) {
        case 'token': {
          const last = next.messages[next.messages.length - 1];
          if (last && last.role === 'assistant' && last.streaming) {
            const updated: ChatMessage = { ...last, content: last.content + event.content };
            next.messages = [...next.messages.slice(0, -1), updated];
          } else {
            const m: ChatMessage = {
              id: uuid(),
              role: 'assistant',
              content: event.content,
              streaming: true,
              created_at: new Date().toISOString(),
              tool_calls: [],
              artifacts: [],
            };
            next.messages = [...next.messages, m];
          }
          break;
        }
        case 'tool_start': {
          const callId = event.call_id ?? uuid();
          const last = next.messages[next.messages.length - 1];
          const tool: ChatToolCallView = {
            call_id: callId,
            tool_name: event.tool_name,
            arguments_summary: event.arguments_summary,
            status: 'running',
          };
          if (last && last.role === 'assistant') {
            const updated: ChatMessage = { ...last, tool_calls: [...last.tool_calls, tool] };
            next.messages = [...next.messages.slice(0, -1), updated];
          } else {
            const m: ChatMessage = {
              id: uuid(),
              role: 'assistant',
              content: '',
              streaming: true,
              created_at: new Date().toISOString(),
              tool_calls: [tool],
              artifacts: [],
            };
            next.messages = [...next.messages, m];
          }
          break;
        }
        case 'tool_result': {
          next.messages = next.messages.map((m) => {
            if (m.role !== 'assistant') return m;
            const tool_calls = m.tool_calls.map((tc) =>
              tc.call_id === event.call_id || tc.tool_name === event.tool_name
                ? {
                    ...tc,
                    status: event.ok ? ('ok' as const) : ('error' as const),
                    result_summary: event.summary,
                  }
                : tc,
            );
            return { ...m, tool_calls };
          });
          break;
        }
        case 'artifact': {
          next.artifacts = { ...next.artifacts, [event.artifact.id]: event.artifact };
          const last = next.messages[next.messages.length - 1];
          if (last && last.role === 'assistant') {
            const updated: ChatMessage = {
              ...last,
              artifacts: [...last.artifacts, event.artifact.id],
            };
            next.messages = [...next.messages.slice(0, -1), updated];
          }
          break;
        }
        case 'decision': {
          next.pendingDecisions = [...next.pendingDecisions, event];
          break;
        }
        case 'auth_prompt': {
          next.pendingAuthPrompts = [...next.pendingAuthPrompts, event];
          break;
        }
        case 'notification': {
          const title = event.title ?? 'Notification';
          const body = event.message ?? event.body ?? event.text ?? '';
          const content = body ? `${title}: ${body}` : title;
          const duplicateTemplated = event.template_id
            ? next.messages.some((m) => m.content === content)
            : false;
          if (duplicateTemplated) break;
          const m: ChatMessage = {
            id: event.id ?? event.notification_id ?? uuid(),
            role: 'assistant',
            content,
            streaming: false,
            created_at: event.created_at ?? new Date().toISOString(),
            tool_calls: [],
            artifacts: [],
          };
          next.messages = [...next.messages, m];
          break;
        }
        case 'error': {
          next.lastError = event.content;
          break;
        }
        case 'response_complete':
        case 'turn_complete': {
          next.messages = next.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m));
          break;
        }
        case 'heartbeat':
        case 'session_ready':
        default:
          break;
      }
      return next;
    });
  },

  setPanelOpen: (panelOpen) => set({ panelOpen }),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  setPanelWidth: (panelWidth) => set({ panelWidth: Math.max(280, Math.min(640, panelWidth)) }),
  resolveDecision: (id) =>
    set((s) => ({ pendingDecisions: s.pendingDecisions.filter((d) => d.id !== id) })),
  resolveAuthPrompt: (id) =>
    set((s) => ({ pendingAuthPrompts: s.pendingAuthPrompts.filter((d) => d.id !== id) })),
  clear: () =>
    set({
      messages: [],
      artifacts: {},
      pendingDecisions: [],
      pendingAuthPrompts: [],
      lastError: null,
    }),
}));
