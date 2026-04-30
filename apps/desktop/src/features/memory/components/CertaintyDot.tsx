import { cn } from '@/lib/utils';
import { certaintyVisual, type Certainty } from '../utils/format';

interface CertaintyDotProps {
  certainty: Certainty | string;
  size?: number;
  className?: string;
  ariaLabel?: string;
}

/**
 * The 6px provenance dot used to indicate a memory's certainty band.
 * Pairs with a 4px certainty-color left rule on the parent row.
 */
export function CertaintyDot({
  certainty,
  size = 6,
  className,
  ariaLabel,
}: CertaintyDotProps): JSX.Element {
  const visual = certaintyVisual(certainty);
  return (
    <span
      role="img"
      aria-label={ariaLabel ?? `${visual.label} certainty`}
      className={cn('inline-block shrink-0 rounded-full align-middle', className)}
      style={{ width: size, height: size, background: visual.color }}
    />
  );
}
