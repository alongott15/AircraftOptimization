import { he } from './he';

type Leaves<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${P}${K}` : Leaves<T[K], `${P}${K}.`>;
}[keyof T & string];

export type MessageKey = Leaves<typeof he>;
export type Params = Record<string, string | number | null | undefined>;

export function interpolate(template: string, params?: Params): string {
  return template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const v = params?.[name];
    return v === null || v === undefined ? '' : String(v);
  });
}

function lookup(key: string): string | undefined {
  let node: unknown = he;
  for (const part of key.split('.')) {
    if (node && typeof node === 'object' && part in node) {
      node = (node as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return typeof node === 'string' ? node : undefined;
}

/** Type-checked translation. */
export function t(key: MessageKey, params?: Params): string {
  return interpolate(lookup(key) ?? key, params);
}

/** Dynamic key (e.g. `status.${code}`); returns `fallback` (or the key) when missing. */
export function tk(key: string, params?: Params, fallback?: string): string {
  const value = lookup(key);
  return value === undefined ? (fallback ?? key) : interpolate(value, params);
}

export function hasKey(key: string): boolean {
  return lookup(key) !== undefined;
}
