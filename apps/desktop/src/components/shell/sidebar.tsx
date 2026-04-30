import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Compass,
  Cpu,
  ClipboardList,
  HeartPulse,
  ListChecks,
  Pill as PillIcon,
  Plug,
  Settings as SettingsIcon,
  Sparkles,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { to: '/today', label: 'Today', icon: Sparkles },
  { to: '/calendar', label: 'Calendar', icon: CalendarIcon },
  { to: '/medication', label: 'Medication', icon: PillIcon },
  { to: '/routines', label: 'Routines', icon: ListChecks },
  { to: '/repair', label: 'Repair', icon: Wrench },
  { to: '/memory', label: 'Memory', icon: ClipboardList },
  { to: '/autonomous', label: 'Autonomous', icon: Compass },
  { to: '/integrations', label: 'Integrations', icon: Plug },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/runtime', label: 'Runtime', icon: HeartPulse },
];

export function Sidebar(): JSX.Element {
  const [collapsed, setCollapsed] = useState(true);
  const width = collapsed ? 'var(--rail-w)' : 'var(--rail-w-expanded)';

  return (
    <TooltipProvider>
      <nav
        aria-label="Primary"
        style={{ width }}
        className={cn(
          'flex h-full shrink-0 flex-col border-r border-[var(--border)] bg-[var(--bg)] no-select',
          'transition-[width] duration-[var(--motion)] ease-[var(--ease-out)]',
        )}
      >
        <div className="flex h-12 items-center gap-2 px-3">
          <div
            className="flex h-8 w-8 items-center justify-center rounded-[var(--r-2)] bg-[var(--surface-2)]"
            aria-hidden
          >
            <Cpu className="h-4 w-4 text-[var(--accent)]" strokeWidth={1.5} />
          </div>
          {!collapsed && (
            <span className="font-narrative text-[var(--fs-md)] tracking-[var(--track-tight)] text-[var(--fg)]">
              Kora
            </span>
          )}
        </div>

        <ul className="flex-1 space-y-0.5 px-2 py-2">
          {NAV.map((item) => (
            <li key={item.to}>
              <SidebarItem item={item} collapsed={collapsed} />
            </li>
          ))}
        </ul>

        <div className="px-2 pb-3">
          <button
            type="button"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            onClick={() => setCollapsed((c) => !c)}
            className={cn(
              'inline-flex h-9 w-full items-center justify-center gap-2 rounded-[var(--r-1)]',
              'text-[var(--fg-muted)] hover:bg-[var(--surface-2)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            )}
          >
            {collapsed ? (
              <ChevronRight className="h-4 w-4" strokeWidth={1.5} />
            ) : (
              <>
                <ChevronLeft className="h-4 w-4" strokeWidth={1.5} />
                <span className="text-[var(--fs-xs)]">Collapse</span>
              </>
            )}
          </button>
        </div>
      </nav>
    </TooltipProvider>
  );
}

function SidebarItem({ item, collapsed }: { item: NavItem; collapsed: boolean }): JSX.Element {
  const Icon = item.icon;
  const link = (
    <NavLink
      to={item.to}
      className={({ isActive }) =>
        cn(
          'group relative flex h-9 items-center gap-3 rounded-[var(--r-1)] px-2',
          'text-[var(--fs-sm)] text-[var(--fg-muted)] outline-none',
          'transition-colors duration-[var(--motion-fast)] ease-[var(--ease-out)]',
          'hover:bg-[var(--surface-2)] hover:text-[var(--fg)]',
          'focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          isActive &&
            'bg-[var(--surface-1)] text-[var(--fg)] before:absolute before:left-[-8px] before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-full before:bg-[var(--accent)]',
        )
      }
    >
      <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.5} />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </NavLink>
  );

  if (!collapsed) return link;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}
