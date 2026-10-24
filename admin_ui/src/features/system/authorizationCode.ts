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

/**
 * The state of the link the code came back from, when an address was pasted: the server checks it is one it
 * made (src/admin/saxo.py). None for a bare code.
 */
export function extractAuthorizationState(input: string): string | null {
  const text = input.trim()
  const queryStart = text.indexOf('?')
  if (queryStart === -1 && !text.includes('state=')) return null
  const query = (queryStart !== -1 ? text.slice(queryStart + 1) : text).split('#')[0]
  return new URLSearchParams(query).get('state')?.trim() || null
}
