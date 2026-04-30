import { Sparkles, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import { ChatStream } from '@/components/chat/chat-stream';
import { ChatInput } from '@/components/chat/chat-input';
import { useChatStore } from '@/lib/ws/store';
import { DEMO_LABEL, isDemoMode } from '@/lib/demo/mode';
import { cn } from '@/lib/utils';

const MIN_WIDTH = 320;
const MAX_WIDTH = 640;

export function ChatPanel(): JSX.Element | null {
  const open = useChatStore((s) => s.panelOpen);
  const width = useChatStore((s) => s.panelWidth);
  const setWidth = useChatStore((s) => s.setPanelWidth);
  const setOpen = useChatStore((s) => s.setPanelOpen);
  const connectionState = useChatStore((s) => s.connectionState);
  const demo = isDemoMode();

  const [dragging, setDragging] = useState(false);
  const startX = useRef<number>(0);
  const startW = useRef<number>(width);

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const delta = startX.current - e.clientX;
      const next = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startW.current + delta));
      setWidth(next);
    },
    [setWidth],
  );

  useEffect(() => {
    if (!dragging) return;
    function onUp() {
      setDragging(false);
    }
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragging, onPointerMove]);

  if (!open) return null;

  return (
    <aside
      aria-label="Kora Chat"
      style={{ width }}
      className={cn(
        'relative flex h-full shrink-0 flex-col border-l border-[var(--border)] bg-[var(--surface-1)]',
      )}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize chat panel"
        onPointerDown={(e) => {
          startX.current = e.clientX;
          startW.current = width;
          setDragging(true);
        }}
        className={cn(
          'absolute left-0 top-0 z-10 h-full w-1 cursor-col-resize',
          'hover:bg-[var(--accent-soft)]',
          dragging && 'bg-[var(--accent-soft)]',
        )}
      />

      <header className="flex h-12 items-center justify-between gap-2 border-b border-[var(--border)] px-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[var(--accent)]" strokeWidth={1.5} />
          <h2 className="font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]">
            Kora
          </h2>
          <Pill
            status={
              demo
                ? 'unknown'
                : connectionState === 'open'
                  ? 'ok'
                  : connectionState === 'connecting'
                    ? 'warn'
                    : 'degraded'
            }
            label={
              demo
                ? 'demo'
                : connectionState
            }
          />
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Close chat panel"
          onClick={() => setOpen(false)}
        >
          <X className="h-4 w-4" strokeWidth={1.5} />
        </Button>
      </header>

      <div className="flex-1 overflow-hidden">
        <ChatStream />
      </div>

      <div className="border-t border-[var(--border)] p-3">
        {demo ? (
          <div
            className={cn(
              'rounded-[var(--r-2)] border border-[var(--border)] bg-[var(--surface-2)]',
              'px-3 py-2 text-[var(--fs-xs)] text-[var(--fg-muted)]',
            )}
          >
            Conversation disabled. {DEMO_LABEL}
          </div>
        ) : (
          <ChatInput />
        )}
      </div>
    </aside>
  );
}
