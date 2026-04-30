import type { ApiClient } from '@/lib/api/client';
import type {
  DesktopSettings,
  SettingsValidationIssue,
  SettingsValidationView,
} from '@/lib/api/types';

/**
 * Run server-side validation for a `DesktopSettings` patch and return the
 * raw validation view. Caller decides what to do on `valid: false`.
 */
export async function validatePatch(
  api: ApiClient,
  patch: Partial<DesktopSettings>,
): Promise<SettingsValidationView> {
  return api.validateSettings(patch);
}

/**
 * Group issues by their dotted `path` so each form field can render its
 * own inline error/warning text without scanning the full list.
 */
export function groupIssuesByPath(
  issues: SettingsValidationIssue[] | undefined,
): Record<string, SettingsValidationIssue[]> {
  const grouped: Record<string, SettingsValidationIssue[]> = {};
  if (!issues) return grouped;
  for (const issue of issues) {
    const key = issue.path || '*';
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(issue);
  }
  return grouped;
}

export function firstErrorMessage(
  issues: SettingsValidationIssue[] | undefined,
): string | null {
  if (!issues || issues.length === 0) return null;
  const err = issues.find((i) => i.severity === 'error');
  return (err ?? issues[0]).message;
}
