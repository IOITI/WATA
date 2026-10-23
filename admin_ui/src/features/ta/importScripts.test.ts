import { afterEach, describe, expect, it, vi } from 'vitest'

import { importedName, importScript, librariesFirst } from './importScripts'

function pine(name: string, source: string): File {
  return new File([source], name, { type: 'text/plain' })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('importing .pine files', () => {
  it('names a script as it names itself, else as its file without the extension', () => {
    expect(importedName('//@version=6\nindicator("EMA cross", overlay=true)', 'ema.pine')).toBe('EMA cross')
    expect(importedName('//@version=6\nlibrary(\'Kernels\')', 'k.pine')).toBe('Kernels')
    expect(importedName('//@version=6\nplot(close)', 'Smoke.TXT')).toBe('Smoke')
  })

  it('takes the libraries first, each kind in the order given', async () => {
    const files = [
      pine('a.pine', '//@version=6\nindicator("A")'),
      pine('lib1.pine', '//@version=6\nlibrary("One")'),
      pine('b.pine', '//@version=6\nstrategy("B")'),
      pine('lib2.pine', '//@version=6\nlibrary("Two")'),
    ]

    expect((await librariesFirst(files)).map((file) => file.name)).toEqual(['lib1.pine', 'lib2.pine', 'a.pine', 'b.pine'])
  })

  it('sends the source under its name', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: 's1', name: 'EMA cross' }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)

    await importScript(pine('ema.pine', '//@version=6\nindicator("EMA cross")'))

    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/ta/scripts')
    expect(JSON.parse(String(init.body))).toEqual({ name: 'EMA cross', source: '//@version=6\nindicator("EMA cross")' })
  })
})
