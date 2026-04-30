import { useNavigate } from 'react-router-dom';
import {
  Calendar as CalendarIcon,
  ClipboardList,
  Compass,
  HeartPulse,
  ListChecks,
  MessageSquare,
  Pill as PillIcon,
  Plug,
  Settings as SettingsIcon,
  Sparkles,
  Wrench,
} from 'lucide-react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import { useCommandPaletteStore } from '@/lib/shortcuts';
import { useChatStore } from '@/lib/ws/store';

interface NavCommand {
  id: string;
  label: string;
  to: string;
  icon: typeof Sparkles;
  hint?: string;
}

const NAV: NavCommand[] = [
  { id: 'today', label: 'Go to Today', to: '/today', icon: Sparkles, hint: '⌘1' },
  { id: 'calendar', label: 'Go to Calendar', to: '/calendar', icon: CalendarIcon, hint: '⌘2' },
  { id: 'medication', label: 'Go to Medication', to: '/medication', icon: PillIcon, hint: '⌘3' },
  { id: 'routines', label: 'Go to Routines', to: '/routines', icon: ListChecks, hint: '⌘4' },
  { id: 'repair', label: 'Go to Repair', to: '/repair', icon: Wrench, hint: '⌘5' },
  { id: 'memory', label: 'Go to Memory', to: '/memory', icon: ClipboardList, hint: '⌘6' },
  { id: 'autonomous', label: 'Go to Autonomous', to: '/autonomous', icon: Compass, hint: '⌘7' },
  { id: 'integrations', label: 'Go to Integrations', to: '/integrations', icon: Plug, hint: '⌘8' },
  { id: 'settings', label: 'Go to Settings', to: '/settings', icon: SettingsIcon, hint: '⌘9' },
  { id: 'runtime', label: 'Go to Runtime', to: '/runtime', icon: HeartPulse },
];

export function CommandPalette(): JSX.Element {
  const open = useCommandPaletteStore((s) => s.open);
  const setOpen = useCommandPaletteStore((s) => s.setOpen);
  const togglePanel = useChatStore((s) => s.togglePanel);
  const navigate = useNavigate();

  function runNav(to: string) {
    setOpen(false);
    navigate(to);
  }

  function runChat() {
    setOpen(false);
    togglePanel();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="w-full max-w-xl border-0 bg-transparent p-0 shadow-none">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Search and run commands across Kora.
        </DialogDescription>
        <Command label="Kora command palette">
          <CommandInput placeholder="Search commands…" autoFocus />
          <CommandList>
            <CommandEmpty>No matching commands.</CommandEmpty>
            <CommandGroup heading="Navigate">
              {NAV.map((c) => {
                const Icon = c.icon;
                return (
                  <CommandItem key={c.id} value={c.label} onSelect={() => runNav(c.to)}>
                    <Icon className="h-4 w-4 text-[var(--fg-muted)]" strokeWidth={1.5} />
                    <span>{c.label}</span>
                    {c.hint && (
                      <span className="ml-auto font-mono text-[var(--fs-2xs)] text-[var(--fg-muted)]">
                        {c.hint}
                      </span>
                    )}
                  </CommandItem>
                );
              })}
            </CommandGroup>
            <CommandSeparator />
            <CommandGroup heading="Chat">
              <CommandItem value="Toggle Kora chat panel" onSelect={runChat}>
                <MessageSquare className="h-4 w-4 text-[var(--fg-muted)]" strokeWidth={1.5} />
                <span>Toggle Kora chat panel</span>
                <span className="ml-auto font-mono text-[var(--fs-2xs)] text-[var(--fg-muted)]">⌘/</span>
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
