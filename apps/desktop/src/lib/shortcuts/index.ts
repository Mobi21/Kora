import { useEffect } from 'react';
import { create } from 'zustand';
import type { NavigateFunction } from 'react-router-dom';
import { useChatStore } from '../ws/store';

interface CommandPaletteState {
  open: boolean;
  setOpen: (v: boolean) => void;
  toggle: () => void;
}

export const useCommandPaletteStore = create<CommandPaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  toggle: () => set((s) => ({ open: !s.open })),
}));

const NAV_ORDER = [
  '/today',
  '/calendar',
  '/medication',
  '/routines',
  '/repair',
  '/memory',
  '/autonomous',
  '/integrations',
  '/settings',
] as const;

export function useGlobalShortcuts(navigate: NavigateFunction): void {
  const togglePalette = useCommandPaletteStore((s) => s.toggle);
  const toggleChatPanel = useChatStore((s) => s.togglePanel);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;
      if (!isMeta) return;
      if (e.key === 'k' || e.key === 'K') {
        e.preventDefault();
        togglePalette();
        return;
      }
      if (e.key === '/') {
        e.preventDefault();
        toggleChatPanel();
        return;
      }
      if (/^[1-9]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1;
        const target = NAV_ORDER[idx];
        if (target) {
          e.preventDefault();
          navigate(target);
        }
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, togglePalette, toggleChatPanel]);
}
