/** Returns the url only when it is http or https, so API data can never produce a javascript: link. */
export function safeUrl(raw: string | undefined | null): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw, 'https://placeholder.invalid');
    return u.protocol === 'http:' || u.protocol === 'https:' ? raw : null;
  } catch {
    return null;
  }
}
