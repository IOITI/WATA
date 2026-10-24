import { describe, expect, it } from 'vitest'

import { extractAuthorizationCode, extractAuthorizationState } from './authorizationCode'

describe('extractAuthorizationCode', () => {
  it('takes the code from the address Saxo redirected to', () => {
    expect(extractAuthorizationCode('https://localhost/?code=abc-123&state=f00')).toBe('abc-123')
    expect(extractAuthorizationCode('  https://localhost/redirect?state=f00&code=abc%2D123#top  ')).toBe('abc-123')
  })

  it('takes the code from a pasted query string', () => {
    expect(extractAuthorizationCode('code=abc-123&state=f00')).toBe('abc-123')
  })

  it('keeps a bare code as it is', () => {
    expect(extractAuthorizationCode(' abc-123 ')).toBe('abc-123')
  })

  it('finds nothing in an address without a code, or in nothing', () => {
    expect(extractAuthorizationCode('https://localhost/?error=access_denied')).toBe('')
    expect(extractAuthorizationCode('https://localhost')).toBe('')
    expect(extractAuthorizationCode('   ')).toBe('')
  })
})

describe('the state of the link', () => {
  it('is taken from the address pasted, and is none for a bare code', () => {
    expect(extractAuthorizationState('https://admin.example.com/api/saxo/callback?code=abc&state=xyz')).toBe('xyz')
    expect(extractAuthorizationState('code=abc&state=xyz')).toBe('xyz')
    expect(extractAuthorizationState('abc-123')).toBeNull()
    expect(extractAuthorizationState('https://localhost/?code=abc')).toBeNull()
  })
})
