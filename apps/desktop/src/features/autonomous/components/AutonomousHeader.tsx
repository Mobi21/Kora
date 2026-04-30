import { Switch } from '@/components/ui/switch';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface AutonomousHeaderProps {
  enabled: boolean;
}

/**
 * Header for the Autonomous Work screen.
 *
 * The right-hand switch *mirrors* the daemon's `view.enabled` flag — it's
 * intentionally read-only because there's no toggle endpoint exposed yet.
 * The tooltip routes the user to Settings, which is the one surface that
 * can flip the flag today.
 */
export function AutonomousHeader({ enabled }: AutonomousHeaderProps): JSX.Element {
  return (
    <header className="flex items-start justify-between gap-6">
      <div className="flex flex-col gap-1.5">
        <h1
          className="font-narrative tracking-[var(--track-tight)] text-[var(--fg)]"
          style={{ fontSize: '1.875rem', lineHeight: 1.15 }}
        >
          Autonomous
        </h1>
        <p
          className="font-narrative italic text-[var(--fg-muted)]"
          style={{ fontSize: '1rem', lineHeight: 1.5 }}
        >
          Plans Kora is running on your behalf.
        </p>
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <label
            className="flex cursor-help select-none items-center gap-3 pt-1.5 text-[var(--fs-sm)] text-[var(--fg-muted)]"
            aria-label="Autonomous on/off (read-only — toggle via Settings)"
          >
            <span className="num-tabular">Autonomous on/off</span>
            <Switch
              checked={enabled}
              disabled
              aria-readonly
              aria-label={`Autonomous is ${enabled ? 'enabled' : 'disabled'} — toggle via Settings`}
            />
          </label>
        </TooltipTrigger>
        <TooltipContent>Toggle autonomous via Settings</TooltipContent>
      </Tooltip>
    </header>
  );
}
