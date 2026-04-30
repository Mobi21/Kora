import type { ReactNode } from 'react';
import { Sidebar } from './sidebar';
import { ChatPanel } from './chat-panel';
import { CommandBar } from './command-bar';
import { TooltipProvider } from '@/components/ui/tooltip';
import { DEMO_LABEL, isDemoMode } from '@/lib/demo/mode';

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps): JSX.Element {
  const demo = isDemoMode();
  return (
    <TooltipProvider>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-[var(--bg)] text-[var(--fg)]">
        {demo && (
          <div className="shrink-0 border-b border-[var(--border)] bg-[var(--surface-2)] px-4 py-1.5 text-center font-mono text-[var(--fs-2xs)] uppercase tracking-[0.02em] text-[var(--fg-muted)]">
            {DEMO_LABEL}
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          <Sidebar />
          <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--bg)]">
            <div className="flex-1 overflow-auto">{children}</div>
          </main>
          <ChatPanel />
        </div>
        <CommandBar />
      </div>
    </TooltipProvider>
  );
}
