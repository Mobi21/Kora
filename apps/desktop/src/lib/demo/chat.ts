import type { ChatMessage } from '@/lib/ws/store';
import { DEMO_LABEL } from './mode';

export async function loadDemoChatMessages(): Promise<ChatMessage[]> {
  return DEMO_CHAT_MESSAGES;
}

export function demoChatNotice(): ChatMessage {
  return {
    id: 'demo-chat-notice',
    role: 'assistant',
    content: DEMO_LABEL,
    streaming: false,
    created_at: new Date().toISOString(),
    tool_calls: [],
    artifacts: [],
  };
}

const DEMO_CHAT_MESSAGES: ChatMessage[] = [
  {
    id: 'demo-chat-1',
    role: 'user',
    content:
      "It's Thursday Apr 30 and my day already feels messy. What actually matters next?",
    streaming: false,
    created_at: '2026-04-30T13:42:00-04:00',
    tool_calls: [],
    artifacts: [],
  },
  {
    id: 'demo-chat-2',
    role: 'assistant',
    content:
      "The protected anchors are the STAT quiz window, HCI peer feedback, and Priya rent/utilities tonight. I moved the day into stabilization mode and kept the plan small: short mom text, quiz window, feedback due, then rent/utilities.",
    streaming: false,
    created_at: '2026-04-30T13:42:12-04:00',
    tool_calls: [],
    artifacts: [],
  },
  {
    id: 'demo-chat-3',
    role: 'user',
    content:
      "I missed groceries and laundry. Please don't let that wreck the whole week.",
    streaming: false,
    created_at: '2026-04-30T16:02:00-04:00',
    tool_calls: [],
    artifacts: [],
  },
  {
    id: 'demo-chat-4',
    role: 'assistant',
    content:
      "I marked grocery and laundry as blocked, protected school and rent commitments, and suggested a repair instead of pretending the original day still works. The repair board shows what can move to tomorrow without losing context.",
    streaming: false,
    created_at: '2026-04-30T16:02:18-04:00',
    tool_calls: [],
    artifacts: [],
  },
  {
    id: 'demo-chat-5',
    role: 'user',
    content: 'Can you preserve this so tomorrow starts with the real situation?',
    streaming: false,
    created_at: '2026-04-30T21:08:00-04:00',
    tool_calls: [],
    artifacts: [],
  },
  {
    id: 'demo-chat-6',
    role: 'assistant',
    content:
      "Yes. I saved the bridge context, memory notes, and acceptance proof from this repaired day. Tomorrow can start from what actually happened instead of making you reconstruct it from scratch.",
    streaming: false,
    created_at: '2026-04-30T21:08:15-04:00',
    tool_calls: [],
    artifacts: [],
  },
];
