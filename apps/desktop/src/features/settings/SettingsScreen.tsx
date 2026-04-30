import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Plug, Settings as SettingsIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import type {
  DesktopSettings,
  SettingsValidationIssue,
} from '@/lib/api/types';
import { useConnectionStore } from '@/lib/api/connection';
import { SettingsLayout } from './SettingsLayout';
import { SettingsRail, SETTINGS_SECTIONS } from './SettingsRail';
import {
  useInspectSetupForSettings,
  useIntegrationsForSettings,
  useSectionSave,
  useSettingsQuery,
  useStatusForSettings,
} from './queries';
import { ThemeAndDisplaySection } from './sections/ThemeAndDisplaySection';
import { AppLayoutSection } from './sections/AppLayoutSection';
import { CalendarLayersSection } from './sections/CalendarLayersSection';
import { LLMSection } from './sections/LLMSection';
import { MemorySection } from './sections/MemorySection';
import { AgentsSection } from './sections/AgentsSection';
import { QualitySection } from './sections/QualitySection';
import { DaemonSection } from './sections/DaemonSection';
import { NotificationsSection } from './sections/NotificationsSection';
import { PlanningSection } from './sections/PlanningSection';
import { AutonomousSection } from './sections/AutonomousSection';
import { OrchestrationSection } from './sections/OrchestrationSection';
import { MCPSection } from './sections/MCPSection';
import { SecuritySection } from './sections/SecuritySection';
import { BrowserSection } from './sections/BrowserSection';
import { VaultSection } from './sections/VaultSection';
import { WorkspaceSection } from './sections/WorkspaceSection';
import { UserTimezoneSection } from './sections/UserTimezoneSection';
import { BackupsSection } from './sections/BackupsSection';
import { Skeleton } from '@/components/ui/skeleton';
import { diffSettings } from './utils/diff';

const THEME_KEYS: (keyof DesktopSettings)[] = [
  'theme_family',
  'accent_color',
  'density',
  'motion',
  'support_mode_visuals',
  'command_bar_behavior',
];
const LAYOUT_KEYS: (keyof DesktopSettings)[] = [
  'chat_panel_default_open',
  'chat_panel_width',
  'today_module_order',
  'calendar_default_view',
  'timeline_position',
];
const CALENDAR_LAYER_KEYS: (keyof DesktopSettings)[] = ['calendar_layers'];

type Scope = 'theme' | 'layout' | 'calendar-layers';

const SCOPE_KEYS: Record<Scope, (keyof DesktopSettings)[]> = {
  theme: THEME_KEYS,
  layout: LAYOUT_KEYS,
  'calendar-layers': CALENDAR_LAYER_KEYS,
};

const SECTION_FIELD_KEYWORDS: Record<string, string[]> = {
  theme: [
    'theme family warm-neutral quiet-dark low-stimulation high-contrast soft-color compact-focus',
    'accent color sage terracotta slate plum amber',
    'density cozy balanced compact',
    'motion normal reduced none',
    'support mode visuals adhd autism',
    'command bar behavior screen-aware always-global minimal',
  ],
  layout: [
    'chat panel default open',
    'chat panel width pixels',
    'today module order now next later timeline reality_check load',
    'calendar default view day week month agenda',
    'timeline position left right',
  ],
  'calendar-layers': ['calendar layers visibility google meds routines reminders protected'],
  llm: ['provider model background api_base api key timeout max tokens minimax'],
  memory: ['memory root embedding model dims hybrid weights dedup threshold projection signal scanner'],
  agents: ['iteration budget timeout loop detection reviewer sampling thinking planner'],
  quality: ['confidence threshold regression window sampling llm judge'],
  daemon: ['host port 127.0.0.1 idle interval background ownership'],
  notifications: ['enabled cooldown dnd hyperfocus max per hour re-engagement'],
  planning: ['daily weekly monthly cadence reflection planning time'],
  autonomous: [
    'enabled daily cost session checkpoint decision timeout request limit token warning concurrent overlap',
  ],
  orchestration: ['trigger evaluator tick interval'],
  mcp: ['mcp servers tools startup timeout enabled health'],
  security: ['cors token injection scan auth mode'],
  browser: ['browser binary profile clip target session timeout'],
  vault: ['vault obsidian path memory root health'],
  workspace: ['workspace google email calendar tool map mcp server account'],
  timezone: ['user timezone tz iana offset display'],
  backups: ['data directory memory root settings.toml reveal'],
};

