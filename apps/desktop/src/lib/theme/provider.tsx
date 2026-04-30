import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { DensityMode, MotionMode, ThemeFamily, ThemeState } from './types';

interface ThemeContextValue extends ThemeState {
  setTheme: (theme: ThemeFamily) => void;
  setDensity: (density: DensityMode) => void;
  setMotion: (motion: MotionMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = 'kora.theme.v1';

interface PersistedTheme {
  theme: ThemeFamily;
  density: DensityMode;
  motion: MotionMode;
}

function loadPersisted(): PersistedTheme {
  if (typeof window === 'undefined') {
    return { theme: 'warm-neutral', density: 'balanced', motion: 'normal' };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return readSystemDefault();
    const parsed = JSON.parse(raw) as Partial<PersistedTheme>;
    return {
      theme: parsed.theme ?? 'warm-neutral',
      density: parsed.density ?? 'balanced',
      motion: parsed.motion ?? readSystemMotion(),
    };
  } catch {
    return readSystemDefault();
  }
}

function readSystemMotion(): MotionMode {
  if (typeof window === 'undefined') return 'normal';
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'reduced' : 'normal';
}

function readSystemDefault(): PersistedTheme {
  const dark =
    typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  return {
    theme: dark ? 'quiet-dark' : 'warm-neutral',
    density: 'balanced',
    motion: readSystemMotion(),
  };
}

function applyToDocument(state: PersistedTheme): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.theme = state.theme;
  root.dataset.density = state.density;
  root.dataset.motion = state.motion;
}

export function ThemeProvider({ children }: { children: ReactNode }): JSX.Element {
  const [state, setState] = useState<PersistedTheme>(() => loadPersisted());

  useEffect(() => {
    applyToDocument(state);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // ignore quota / private mode
    }
  }, [state]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      ...state,
      setTheme: (theme) => setState((s) => ({ ...s, theme })),
      setDensity: (density) => setState((s) => ({ ...s, density })),
      setMotion: (motion) => setState((s) => ({ ...s, motion })),
    }),
    [state],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
