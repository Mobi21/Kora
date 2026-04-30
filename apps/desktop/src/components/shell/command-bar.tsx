import { Command, MessageSquare } from 'lucide-react';
import { Kbd } from '@/components/ui/kbd';
import { Button } from '@/components/ui/button';
import { RuntimeIndicator } from './runtime-indicator';
import { useCommandPaletteStore } from '@/lib/shortcuts';
import { useChatStore } from '@/lib/ws/store';

export function CommandBar(): JSX.Element {
  const openPalette = useCommandPaletteStore((s) => s.toggle);
  const togglePanel = useChatStore((s) => s.togglePanel);
  const panelOpen = useChatStore((s) => s.panelOpen);

  return (
    <div
      role="contentinfo"
      aria-label="Command bar"
      className="flex h-[var(--command-bar-h)] shrink-0 items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--surface-1)] px-3 text-[var(--fs-xs)] text-[var(--fg-muted)]"
    >
      <div className="flex items-center gap-2">
        <RuntimeIndicator />
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          aria-label={panelOpen ? 'Hide chat panel' : 'Show chat panel'}
          onClick={togglePanel}
          className="h-7 gap-1.5 px-2 text-[var(--fg-muted)]"
        >
          <MessageSquare className="h-3.5 w-3.5" strokeWidth={1.5} />
          <span className="hidden sm:inline">Chat</span>
          <Kbd>⌘/</Kbd>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Open command palette"
          onClick={openPalette}
          className="h-7 gap-1.5 px-2 text-[var(--fg-muted)]"
        >
          <Command className="h-3.5 w-3.5" strokeWidth={1.5} />
          <span className="hidden sm:inline">Command</span>
          <Kbd>⌘K</Kbd>
        </Button>
      </div>
    </div>
  );
}
