import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import type { IntegrationStatusView, IntegrationToolView } from '@/lib/api/types';

interface ToolListPanelProps {
  integrations: IntegrationStatusView[];
  tools: IntegrationToolView[];
}

export function ToolListPanel({
  integrations,
  tools,
}: ToolListPanelProps): JSX.Element {
  const [query, setQuery] = useState('');

  const integrationById = useMemo(() => {
    const map = new Map<string, IntegrationStatusView>();
    for (const integration of integrations) {
      map.set(integration.id, integration);
    }
    return map;
  }, [integrations]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tools;
    return tools.filter((tool) => {
      const integration = integrationById.get(tool.integration_id);
      const haystack = [
        tool.name,
        tool.description ?? '',
        integration?.label ?? '',
        tool.status,
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [query, tools, integrationById]);

  return (
    <section
      className="flex flex-col gap-3"
      aria-labelledby="all-tools-heading"
    >
      <header className="flex items-baseline justify-between gap-3">
        <h2
          id="all-tools-heading"
          className={cn(
            'font-narrative text-[var(--fs-xl)] tracking-[var(--track-tight)]',
            'text-[var(--fg)]',
          )}
        >
          All tools
          <span className="ml-2 font-mono text-[var(--fs-xs)] text-[var(--fg-muted)] num-tabular">
            · {tools.length}
          </span>
        </h2>
      </header>

      <div className="relative">
        <Search
          aria-hidden
          className={cn(
            'pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2',
            'text-[var(--fg-subtle)]',
          )}
          strokeWidth={1.5}
        />
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter tools by name, server, or description"
          aria-label="Filter tools"
          className="pl-9"
        />
      </div>

      {filtered.length === 0 ? (
        <p className="text-[var(--fs-sm)] text-[var(--fg-muted)]">
          {tools.length === 0
            ? 'No tools reported yet.'
            : 'No tools match this filter.'}
        </p>
      ) : (
        <ul
          className={cn(
            'flex flex-col rounded-[var(--r-2)] border border-[var(--border)]',
            'bg-[var(--surface-1)] divide-y divide-[var(--border)]',
          )}
        >
          {filtered.map((tool) => {
            const integration = integrationById.get(tool.integration_id);
            return (
              <li
                key={`${tool.integration_id}:${tool.name}`}
                className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] items-center gap-3 px-3 py-2"
              >
                <span
                  className="min-w-0 truncate text-[var(--fs-sm)] text-[var(--fg-muted)]"
                  title={integration?.label ?? tool.integration_id}
                >
                  {integration?.label ?? tool.integration_id}
                </span>
                <span
                  className={cn(
                    'min-w-0 truncate font-mono text-[var(--fs-xs)] text-[var(--fg)]',
                    'num-tabular',
                  )}
                  title={tool.description ?? tool.name}
                >
                  {tool.name}
                </span>
                <ToolStatusChip status={tool.status} />
              </li>
            );
          })}
        </ul>
      )}

      <p className="font-mono text-[var(--fs-2xs)] text-[var(--fg-subtle)] num-tabular">
        {summarizeStatuses(tools)}
      </p>
    </section>
  );
}

function ToolStatusChip({
  status,
}: {
  status: IntegrationToolView['status'];
}): JSX.Element {
  const label =
    status === 'available' ? 'available' : status === 'failing' ? 'failing' : 'untested';
  const tone =
    status === 'available'
      ? 'var(--ok)'
      : status === 'failing'
        ? 'var(--danger)'
        : 'var(--fg-subtle)';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-[var(--border)]',
        'bg-[var(--surface-2)] px-2 py-0.5 text-[var(--fs-2xs)] text-[var(--fg)] num-tabular',
      )}
      role="status"
      aria-label={label}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: tone }}
      />
      {label}
    </span>
  );
}

function summarizeStatuses(tools: IntegrationToolView[]): string {
  if (tools.length === 0) return '0 available · 0 failing · 0 untested';
  let available = 0;
  let failing = 0;
  let untested = 0;
  for (const tool of tools) {
    if (tool.status === 'available') available += 1;
    else if (tool.status === 'failing') failing += 1;
    else untested += 1;
  }
  return `${available} available · ${failing} failing · ${untested} untested`;
}
