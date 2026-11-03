import { describe, expect, it } from 'vitest'

import type { TaScriptSummary } from '@/lib/types'

import { familiesInUse, familiesOffered, indicatorsHref, narrowed, pictureSource, readIndicatorsAddress, scriptsShown } from './indicatorsModel'

const LUXALGO_PICTURE = 'https://luxalgo-production.s3.amazonaws.com/library/indicators/vwap/cover.png'

function script(changes: Partial<TaScriptSummary>): TaScriptSummary {
  return {
    id: 'aaaaaaaaaaaa', name: 'Cross', kind: 'indicator', description: '', pine_version: 6, library_name: null, imports: [],
    version: 1, versions: [], created_at: '2026-11-01T10:00:00+00:00', updated_at: '2026-11-01T10:00:00+00:00', ...changes,
  }
}

describe('the list of scripts narrowed by its address', () => {
  it('reads the favourites, a family and the view, and leaves its defaults out of the links it makes', () => {
    const address = readIndicatorsAddress(new URLSearchParams('favourite=yes&family=%20Trend%20&view=cards'))

    expect(address).toEqual({ favourite: 'yes', family: 'Trend', view: 'cards' })
    expect(indicatorsHref(address)).toBe('/ta/indicators?favourite=yes&family=Trend&view=cards')
    expect(indicatorsHref({ favourite: null, family: null, view: 'table' })).toBe('/ta/indicators')
    expect(readIndicatorsAddress(new URLSearchParams('favourite=no&view=list'))).toEqual({ favourite: null, family: null, view: 'table' })
    expect([narrowed(address), narrowed({ favourite: null, family: null, view: 'cards' })]).toEqual([true, false])
  })

  it('keeps the favourites and the scripts of a family, whatever its case', () => {
    const scripts = [
      script({ id: 'a', favourite: true, family: 'Trend' }),
      script({ id: 'b', family: 'trend' }),
      script({ id: 'c', favourite: true, family: 'Momentum' }),
      script({ id: 'd' }),
    ]

    expect(scriptsShown(scripts, { favourite: 'yes', family: null, view: 'table' }).map((found) => found.id)).toEqual(['a', 'c'])
    expect(scriptsShown(scripts, { favourite: null, family: 'TREND', view: 'table' }).map((found) => found.id)).toEqual(['a', 'b'])
    expect(scriptsShown(scripts, { favourite: 'yes', family: 'Trend', view: 'cards' }).map((found) => found.id)).toEqual(['a'])
  })
})

describe('the families', () => {
  it('in use are listed once each, and offered with those of LuxAlgo’s Library not in use', () => {
    const scripts = [script({ family: 'Trend' }), script({ family: 'trend' }), script({ family: 'Breakouts' }), script({ family: null })]

    expect(familiesInUse(scripts)).toEqual(['Breakouts', 'Trend'])
    expect(familiesOffered(scripts, [{ key: 'trend', name: 'Trend' }, { key: 'volume-orderflow', name: 'Volume & Order Flow' },
      { key: 'momentum', name: 'Momentum' }])).toEqual(['Breakouts', 'Trend', 'Momentum', 'Volume & Order Flow'])
  })
})

describe('a script’s picture', () => {
  it('is its own from the admin, else LuxAlgo’s from its image host alone', () => {
    expect(pictureSource(script({ picture: '/api/ta/scripts/aaaaaaaaaaaa/picture?v=1', own_picture: true }))).toBe('/api/ta/scripts/aaaaaaaaaaaa/picture?v=1')
    expect(pictureSource(script({ picture: '/api/ta/scripts/bbbbbbbbbbbb/picture?v=1', own_picture: true }))).toBeNull()
    expect(pictureSource(script({ picture: LUXALGO_PICTURE, own_picture: false }))).toBe(LUXALGO_PICTURE)
    expect(pictureSource(script({ picture: 'https://evil.example/library/a.png', own_picture: false }))).toBeNull()
    expect(pictureSource(script({}))).toBeNull()
  })
})
