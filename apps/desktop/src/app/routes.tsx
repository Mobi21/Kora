import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Plug } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useConnectionStore } from '@/lib/api/connection';
import { TodayScreen } from '@/features/today/TodayScreen';
import { CalendarScreen } from '@/features/calendar/CalendarScreen';
import { MedicationScreen } from '@/features/medication/MedicationScreen';
import { RoutinesScreen } from '@/features/routines/RoutinesScreen';
import { RepairScreen } from '@/features/repair/RepairScreen';
import { MemoryScreen } from '@/features/memory/MemoryScreen';
import { AutonomousScreen } from '@/features/autonomous/AutonomousScreen';
import { IntegrationsScreen } from '@/features/integrations/IntegrationsScreen';
import { SettingsScreen } from '@/features/settings/SettingsScreen';
import { RuntimeScreen } from '@/features/runtime/RuntimeScreen';

export const FIRST_RUN_PATH = '/first-run';

/**
 * In-shell routes (sidebar + chat panel are visible).
 * `/first-run` is NOT in this list — it renders standalone in App.tsx.
 */
export function AppRoutes(): JSX.Element {
  const location = useLocation();
  const connStatus = useConnectionStore((s) => s.status);
  const connection = useConnectionStore((s) => s.connection);

  if (
    connStatus === 'error' &&
    !connection &&
    location.pathname !== '/runtime' &&
    location.pathname !== '/settings'
  ) {
    return <DisconnectedMain />;
  }

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/today" replace />} />
      <Route path="/today" element={<TodayScreen />} />
      <Route path="/calendar" element={<CalendarScreen />} />
      <Route path="/medication" element={<MedicationScreen />} />
      <Route path="/routines" element={<RoutinesScreen />} />
      <Route path="/repair" element={<RepairScreen />} />
      <Route path="/memory" element={<MemoryScreen />} />
      <Route path="/autonomous" element={<AutonomousScreen />} />
      <Route path="/integrations" element={<IntegrationsScreen />} />
      <Route path="/settings" element={<SettingsScreen />} />
      <Route path="/runtime" element={<RuntimeScreen />} />
      <Route path="*" element={<Navigate to="/today" replace />} />
    </Routes>
  );
}

function DisconnectedMain(): JSX.Element {
  const reload = useConnectionStore((s) => s.load);
  const error = useConnectionStore((s) => s.error);

  return (
    <div className="flex h-full w-full items-center justify-center px-6 py-10">
      <div style={{ maxWidth: 'var(--ws-memory)' }} className="w-full">
        <EmptyState
          icon={Plug}
          title="Daemon not reachable"
          description={
            error
              ? `Kora could not resolve the local daemon connection: ${error}`
              : 'Start the daemon, then reconnect.'
          }
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
