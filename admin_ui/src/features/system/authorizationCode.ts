/**
 * The Saxo authorization code from what the operator pasted: either the code
 * itself, or the address Saxo redirected to (`https://…/?code=…&state=…`).
 */
export function extractAuthorizationCode(input: string): string {
  const text = input.trim()
  if (!text) return ''

  const isAddress = /^https?:\/\//i.test(text)
  if (isAddress || text.includes('code=')) {
    const queryStart = text.indexOf('?')
    const query = (queryStart !== -1 ? text.slice(queryStart + 1) : text).split('#')[0]
    const code = new URLSearchParams(query).get('code')?.trim()
    if (code) return code
    // An address without a code is not a code.
    if (isAddress) return ''
  }
  return text
}
