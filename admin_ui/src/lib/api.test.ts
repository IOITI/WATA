import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, errorMessage, setUnauthorizedListener } from './api'

function respond(status: number, body?: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function mockFetch(response: Response | Error) {
  const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => {
    if (response instanceof Error) throw response
    return response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedListener(null)
})

describe('errorMessage', () => {
  it('reads the detail of a FastAPI error', () => {
    expect(errorMessage({ detail: 'Invalid password' }, 'fallback')).toBe('Invalid password')
  })

  it('joins the field errors of a malformed body', () => {
    const body = { detail: [{ msg: 'Field required' }, { msg: 'Input should be less than or equal to 2' }] }
    expect(errorMessage(body, 'fallback')).toBe('Field required; Input should be less than or equal to 2')
  })

  it('falls back when there is nothing usable', () => {
    expect(errorMessage(null, 'fallback')).toBe('fallback')
    expect(errorMessage({ detail: [] }, 'fallback')).toBe('fallback')
  })
})

describe('requests', () => {
  it('sends JSON with the session cookie and returns the parsed answer', async () => {
    const fetchMock = mockFetch(respond(200, { signal_uuid: 'abc', signal: { action: 'long' } }))

    const sent = await api.sendSignal({ action: 'long', indice: 'us100' })

    expect(sent.signal_uuid).toBe('abc')
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/actions/signal')
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('same-origin')
    expect(JSON.parse(init?.body as string)).toEqual({ action: 'long', indice: 'us100' })
  })

  it('leaves empty filters out of the query string', async () => {
    const fetchMock = mockFetch(respond(200, { items: [], total: 0 }))

    await api.positions({ status: 'closed', from: '2026-10-01', to: null })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/positions?status=closed&from=2026-10-01&limit=2000')
  })

  it('accepts an empty 204 answer', async () => {
    mockFetch(respond(204))

    await expect(api.logout()).resolves.toBeUndefined()
  })

  it('raises the server message with its status', async () => {
    mockFetch(respond(422, { detail: "Indice 'de40' is not in the allowed indices" }))

    await expect(api.sendSignal({ action: 'long', indice: 'de40' })).rejects.toMatchObject({
      status: 422,
      message: "Indice 'de40' is not in the allowed indices",
    })
  })

  it('reports an unreachable server as such', async () => {
    mockFetch(new TypeError('Failed to fetch'))

    await expect(api.overview()).rejects.toMatchObject({ status: 0, message: 'The admin server cannot be reached' })
  })

  it('carries the lockout delay of a throttled login', async () => {
    mockFetch(respond(429, { detail: 'Too many failed attempts' }, { 'Retry-After': '840' }))

    const error = await api.login('wrong').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).retryAfter).toBe(840)
  })
})

describe('session expiry', () => {
  it('signals an expired session when an authenticated call is refused', async () => {
    const listener = vi.fn()
    setUnauthorizedListener(listener)
    mockFetch(respond(401, { detail: 'Not authenticated' }))

    await expect(api.overview()).rejects.toMatchObject({ status: 401 })

    expect(listener).toHaveBeenCalledOnce()
  })

  it('does not treat a wrong password or the first session check as an expiry', async () => {
    const listener = vi.fn()
    setUnauthorizedListener(listener)

    mockFetch(respond(401, { detail: 'Invalid password' }))
    await expect(api.login('wrong')).rejects.toMatchObject({ status: 401 })
    mockFetch(respond(401, { detail: 'Not authenticated' }))
    await expect(api.session()).rejects.toMatchObject({ status: 401 })

    expect(listener).not.toHaveBeenCalled()
  })
})
