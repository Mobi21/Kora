import type { Connection } from '../api/connection';
import type { KoraArtifact } from '../api/types';

export interface ChatTokenEvent {
  type: 'token';
  content: string;
}

export interface ChatToolStartEvent {
  type: 'tool_start';
  tool_name: string;
  arguments_summary?: string;
  call_id?: string;
}

export interface ChatToolResultEvent {
  type: 'tool_result';
  tool_name: string;
  call_id?: string;
  ok: boolean;
  summary?: string;
}

export interface ChatArtifactEvent {
  type: 'artifact';
  artifact: KoraArtifact;
}

export interface ChatDecisionEvent {
  type: 'decision';
  id: string;
  prompt: string;
  options: string[];
  pipeline_id?: string | null;
  deadline_at?: string | null;
}

export interface ChatAuthPromptEvent {
  type: 'auth_prompt';
  id: string;
  scope: string;
  reason: string;
}

export interface ChatNotificationEvent {
  type: 'notification';
  id?: string;
  notification_id?: string;
  title?: string;
  text?: string;
  message?: string;
  body?: string;
  priority?: string;
  tier?: string;
  template_id?: string;
  created_at?: string;
}

export interface ChatErrorEvent {
  type: 'error';
  content: string;
}

export interface ChatHeartbeatEvent {
  type: 'heartbeat';
  ts: string;
}

export interface ChatTurnCompleteEvent {
  type: 'response_complete' | 'turn_complete';
  metadata?: Record<string, unknown>;
}

export interface ChatSessionReadyEvent {
  type: 'session_ready';
  metadata?: Record<string, unknown>;
}

export type ChatEvent =
  | ChatSessionReadyEvent
  | ChatTokenEvent
  | ChatToolStartEvent
  | ChatToolResultEvent
  | ChatArtifactEvent
  | ChatDecisionEvent
  | ChatAuthPromptEvent
  | ChatNotificationEvent
  | ChatErrorEvent
  | ChatHeartbeatEvent
  | ChatTurnCompleteEvent;

export type ChatEventListener = (event: ChatEvent) => void;
export type ConnectionStateListener = (state: WSConnectionState) => void;

export type WSConnectionState = 'idle' | 'connecting' | 'open' | 'closing' | 'closed' | 'error';

export class WSChatClient {
  private socket: WebSocket | null = null;
  private state: WSConnectionState = 'idle';
  private listeners = new Set<ChatEventListener>();
  private stateListeners = new Set<ConnectionStateListener>();
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = true;
  private url: string;

  constructor(connection: Connection, opts: { autoReconnect?: boolean } = {}) {
    const proto = 'ws';
    const params = new URLSearchParams({ token: connection.token });
    this.url = `${proto}://${connection.host}:${connection.port}/api/v1/ws?${params.toString()}`;
    this.shouldReconnect = opts.autoReconnect ?? true;
  }

  connect(): void {
    if (this.socket && (this.state === 'open' || this.state === 'connecting')) return;
    this.setState('connecting');
    try {
      this.socket = new WebSocket(this.url);
    } catch (err) {
      this.setState('error');
      this.scheduleReconnect();
      this.emit({ type: 'error', content: (err as Error).message });
      return;
    }

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.setState('open');
    };

    this.socket.onmessage = (msg) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(msg.data as string);
      } catch {
        return;
      }
      if (typeof parsed !== 'object' || parsed === null) return;
      const evt = parsed as ChatEvent;
      this.emit(evt);
    };

    this.socket.onerror = () => {
      this.setState('error');
    };

    this.socket.onclose = () => {
      this.setState('closed');
      if (this.shouldReconnect) this.scheduleReconnect();
    };
  }

  send(content: string): boolean {
    if (!this.socket || this.state !== 'open') return false;
    try {
      this.socket.send(JSON.stringify({ type: 'chat', content }));
      return true;
    } catch {
      return false;
    }
  }

  sendDecision(decisionId: string, choice: string): boolean {
    if (!this.socket || this.state !== 'open') return false;
    try {
      this.socket.send(
        JSON.stringify({ type: 'decision_response', decision_id: decisionId, choice }),
      );
      return true;
    } catch {
      return false;
    }
  }

  close(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.setState('closing');
      try {
        this.socket.close(1000, 'client_close');
      } catch {
        // ignore
      }
      this.socket = null;
    }
    this.setState('closed');
  }

  on(listener: ChatEventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: ConnectionStateListener): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  getState(): WSConnectionState {
    return this.state;
  }

  private emit(event: ChatEvent): void {
    for (const fn of this.listeners) {
      try {
        fn(event);
      } catch {
        // ignore listener errors
      }
    }
  }

  private setState(state: WSConnectionState): void {
    this.state = state;
    for (const fn of this.stateListeners) {
      try {
        fn(state);
      } catch {
        // ignore
      }
    }
  }

  private scheduleReconnect(): void {
    if (!this.shouldReconnect) return;
    if (this.reconnectTimer) return;
    this.reconnectAttempts += 1;
    const delay = Math.min(30_000, 500 * Math.pow(2, this.reconnectAttempts));
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