function DisconnectedView(): JSX.Element {
  const reload = useConnectionStore((s) => s.load);
  return (
    <div className="flex h-full w-full items-center justify-center px-6 py-10">
      <div style={{ maxWidth: 'var(--ws-memory)' }} className="w-full">
        <EmptyState
          icon={Plug}
          title="Daemon not reachable"
          description="The desktop app couldn't find a running Kora daemon. Start the daemon and try again."
          action={
            <Button onClick={() => void reload()} aria-label="Reconnect to daemon">
              Reconnect
            </Button>
          }
        />
      </div>
    </div>
  );
}

function LoadingScreen(): JSX.Element {
  return (
    <div className="flex h-full w-full overflow-hidden">
      <div className="hidden w-[220px] shrink-0 flex-col gap-2 border-r border-[var(--border)] bg-[var(--bg)] px-6 py-6 md:flex">
        <Skeleton className="h-8 w-full" />
        {Array.from({ length: 12 }).map((_, i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-10 py-10">
        <div className="mx-auto flex max-w-[760px] flex-col gap-6">
          <Skeleton className="h-10 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
          <div className="flex flex-col gap-3 pt-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function SettingsScreen(): JSX.Element {
  const connection = useConnectionStore((s) => s.connection);
  const connStatus = useConnectionStore((s) => s.status);
  const loadConnection = useConnectionStore((s) => s.load);

  useEffect(() => {
    if (connStatus === 'idle') void loadConnection();
  }, [connStatus, loadConnection]);

  const settingsQuery = useSettingsQuery();
  const setupQuery = useInspectSetupForSettings();
  const statusQuery = useStatusForSettings();
  const integrationsQuery = useIntegrationsForSettings();

  const baseline = settingsQuery.data ?? null;
  const [draft, setDraft] = useState<DesktopSettings | null>(baseline);
  const [issues, setIssues] = useState<SettingsValidationIssue[]>([]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savingScope, setSavingScope] = useState<Scope | null>(null);

  // Reset draft to baseline whenever the server's snapshot changes.
  useEffect(() => {
    if (baseline) setDraft((prev) => prev ?? baseline);
  }, [baseline]);

  // If the server PATCH ever returns a different baseline, replace the
  // draft only for fields the user hasn't touched. The simple heuristic:
  // if the draft equals the previous baseline, take the new baseline.
  const lastBaselineRef = useRef<DesktopSettings | null>(null);
  useEffect(() => {
    if (!baseline) return;
    if (
      lastBaselineRef.current &&
      draft &&
      JSON.stringify(diffSettings(lastBaselineRef.current, draft)) === '{}'
    ) {
      setDraft(baseline);
    }
    lastBaselineRef.current = baseline;
  }, [baseline, draft]);

  const saveMutation = useSectionSave();

  const patchDraft = useCallback((patch: Partial<DesktopSettings>) => {
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const discardScope = useCallback(
    (scope: Scope) => {
      if (!baseline || !draft) return;
      const keys = SCOPE_KEYS[scope];
      const reset: Partial<DesktopSettings> = {};
      for (const k of keys) {
        (reset as Record<string, unknown>)[k as string] = baseline[k];
      }
      setDraft({ ...draft, ...reset });
      setIssues((prev) => prev.filter((i) => !keys.some((k) => i.path.startsWith(String(k)))));
      setSaveError(null);
    },
    [baseline, draft],
  );

  const saveScope = useCallback(
    async (scope: Scope) => {
      if (!baseline || !draft) return;
      const keys = SCOPE_KEYS[scope];
      const fullDiff = diffSettings(baseline, draft);
      const scopedPatch: Partial<DesktopSettings> = {};
      for (const k of keys) {
        if (k in fullDiff) {
          (scopedPatch as Record<string, unknown>)[k as string] = (
            fullDiff as Record<string, unknown>
          )[k as string];
        }
      }
      if (Object.keys(scopedPatch).length === 0) return;

      setSavingScope(scope);
      setSaveError(null);
      try {
        const result = await saveMutation.mutateAsync({ patch: scopedPatch });
        setIssues(result.validation.issues);
        if (!result.validation.valid) {
          setSaveError('Validation failed. See field errors below.');
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Save failed.';
        setSaveError(message);
        setIssues([]);
      } finally {
        setSavingScope(null);
      }
    },
    [baseline, draft, saveMutation],
  );

  // ── Search & active section ────────────────────────────────────────
  const [query, setQuery] = useState('');
  const navigate = useNavigate();
  const location = useLocation();

  const initialActive = useMemo(() => {
    const hash = location.hash.replace('#', '');
    return SETTINGS_SECTIONS.find((s) => s.id === hash)?.id ?? 'theme';
  }, [location.hash]);
  const [activeId, setActiveId] = useState<string>(initialActive);

  useEffect(() => {
    const hash = location.hash.replace('#', '');
    if (!hash) return;
    if (SETTINGS_SECTIONS.some((s) => s.id === hash)) {
      setActiveId(hash);
      const el = document.getElementById(hash);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }, [location.hash]);

  const handleSelect = useCallback(
    (id: string) => {
      setActiveId(id);
      navigate({ pathname: '/settings', hash: id }, { replace: true });
      const el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    [navigate],
  );

  // Resolve which section is currently in view as the user scrolls. We
  // observe against the browser viewport (root: null) — the ScrollArea
  // viewport is itself scrolled inside that, so sections still cross the
  // observed boundaries as they enter/leave the user's view.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) {
          setActiveId(visible[0].target.id);
        }
      },
      { rootMargin: '-20% 0px -70% 0px', threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    SETTINGS_SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  const matchedFieldCounts = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return undefined;
    const counts: Record<string, number> = {};
    for (const sectionId of Object.keys(SECTION_FIELD_KEYWORDS)) {
      const blob = SECTION_FIELD_KEYWORDS[sectionId].join(' ').toLowerCase();
      counts[sectionId] = blob.includes(q) ? 1 : 0;
    }
    return counts;
  }, [query]);

  const highlightSet = useMemo<ReadonlySet<string>>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return new Set<string>();
    const set = new Set<string>();
    for (const sectionId of Object.keys(SECTION_FIELD_KEYWORDS)) {
      const lines = SECTION_FIELD_KEYWORDS[sectionId];
      for (const line of lines) {
        if (line.toLowerCase().includes(q)) {
          // Mark all field-name tokens for this section. We use
          // section-prefixed names matching the `hl()` helpers below.
          const tokens = line.split(' ').filter((t) => /^[a-z0-9_]+$/.test(t));
          for (const tok of tokens) set.add(`${sectionId}.${tok}`);
        }
      }
    }
    return set;
  }, [query]);

  if (connStatus === 'error' || (connStatus === 'ready' && !connection)) {
    return <DisconnectedView />;
  }

  if (settingsQuery.isLoading || !baseline || !draft) {
    if (settingsQuery.isError) {
      return (
        <div className="flex h-full w-full items-center justify-center px-6 py-10">
          <div style={{ maxWidth: 'var(--ws-memory)' }} className="w-full">
            <EmptyState
              icon={SettingsIcon}
              title="Settings aren't available"
              description="The daemon couldn't return the desktop settings snapshot. A reconnect or restart often fixes this."
              action={
                <Button onClick={() => void settingsQuery.refetch()}>
                  Retry
                </Button>
              }
            />
          </div>
        </div>
      );
    }
    return <LoadingScreen />;
  }

  const setupReport = setupQuery.data ?? null;
  const status = statusQuery.data ?? null;
  const integrations = integrationsQuery.data ?? null;

  return (
    <SettingsLayout
      rail={
        <SettingsRail
          activeId={activeId}
          query={query}
          onQueryChange={setQuery}
          onSelect={handleSelect}
          matchedFieldCounts={matchedFieldCounts}
          onResetSection={
            activeId === 'theme' || activeId === 'layout' || activeId === 'calendar-layers'
              ? () => discardScope(activeId)
              : undefined
          }
        />
      }
    >
      <div className="contents">
        <ThemeAndDisplaySection
          baseline={baseline}
          draft={draft}
          onPatch={patchDraft}
          onSave={() => void saveScope('theme')}
          onDiscard={() => discardScope('theme')}
          saving={savingScope === 'theme'}
          saveError={savingScope === null && activeId === 'theme' ? saveError : null}
          issues={issues.filter((i) => THEME_KEYS.some((k) => i.path.startsWith(String(k))))}
          highlightFields={highlightSet}
        />

        <AppLayoutSection
          baseline={baseline}
          draft={draft}
          onPatch={patchDraft}
          onSave={() => void saveScope('layout')}
          onDiscard={() => discardScope('layout')}
          saving={savingScope === 'layout'}
          saveError={savingScope === null && activeId === 'layout' ? saveError : null}
          issues={issues.filter((i) => LAYOUT_KEYS.some((k) => i.path.startsWith(String(k))))}
          highlightFields={highlightSet}
        />

        <CalendarLayersSection
          baseline={baseline}
          draft={draft}
          onPatch={patchDraft}
          onSave={() => void saveScope('calendar-layers')}
          onDiscard={() => discardScope('calendar-layers')}
          saving={savingScope === 'calendar-layers'}
          saveError={
            savingScope === null && activeId === 'calendar-layers' ? saveError : null
          }
          issues={issues.filter((i) => i.path.startsWith('calendar_layers'))}
          highlightFields={highlightSet}
        />

        <LLMSection
          setup={setupReport}
          loading={setupQuery.isLoading}
          highlightFields={highlightSet}
        />
        <MemorySection
          setup={setupReport}
          loading={setupQuery.isLoading}
          highlightFields={highlightSet}
        />
        <AgentsSection highlightFields={highlightSet} />
        <QualitySection highlightFields={highlightSet} />
        <DaemonSection
          setup={setupReport}
          status={status}
          loading={setupQuery.isLoading}
          highlightFields={highlightSet}
        />
        <NotificationsSection highlightFields={highlightSet} />
        <PlanningSection highlightFields={highlightSet} />
        <AutonomousSection highlightFields={highlightSet} />
        <OrchestrationSection highlightFields={highlightSet} />
        <MCPSection
          integrations={integrations}
          loading={integrationsQuery.isLoading}
          highlightFields={highlightSet}
        />
        <SecuritySection
          setup={setupReport}
          loading={setupQuery.isLoading}
          highlightFields={highlightSet}
        />
        <BrowserSection
          integrations={integrations}
          loading={integrationsQuery.isLoading}
          highlightFields={highlightSet}
        />
        <VaultSection
          status={status}
          loading={statusQuery.isLoading}
          highlightFields={highlightSet}
        />
        <WorkspaceSection
          integrations={integrations}
          loading={integrationsQuery.isLoading}
          highlightFields={highlightSet}
        />
        <UserTimezoneSection highlightFields={highlightSet} />
        <BackupsSection
          setup={setupReport}
          loading={setupQuery.isLoading}
          highlightFields={highlightSet}
        />
      </div>
    </SettingsLayout>
  );
}

export default SettingsScreen;
