import { describe, expect, it } from 'vitest'

import { returnPath } from './returnPath'

describe('returnPath', () => {
  it('goes back to the page of this interface that was asked for', () => {
    expect(returnPath('/positions')).toBe('/positions')
    expect(returnPath('/logs/wata-trader?lines=500')).toBe('/logs/wata-trader?lines=500')
    expect(returnPath('/')).toBe('/')
  })

  it('goes to the dashboard when there is nowhere to go back to', () => {
    expect(returnPath(undefined)).toBe('/')
    expect(returnPath('')).toBe('/')
    expect(returnPath({ pathname: '/positions' })).toBe('/')
  })

  it('never goes to another site', () => {
    for (const target of ['//evil.example', '/\\evil.example', 'https://evil.example', 'evil.example', 'javascript:alert(1)']) {
      expect(returnPath(target)).toBe('/')
    }
  })
})
