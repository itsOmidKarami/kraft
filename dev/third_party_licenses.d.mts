export interface PackageNotice {
  name: string;
  version: string;
  license: string;
  text: string;
}
export function packageDir(id: string): string | null;
export function packageNotice(dir: string, options?: { before?: string }): PackageNotice;
export function thirdPartyLicenses(moduleIds: Iterable<string>, product: string, extra?: PackageNotice[]): string;
