import type { ReactNode } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { TooltipProvider } from '@/components/ui/tooltip';

interface SettingsLayoutProps {
  rail: ReactNode;
  children: ReactNode;
}

/**
 * Two-column shell: rail on the left, scrollable content on the right.
 * Width is capped at 760px (the Settings workspace token).
 */
export function SettingsLayout({ rail, children }: SettingsLayoutProps): JSX.Element {
  return (
    <TooltipProvider>
      <div className="flex h-full w-full overflow-hidden">
        {rail}
        <ScrollArea className="flex-1">
          <div
            className="mx-auto flex w-full flex-col gap-10 px-6 py-8 md:px-10 md:py-10"
            style={{ maxWidth: 'var(--ws-settings)' }}
          >
            {children}
          </div>
        </ScrollArea>
      </div>
    </TooltipProvider>
  );
}
