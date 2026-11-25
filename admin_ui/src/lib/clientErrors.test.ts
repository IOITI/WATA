import { describe, expect, it, vi } from 'vitest'

import { describeError, errorReporter, MOST_CHARACTERS, SAME_ERROR_MS } from './clientErrors'
import type { ClientErrorBody } from './types'

describe('describeError', () => {
  it('says an Error by its name, message and stack, and where it was caught', () => {
    const error = new TypeError('Cannot read properties of undefined')
    const body = describeError(error, { source: 'boundary', componentStack: '\n    at Chart\n    at Page' }, '/positions')
    expect(body).toEqual({
      source: 'boundary',
      path: '/positions',
      name: 'TypeError',
      message: 'Cannot read properties of undefined',
      stack: error.stack,
      component_stack: '\n    at Chart\n    at Page',
    })
  })

  it("says a route's answer by its status", () => {
    expect(describeError({ status: 404, statusText: 'Not Found', data: null }, { source: 'route' }, '/backtests/x'))
      .toEqual({ source: 'route', path: '/backtests/x', name: 'HTTP 404', message: 'Not Found' })
  })

  it('says anything else as text, and cuts what is too long', () => {
    const body = describeError('x'.repeat(5000), { source: 'route' }, '/')
    expect(body.name).toBe('Error')
    expect(body.message).toHaveLength(MOST_CHARACTERS.message)
    expect(body.message.endsWith('…')).toBe(true)
  })
})

describe('errorReporter', () => {
  function reporter() {
    const sent: ClientErrorBody[] = []
    let clock = 1_000
    let path = '/positions'
    const report = errorReporter(
      async (body) => {
        sent.push(body)
      },
      () => clock,
      () => path,
    )
    return {
      sent,
      report,
      later: (ms: number) => {
        clock += ms
      },
      goTo: (to: string) => {
        path = to
      },
    }
  }

  it('sends an error once a minute on the same page', () => {
    const { sent, report, later, goTo } = reporter()
    report(new Error('The chart broke'), { source: 'boundary' })
    report(new Error('The chart broke'), { source: 'boundary' })
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ path: '/positions', message: 'The chart broke' })

    goTo('/orders')
    report(new Error('The chart broke'), { source: 'boundary' })
    expect(sent).toHaveLength(2)

    later(SAME_ERROR_MS)
    report(new Error('The chart broke'), { source: 'boundary' })
    expect(sent).toHaveLength(3)
  })

  it('never throws, nor lets a refused report through', async () => {
    const send = vi.fn(async () => {
      throw new Error('401')
    })
    const report = errorReporter(send, () => 0, () => '/')
    expect(() => report(new Error('broken'), { source: 'route' })).not.toThrow()
    await Promise.resolve()
    expect(send).toHaveBeenCalledOnce()

    const failing = errorReporter(send, () => 0, () => {
      throw new Error('no window')
    })
    expect(() => failing(new Error('broken'), { source: 'route' })).not.toThrow()
  })
})
