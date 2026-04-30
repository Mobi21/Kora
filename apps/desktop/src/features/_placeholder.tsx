import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useChatStore } from '@/lib/ws/store';
import { cn } from '@/lib/utils';

interface PlaceholderScreenProps {
  title: string;
  description?: string;
  icon: LucideIcon;
  maxWidth?: string;
  fullBleed?: boolean;
}

export function PlaceholderScreen({
  title,
  description = 'This screen lives here. Wiring it up to your daemon next.',
  icon,
  maxWidth = 'var(--ws-today)',
  fullBleed = false,
}: PlaceholderScreenProps): JSX.Element {
  const setOpen = useChatStore((s) => s.setPanelOpen);
  return (
    <div
      className={cn(
        'flex h-full w-full items-center justify-center px-6 py-10',
        fullBleed ? 'max-w-none' : '',
      )}
    >
      <div
        style={fullBleed ? undefined : { maxWidth }}
        className={cn('w-full', fullBleed ? 'h-full' : '')}
      >
        <EmptyState
          icon={icon}
          title={title}
          description={description}
          action={
            <Button onClick={() => setOpen(true)} aria-label={`Open Kora chat for ${title}`}>
              Open Chat
            </Button>
          }
        />
      </div>
    </div>
  );
}
