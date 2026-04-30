/* Thin re-exports + view-specific composition for the Calendar screen.
   The underlying hooks are defined once in `@/lib/api/queries`. */
export {
  useCalendar,
  useCalendarPreview,
  useCalendarApply,
  useMedication,
} from '@/lib/api/queries';
