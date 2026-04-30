import type { ReactNode } from 'react';
import { Sidebar } from './sidebar';
import { ChatPanel } from './chat-panel';
import { CommandBar } from './command-bar';
import { TooltipProvider } from '@/components/ui/tooltip';

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <TooltipProvider>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-[var(--bg)] text-[var(--fg)]">
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
