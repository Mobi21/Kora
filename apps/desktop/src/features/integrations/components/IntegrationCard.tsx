import { MoreHorizontal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Pill, type PillStatus } from '@/components/ui/pill';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import type { HealthState, IntegrationStatusView } from '@/lib/api/types';
import { KindIcon } from './KindIcon';

interface IntegrationCardProps {
  integration: IntegrationStatusView;
  onRecheck: () => void;
  // Optional explanatory copy used when the integration is unconfigured.
  // If omitted, the card falls back to the generic detail text.
  unconfiguredCopy?: string;
}

export function IntegrationCard({
  integration,
  onRecheck,
  unconfiguredCopy,
}: IntegrationCardProps): JSX.Element {
  const navigate = useNavigate();
  const pillStatus = healthToPill(integration.health);
  const disabled = integration.enabled === false;
  const unconfigured = integration.health === 'unconfigured';

  const settingsHash = settingsAnchorFor(integration.kind);
  const goToSettings = () => navigate(`/settings${settingsHash}`);

  const detail = unconfigured && unconfiguredCopy ? unconfiguredCopy : integration.detail;
  const monoPath = pickPath(integration);

  return (
    <Card
      className={cn(
        'flex flex-col gap-3 p-[var(--pad)]',
        disabled && 'opacity-60',
      )}
    >
      <div className="flex items-start gap-3">
        <KindIcon kind={integration.kind} size={32} />

        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3
            className={cn(
              'font-narrative text-[var(--fs-md)] text-[var(--fg)]',
              'tracking-[var(--track-tight)]',
            )}
          >
            {integration.label}
          </h3>
          {detail && (
            <p
              className={cn(
                'text-[var(--fs-base)] text-[var(--fg-muted)]',
                unconfigured && 'font-narrative italic',
              )}
            >
              {detail}
            </p>
          )}
          {monoPath && (
            <p
              className={cn(
                'min-w-0 truncate font-mono text-[var(--fs-xs)]',
                'text-[var(--fg-subtle)] num-tabular',
              )}
              title={monoPath}
            >
              {monoPath}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {unconfigured && disabled ? (
            <Button
              variant="outline"
              size="sm"
              onClick={goToSettings}
              aria-label={`Configure ${integration.label}`}
            >
              Configure
            </Button>
          ) : unconfigured ? (
            <Button
              variant="outline"
              size="sm"
              onClick={goToSettings}
              aria-label="Open settings"
            >
              Open Settings
            </Button>
          ) : (
            <Pill status={pillStatus} label={healthLabel(integration.health)} />
          )}

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
              <DropdownMenuItem onSelect={() => goToSettings()}>
                Open settings
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <MetadataChips metadata={integration.metadata} />
    </Card>
  );
}

interface MetadataChipsProps {
  metadata: Record<string, unknown>;
}

function MetadataChips({ metadata }: MetadataChipsProps): JSX.Element | null {
  const chips = Object.entries(metadata).filter(([key]) => !PATH_KEYS.has(key));
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.map(([key, value]) => (
        <MetadataChip key={key} chipKey={key} value={value} />
      ))}
    </div>
  );
}

interface MetadataChipProps {
  chipKey: string;
  value: unknown;
}

function MetadataChip({ chipKey, value }: MetadataChipProps): JSX.Element | null {
  if (value === null || value === undefined) return null;
  const label = humanizeKey(chipKey);
  const isMono = MONO_KEYS.has(chipKey);
  const formatted = formatChipValue(value);
  if (formatted === null) return null;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-[var(--border)]',
        'bg-[var(--surface-2)] px-2 py-0.5 text-[var(--fs-2xs)] text-[var(--fg-muted)]',
      )}
    >
      <span className="uppercase tracking-[var(--track-label)] text-[var(--fg-subtle)]">
        {label}
      </span>
      <span
        className={cn(
          'text-[var(--fg)]',
          isMono ? 'font-mono num-tabular' : 'num-tabular',
        )}
        title={typeof value === 'string' ? value : undefined}
      >
        {formatted}
      </span>
    </span>
  );
}

const MONO_KEYS = new Set([
  'default_calendar_id',
  'mcp_server_name',
  'binary_path',
  'profile_path',
  'cli_path',
  'protocol_version',
  'user_email',
]);

// Keys we render as the mono path line above the chips. We hide them from
// the chip row to avoid showing the same long path twice.
const PATH_KEYS = new Set([
  'path',
  'vault_path',
  'binary_path',
  'cli_path',
  'profile_path',
]);

function pickPath(integration: IntegrationStatusView): string | null {
  const meta = integration.metadata ?? {};
  const candidates = ['path', 'vault_path', 'binary_path', 'cli_path', 'profile_path'];
  for (const key of candidates) {
    const value = meta[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ');
}

function formatChipValue(value: unknown): string | null {
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') {
    if (value.length === 0) return null;
    return value.length > 64 ? `${value.slice(0, 60)}…` : value;
  }
  return null;
}

export function healthToPill(health: HealthState): PillStatus {
  switch (health) {
    case 'ok':
      return 'ok';
    case 'degraded':
      return 'warn';
    case 'unavailable':
      return 'degraded';
    case 'unconfigured':
    default:
      return 'unknown';
  }
}

export function healthLabel(health: HealthState): string {
  switch (health) {
    case 'ok':
      return 'healthy';
    case 'degraded':
      return 'degraded';
    case 'unavailable':
      return 'unavailable';
    case 'unconfigured':
      return 'not configured';
    default:
      return health;
  }
}

function settingsAnchorFor(kind: IntegrationStatusView['kind']): string {
  switch (kind) {
    case 'workspace':
      return '#workspace';
    case 'vault':
      return '#vault';
    case 'browser':
      return '#browser';
    case 'claude_code':
      return '#claude-code';
    case 'mcp':
    default:
      return '#mcp';
  }
}
