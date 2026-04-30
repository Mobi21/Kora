import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { tomorrowIsoDate } from '../queries';

interface TomorrowBridgeFooterProps {
  className?: string;
}

export function TomorrowBridgeFooter({ className }: TomorrowBridgeFooterProps): JSX.Element {
  const navigate = useNavigate();
  const tomorrow = tomorrowIsoDate();

  return (
    <div
      className={cn(
        'flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-between',
        'pt-1 text-[var(--fg-muted)]',
        className,
      )}
    >
      <p className="font-narrative italic text-[var(--fs-md)]">
        Tomorrow's prep is staged in your calendar.
      </p>
      <Button
        variant="outline"
        size="sm"
        onClick={() => navigate(`/calendar?date=${tomorrow}`)}
      >
        Plan tomorrow
      </Button>
    </div>
  );
}
