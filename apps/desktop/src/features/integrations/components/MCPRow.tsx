import { useState } from 'react';
import { ChevronRight, MoreHorizontal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/ui/pill';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import type { IntegrationStatusView, IntegrationToolView } from '@/lib/api/types';
import { getKindColor } from './KindIcon';
import { healthLabel, healthToPill } from './IntegrationCard';

interface MCPRowProps {
  integration: IntegrationStatusView;
  tools: IntegrationToolView[];
  onRecheck: () => void;
}

export function MCPRow({ integration, tools, onRecheck }: MCPRowProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ruleColor = getKindColor('mcp');
  const failingCount = integration.tools_failing;
  const availableCount = integration.tools_available;
  const disabled = integration.enabled === false;

  // Failing tools first; preserves server order otherwise. Useful when
  // expanded so the user lands on what needs attention.
  const orderedTools = [...tools].sort((a, b) => {
    if (a.status === b.status) return 0;
    if (a.status === 'failing') return -1;
    if (b.status === 'failing') return 1;
    return 0;
  });

  return (
    <div
      className={cn(
        'relative flex flex-col gap-2 py-3 pl-3 pr-2',
        disabled && 'opacity-60',
      )}
    >
      <div
        aria-hidden
        className="absolute inset-y-2 left-0 w-[3px] rounded-[var(--r-pill)]"
        style={{ background: ruleColor }}
      />

      <div className="flex items-start gap-3 pl-3">
        <span
          aria-hidden
          className="mt-1.5 inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: ruleColor }}
        />

        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
          aria-controls={`mcp-tools-${integration.id}`}
          className={cn(
            'flex min-w-0 flex-1 items-start gap-2 text-left',
            'rounded-[var(--r-1)] focus-visible:outline-none focus-visible:ring-2',
            'focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2',
            'focus-visible:ring-offset-[var(--bg)]',
          )}
        >
          <ChevronRight
            className={cn(
              'mt-1 h-3.5 w-3.5 shrink-0 text-[var(--fg-subtle)]',
              'transition-transform duration-[var(--motion-fast)] ease-[var(--ease-out)]',
              open && 'rotate-90',
            )}
            strokeWidth={1.5}
          />
          <div className="flex min-w-0 flex-col gap-0.5">
            <span
              className={cn(
                'font-narrative text-[var(--fs-md)] text-[var(--fg)]',
                'tracking-[var(--track-tight)]',
              )}
            >
              {integration.label}
            </span>
            {integration.detail && (
              <span className="text-[var(--fs-base)] text-[var(--fg-muted)]">
                {integration.detail}
              </span>
            )}
          </div>
        </button>

        <div className="flex shrink-0 items-center gap-2">
          <CountChip label="tools" value={availableCount} />
          {failingCount > 0 && <FailingChip count={failingCount} />}
          <Pill status={healthToPill(integration.health)} label={healthLabel(integration.health)} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Actions for ${integration.label}`}
              >
                <MoreHorizontal className="h-4 w-4" strokeWidth={1.5} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => onRecheck()}>Recheck</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => navigate('/settings#mcp')}>
                Open settings
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {open && (
        <div
          id={`mcp-tools-${integration.id}`}
          className={cn(
            'ml-9 mt-1 flex flex-col gap-2 rounded-[var(--r-2)]',
            'border border-[var(--border)] bg-[var(--surface-2)] p-3',
          )}
        >
          {orderedTools.length === 0 ? (
            <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
              No tools reported by this server.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {orderedTools.map((tool) => (
                <ToolRow key={`${tool.integration_id}:${tool.name}`} tool={tool} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function ToolRow({ tool }: { tool: IntegrationToolView }): JSX.Element {
  const failing = tool.status === 'failing';
  return (
    <li
      className={cn(
        'flex flex-col gap-1 rounded-[var(--r-1)] px-2 py-1.5',
        failing && 'border border-[color-mix(in_oklch,var(--danger)_30%,var(--border))]',
        failing && 'bg-[color-mix(in_oklch,var(--danger)_5%,transparent)]',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <span
          className={cn(
            'min-w-0 truncate font-mono text-[var(--fs-xs)] text-[var(--fg)]',
            'num-tabular',
          )}
          title={tool.name}
        >
          {tool.name}
        </span>
        <ToolStatus status={tool.status} />
      </div>
      {tool.description && (
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">{tool.description}</p>
      )}
      {failing && tool.last_error && (
        <pre
          className={cn(
            'mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words',
            'rounded-[var(--r-1)] bg-[var(--surface-3)] px-2 py-1.5',
            'font-mono text-[var(--fs-2xs)] text-[var(--danger)] num-tabular',
          )}
        >
          {tool.last_error}
        </pre>
      )}
    </li>
  );
}

function ToolStatus({ status }: { status: IntegrationToolView['status'] }): JSX.Element {
  switch (status) {
    case 'available':
      return <Pill status="ok" label="available">available</Pill>;
    case 'failing':
      return <Pill status="degraded" label="failing">failing</Pill>;
    case 'untested':
    default:
      return <Pill status="unknown" label="untested">untested</Pill>;
  }
}

function CountChip({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-[var(--r-pill)] border border-[var(--border)]',
        'bg-[var(--surface-1)] px-2 py-0.5 text-[var(--fs-2xs)] text-[var(--fg-muted)]',
      )}
    >
      <span className="font-mono num-tabular text-[var(--fg)]">{value}</span>
      <span className="uppercase tracking-[var(--track-label)]">{label}</span>
    </span>
  );
}

function FailingChip({ count }: { count: number }): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-[var(--r-pill)]',
        'border border-[color-mix(in_oklch,var(--warn)_40%,var(--border))]',
        'bg-[color-mix(in_oklch,var(--warn)_8%,transparent)]',
        'px-2 py-0.5 text-[var(--fs-2xs)] text-[var(--fg)]',
      )}
      role="status"
      aria-label={`${count} failing`}
    >
      <span className="font-mono num-tabular">{count}</span>
      <span className="uppercase tracking-[var(--track-label)] text-[var(--fg-muted)]">
        failing
      </span>
    </span>
  );
}
