/**
 * Where to go after signing in: the page that was asked for, as long as it is a
 * path of this interface. "//host" and "/\host" are addresses of another site.
 */
export function returnPath(from: unknown): string {
  return typeof from === 'string' && /^\/(?![/\\])/.test(from) ? from : '/'
}
