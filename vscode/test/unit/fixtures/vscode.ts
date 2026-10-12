/** The few parts of the `vscode` module that `src/board.ts` and `src/gates.ts` touch, for unit
 *  tests: vitest.config.ts aliases `vscode` here. A test replaces what it
 *  watches (`window.createTreeView`) with `vi.spyOn`. */

export class EventEmitter<T> {
  private listeners = new Set<(v: T) => void>();
  event = (cb: (v: T) => void) => (this.listeners.add(cb), { dispose: () => this.listeners.delete(cb) });
  fire = (v: T) => this.listeners.forEach((cb) => cb(v));
}

export enum TreeItemCollapsibleState { None = 0, Collapsed = 1, Expanded = 2 }

export class TreeItem {
  id?: string;
  description?: string;
  iconPath?: unknown;
  contextValue?: string;
  tooltip?: string;
  command?: unknown;
  constructor(public label: string, public collapsibleState = TreeItemCollapsibleState.None) {}
}

export class ThemeIcon {
  constructor(public id: string) {}
}

export const commands = {
  executeCommand: async (..._a: unknown[]) => undefined,
  getCommands: async (_filterInternal?: boolean): Promise<string[]> => [],
  registerCommand: (_id: string, _run: unknown) => ({ dispose() {} }),
};

export const window = {
  createTreeView: (_id: string, _opts: unknown): unknown => ({ reveal: async () => {}, onDidExpandElement: () => ({ dispose() {} }), dispose() {} }),
  showInformationMessage: async (..._a: unknown[]) => undefined,
  showWarningMessage: async (..._a: unknown[]) => undefined,
  showErrorMessage: async (..._a: unknown[]) => undefined,
  showInputBox: async (..._a: unknown[]): Promise<string | undefined> => undefined,
  createTerminal: () => ({ sendText() {}, show() {} }),
  createQuickPick: (): unknown => ({}),
  showQuickPick: async (..._a: unknown[]): Promise<unknown> => undefined,
  showTextDocument: async (..._a: unknown[]) => undefined,
};

export const workspace = {
  getConfiguration: (_section?: string) => ({ get: <T>(_key: string, fallback?: T) => fallback }),
  registerTextDocumentContentProvider: (_scheme: string, _provider: unknown) => ({ dispose() {} }),
  openTextDocument: async (uri: unknown) => ({ uri }),
};

export const env = { openExternal: async () => true };
export const Uri = {
  parse: (s: string) => ({ toString: () => s }),
  file: (s: string) => ({ fsPath: s }),
  from: (c: { scheme: string; path: string }) => ({ ...c, toString: () => `${c.scheme}:${c.path}` }),
};
