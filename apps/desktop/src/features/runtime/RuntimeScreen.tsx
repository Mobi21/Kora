import { Plug } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useConnectionStore } from '@/lib/api/connection';
import { RuntimeHeader } from './components/RuntimeHeader';
import { StatusTab } from './components/StatusTab';
import { DoctorTab } from './components/DoctorTab';
import { SetupTab } from './components/SetupTab';
import { PermissionsTab } from './components/PermissionsTab';

const TABS = [
  { id: 'status', label: 'Status' },
  { id: 'doctor', label: 'Doctor' },
  { id: 'setup', label: 'Setup' },
  { id: 'permissions', label: 'Permissions' },
] as const;

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

export function RuntimeScreen(): JSX.Element {
  const connStatus = useConnectionStore((s) => s.status);
  const connection = useConnectionStore((s) => s.connection);
  const loadConnection = useConnectionStore((s) => s.load);
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]['id']>('status');

  useEffect(() => {
    if (connStatus === 'idle') {
      void loadConnection();
    }
  }, [connStatus, loadConnection]);

  if (connStatus === 'error' || (connStatus === 'ready' && !connection)) {
    return <DisconnectedView />;
  }

  return (
    <TooltipProvider>
      <div className="flex h-full w-full justify-center overflow-y-auto px-6 py-8 md:px-8 md:py-10">
        <div
          className="flex w-full flex-col gap-6"
          style={{ maxWidth: '960px' }}
        >
          <RuntimeHeader />

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as typeof activeTab)}
            className="flex flex-col gap-5"
          >
            <TabsList className="w-full justify-start gap-2">
              {TABS.map((t) => (
                <TabsTrigger key={t.id} value={t.id}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>

            <TabsContent value="status">
              {activeTab === 'status' && <StatusTab />}
            </TabsContent>
            <TabsContent value="doctor">
              {activeTab === 'doctor' && <DoctorTab />}
            </TabsContent>
            <TabsContent value="setup">
              {activeTab === 'setup' && <SetupTab />}
            </TabsContent>
            <TabsContent value="permissions">
              {activeTab === 'permissions' && <PermissionsTab />}
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </TooltipProvider>
  );
}

export default RuntimeScreen;
