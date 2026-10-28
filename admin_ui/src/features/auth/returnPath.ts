/**
 * Where to go after signing in: the page that was asked for, as long as it is a
 * path of this interface. "//host" and "/\host" are addresses of another site.
 */
export function returnPath(from: unknown): string {
  return typeof from === 'string' && /^\/(?![/\\])/.test(from) ? from : '/'
}

/**
 * The sign-in page, where a session that ended sends every page: a page with changes not saved lets it go
 * there without asking, since staying would save nothing.
 */
export function isSignIn(pathname: string): boolean {
  return pathname === '/login'
}
