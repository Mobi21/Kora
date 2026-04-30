export type ThemeFamily =
  | 'warm-neutral'
  | 'quiet-dark'
  | 'low-stimulation'
  | 'high-contrast'
  | 'soft-color'
  | 'compact-focus';

export type DensityMode = 'cozy' | 'balanced' | 'compact';

export type MotionMode = 'normal' | 'reduced' | 'none';

export interface ThemeState {
  theme: ThemeFamily;
  density: DensityMode;
  motion: MotionMode;
}

export const THEME_FAMILIES: ThemeFamily[] = [
  'warm-neutral',
  'quiet-dark',
  'low-stimulation',
  'high-contrast',
  'soft-color',
  'compact-focus',
];

export const DENSITY_MODES: DensityMode[] = ['cozy', 'balanced', 'compact'];
export const MOTION_MODES: MotionMode[] = ['normal', 'reduced', 'none'];
