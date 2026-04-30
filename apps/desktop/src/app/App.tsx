import { useEffect, useRef, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { AppShell } from '@/components/shell/app-shell';
import { CommandPalette } from '@/components/shell/command-palette';
import { useGlobalShortcuts } from '@/lib/shortcuts';
import { useConnectionStore } from '@/lib/api/connection';
import { FirstRunScreen } from '@/features/first-run/FirstRunScreen';
import {
  pingDaemonOnce,
  readFirstRunCompleted,
} from '@/features/first-run/queries';
import { AppRoutes, FIRST_RUN_PATH } from './routes';

type GateDecision = 'undetermined' | 'first-run' | 'app';

/**
 * Decide whether to show the first-run wizard or the regular app shell.
 *
 * Reasons we route to /first-run:
 *   1. The user has never marked first-run complete (localStorage flag).
 *   2. The Electron bridge cannot reach the daemon within 3 seconds.
 *   3. The connection store reports a hard error (no token / no lockfile).
 *
 * Once the wizard finishes, it sets the flag + navigates to /today.
 */
function useFirstRunGate(): GateDecision {
  const navigate = useNavigate();
  const location = useLocation();
  const connStatus = useConnectionStore((s) => s.status);
  const [decision, setDecision] = useState<GateDecision>('undetermined');
  const settledRef = useRef(false);

  useEffect(() => {
    // Once we've decided the app is healthy, never re-probe — navigation
    // around the app should not trigger more daemon probes. The wizard's
    // navigate('/today') resets the decision via state change naturally,
    // because the user reloads the app context after first-run completes.
    if (settledRef.current && decision === 'app') return;

    let cancelled = false;

    function routeToFirstRun(): void {
      if (cancelled) return;
      setDecision('first-run');
      if (location.pathname !== FIRST_RUN_PATH) {
        navigate(FIRST_RUN_PATH, { replace: true });
      }
    }

    async function evaluate(): Promise<void> {
      // localStorage is the cheapest signal — check first. If the user has
      // never finished the wizard, we always force them through it.
      if (!readFirstRunCompleted()) {
        // Do an opportunistic 3s probe so a returning-but-not-flagged user
        // (e.g. cleared localStorage) gets past the wizard automatically if
        // the daemon is already healthy. Otherwise, route to the wizard.
        if (connStatus === 'loading' || connStatus === 'idle') return;
        const ok = connStatus === 'ready' ? await pingDaemonOnce(3000) : false;
        if (cancelled) return;
        if (!ok) {
          routeToFirstRun();
          return;
        }
        // Daemon is fine — silently mark first-run done so we don't keep
        // bouncing the user.
        settledRef.current = true;
        setDecision('app');
        return;
      }

      // User has finished the wizard. Respect their choice to "do it later":
      // we don't force them back into /first-run just because the daemon is
      // currently offline. The runtime indicator will surface that state.
      // We only block app shell entry while the connection store is still
      // loading on cold boot.
      if (connStatus === 'loading' || connStatus === 'idle') return;

      settledRef.current = true;
      setDecision('app');
    }

    void evaluate();
    return () => {
      cancelled = true;
    };
  }, [connStatus, location.pathname, navigate, decision]);

  return decision;
}

export function App(): JSX.Element {
  const navigate = useNavigate();
  useGlobalShortcuts(navigate);
  const decision = useFirstRunGate();

  return (
    <Routes>
      <Route path={FIRST_RUN_PATH} element={<FirstRunScreen />} />
      <Route
        path="*"
        element={
          decision === 'undetermined' ? (
            <BootSplash />
          ) : (
            <>
              <AppShell>
                <AppRoutes />
              </AppShell>
              <CommandPalette />
            </>
          )
        }
      />
    </Routes>
  );
}

/**
 * Soft loading state shown for the brief window between mount and the gate
 * deciding which surface to render. Kept intentionally quiet — no spinner —
 * because in practice it lasts a few hundred ms.
 */
function BootSplash(): JSX.Element {
  return (
    <div
      className="flex h-screen w-screen items-center justify-center bg-[var(--bg)]"
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Starting Kora…</span>
    </div>
  );
}
