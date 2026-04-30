import { dialog, type BrowserWindow } from 'electron';

export interface OpenDirectoryOptions {
  title?: string;
  defaultPath?: string;
  buttonLabel?: string;
}

/**
 * Wrap Electron's native directory picker. Always returns a single absolute
 * path, or null when the user cancels. Callers must validate the resulting
 * path before persisting it.
 */
export async function openDirectoryDialog(
  parent: BrowserWindow | null,
  opts: OpenDirectoryOptions = {},
): Promise<string | null> {
  const result = parent
    ? await dialog.showOpenDialog(parent, {
        properties: ['openDirectory', 'createDirectory'],
        title: opts.title ?? 'Choose folder',
        defaultPath: opts.defaultPath,
        buttonLabel: opts.buttonLabel ?? 'Select',
      })
    : await dialog.showOpenDialog({
        properties: ['openDirectory', 'createDirectory'],
        title: opts.title ?? 'Choose folder',
        defaultPath: opts.defaultPath,
        buttonLabel: opts.buttonLabel ?? 'Select',
      });

  if (result.canceled) return null;
  const [picked] = result.filePaths;
  return picked ?? null;
}
