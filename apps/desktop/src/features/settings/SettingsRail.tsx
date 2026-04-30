import { SearchableRail, type RailItem } from './components/SearchableRail';

/**
 * Canonical, ordered list of every section on the Settings screen. The
 * rail and the screen consume the same source of truth so anchors,
 * search, and "active" state can never drift.
 */
export const SETTINGS_SECTIONS: ReadonlyArray<RailItem> = [
  {
    id: 'theme',
    label: 'Theme & Display',
    keywords: ['appearance', 'density', 'motion', 'support mode'],
    writable: true,
  },
  {
    id: 'layout',
    label: 'App layout',
    keywords: ['chat panel', 'today', 'modules', 'timeline', 'workspace'],
    writable: true,
  },
  {
    id: 'calendar-layers',
    label: 'Calendar layers',
    keywords: ['layers', 'visibility', 'sources'],
    writable: true,
  },
  { id: 'llm', label: 'LLM', keywords: ['model', 'provider', 'minimax', 'api key', 'base'] },
  {
    id: 'memory',
    label: 'Memory',
    keywords: ['embedding', 'projection', 'retrieval', 'hybrid', 'dedup', 'kora memory'],
  },
  { id: 'agents', label: 'Agents', keywords: ['planner', 'executor', 'reviewer', 'budget', 'iteration'] },
  { id: 'quality', label: 'Quality', keywords: ['confidence', 'sampling', 'regression'] },
  { id: 'daemon', label: 'Daemon', keywords: ['host', 'port', 'lockfile', '127.0.0.1'] },
  { id: 'notifications', label: 'Notifications', keywords: ['proactive', 'cooldown', 'dnd', 'hyperfocus'] },
  { id: 'planning', label: 'Planning cadence', keywords: ['daily', 'weekly', 'monthly', 'reflection'] },
  {
    id: 'autonomous',
    label: 'Autonomous',
    keywords: ['budgets', 'cost', 'tokens', 'concurrent', 'decision', 'request'],
  },
  { id: 'orchestration', label: 'Orchestration', keywords: ['trigger', 'tick', 'background'] },
  { id: 'mcp', label: 'MCP', keywords: ['model context protocol', 'servers', 'tools'] },
  { id: 'security', label: 'Security', keywords: ['cors', 'token', 'injection', 'auth'] },
  { id: 'browser', label: 'Browser', keywords: ['agent browser', 'profile', 'clip'] },
  { id: 'vault', label: 'Vault', keywords: ['obsidian', 'memory mirror'] },
  { id: 'workspace', label: 'Workspace', keywords: ['google', 'calendar', 'tools'] },
  { id: 'timezone', label: 'User timezone', keywords: ['tz', 'time zone', 'display'] },
  { id: 'backups', label: 'Backups & data', keywords: ['data dir', 'memory root', 'settings.toml'] },
] as const;

interface SettingsRailProps {
  activeId: string;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (id: string) => void;
  onResetSection?: () => void;
  matchedFieldCounts?: Readonly<Record<string, number>>;
}

export function SettingsRail({
  activeId,
  query,
  onQueryChange,
  onSelect,
  onResetSection,
  matchedFieldCounts,
}: SettingsRailProps): JSX.Element {
  return (
    <SearchableRail
      items={SETTINGS_SECTIONS}
      activeId={activeId}
      query={query}
      onQueryChange={onQueryChange}
      onSelect={onSelect}
      onResetSection={onResetSection}
      matchedFieldCounts={matchedFieldCounts}
    />
  );
}
