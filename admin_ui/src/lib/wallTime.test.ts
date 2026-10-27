import { formatDateTime } from 'ioiti-ui'
import { describe, expect, it } from 'vitest'

import { formatWallTime } from './wallTime'

describe('formatWallTime', () => {
  it('shows a time the server wrote in its own timezone as it is, in the usual form', () => {
    const today = new Date('2026-10-12T10:00:00Z')
    expect(formatWallTime('2026-10-12 15:12:00', today)).toBe('12 Oct, 15:12:00')
    expect(formatWallTime('2025-12-31 23:59:59', today)).toBe('31 Dec 2025, 23:59:59')
    expect(formatWallTime('yesterday', today)).toBe('yesterday')
    expect(formatWallTime(null, today)).toBe('—')
  })

  it('looks like the instants shown beside it', () => {
    const today = new Date('2026-10-12T10:00:00Z')
    expect(formatWallTime('2026-10-11 22:52:12', today)).toBe(formatDateTime('2026-10-11T20:52:12Z', 'Europe/Paris', true, today))
  })
})
