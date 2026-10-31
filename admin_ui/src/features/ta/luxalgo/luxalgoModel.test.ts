import { describe, expect, it } from 'vitest'

import type { LuxAlgoCompat, LuxAlgoKnownShort, LuxAlgoSource, ScriptOrigin } from '@/lib/types'

import {
  canTake,
  cardBadges,
  familyTint,
  previewImage,
  sourceLinesSaid,
  checkedOf,
  compatSaid,
  conceptPath,
  goLiveSaid,
  knowsNothing,
  licenceToCheck,
  UNREAD_LICENCE,
  indicatorPath,
  licenceTerms,
  originSaid,
  pageCount,
  returnSaid,
  shownMarkdown,
  signInSaid,
  sourceSaid,
} from './luxalgoModel'

const SOURCE: LuxAlgoSource = { slug: 'tri-star', name: 'Tri-star', available: true, complete: true, source: '//@version=6' }
const ORIGIN: ScriptOrigin = { provider: 'luxalgo', slug: 'tri-star', name: 'Tri-star', licence: 'CC BY-NC-SA 4.0', owner: 'LuxAlgo' }

describe('a write-up of LuxAlgo', () => {
  it('leads its Library links to the admin pages and its other links to LuxAlgo', () => {
    expect(shownMarkdown('the [tri-star](/library/concept/tri-star/) and [Supertrend](/library/indicator/supertrend/)')).toBe(
      `the [tri-star](${conceptPath('tri-star')}) and [Supertrend](${indicatorPath('supertrend')})`,
    )
    expect(shownMarkdown('[a family](/library/family/trend/)')).toBe('[a family](https://www.luxalgo.com/library/family/trend/)')
    expect(shownMarkdown('[it](https://www.luxalgo.com/library/indicator/tri-star/)')).toBe(`[it](${indicatorPath('tri-star')})`)
    expect(shownMarkdown('[TradingView](https://www.tradingview.com/)')).toBe('[TradingView](https://www.tradingview.com/)')
    expect(shownMarkdown('[odd](//elsewhere.example/x)')).toBe('[odd](//elsewhere.example/x)')
  })

  it('leaves out its images, and its own title', () => {
    expect(shownMarkdown('Before ![a chart](https://cdn.example/x.png) after')).toBe('Before  after')
    expect(shownMarkdown('# Supertrend\n\nA Trend concept.\n\n## How to read it')).toBe('A Trend concept.\n\n## How to read it')
    expect(shownMarkdown(null)).toBe('')
  })
})

describe('a source', () => {
  it('is taken whole or not at all', () => {
    expect(canTake(SOURCE)).toBe(true)
    expect(sourceSaid(SOURCE)).toBeNull()
    const part = { ...SOURCE, complete: false, shown_lines: 40, total_lines: 201 }
    expect(canTake(part)).toBe(false)
    expect(sourceSaid(part)).toBe(
      "LuxAlgo gives 40 of its 201 lines to an admin not signed in: sign in to LuxAlgo on the Library's page to read it whole and take it.",
    )
    expect(sourceSaid({ ...SOURCE, available: false, source: undefined })).toBe("LuxAlgo does not publish this indicator's source.")
    expect(canTake(undefined)).toBe(false)
  })
})

describe('the sign-in', () => {
  it('says whether whole sources are given', () => {
    expect(signInSaid({ enabled: true, signed_in: true, account: 'me@example.com', expires_at: 1, refused: null })).toEqual({
      tone: 'positive',
      text: 'Signed in as me@example.com: LuxAlgo gives whole sources. The admin renews the sign-in itself.',
    })
    expect(signInSaid({ enabled: true, signed_in: false, account: null, expires_at: null, refused: null }).tone).toBe('neutral')
    expect(signInSaid({ enabled: true, signed_in: true, account: 'x', expires_at: 1, refused: 'LuxAlgo would not renew' })).toEqual({
      tone: 'warning',
      text: 'LuxAlgo would not renew',
    })
  })

  it('says what came of a return from LuxAlgo', () => {
    expect(returnSaid(new URLSearchParams('luxalgo=signed-in'))).toEqual({ tone: 'success', text: 'Signed in to LuxAlgo.' })
    expect(returnSaid(new URLSearchParams('luxalgo=refused&why=access_denied'))?.text).toBe(
      'The sign-in to LuxAlgo did not finish: access_denied.',
    )
    expect(returnSaid(new URLSearchParams('luxalgo=refused&why=off'))?.text).toMatch(/switched off/)
    expect(returnSaid(new URLSearchParams('page=2'))).toBeNull()
  })
})

describe('the origin of a script', () => {
  it('is said with its licence', () => {
    expect(originSaid(ORIGIN)).toBe("From LuxAlgo's Library: Tri-star, under CC BY-NC-SA 4.0, © LuxAlgo.")
    expect(originSaid({ provider: 'luxalgo', derived: true, licence: 'CC BY-NC-SA 4.0', owner: 'LuxAlgo' })).toBe(
      "Its header says it is LuxAlgo's, © LuxAlgo: under CC BY-NC-SA 4.0.",
    )
    expect(goLiveSaid(ORIGIN)).toBe(
      "Its script comes from LuxAlgo's Library (Tri-star), under CC BY-NC-SA 4.0: its terms are yours to check before trading it.",
    )
    expect(goLiveSaid({ ...ORIGIN, derived: true })).toMatch(/^Its script is derived from one of LuxAlgo's indicators/)
  })

  it("names what the licences LuxAlgo's indicators come under ask", () => {
    expect(licenceTerms('CC BY-NC-SA 4.0')).toMatch(/^non-commercial use only/)
    expect(licenceTerms('Mozilla Public License 2.0')).toMatch(/same licence/)
    expect(licenceTerms("see LuxAlgo's NOTICE")).toBeNull()
  })
})

describe('the pages of the list', () => {
  it('are at least one', () => {
    expect(pageCount(121, 24)).toBe(6)
    expect(pageCount(0, 24)).toBe(1)
    expect(pageCount(24, 24)).toBe(1)
  })
})

const CHECKED = '2026-10-31T09:00:00Z'
const RUNS: LuxAlgoCompat = { slug: 'tri-star', state: 'runs', checked_at: CHECKED, alertconditions: ['Bull', 'Bear'], alert_calls: 1 }

describe("WATA's check of an indicator", () => {
  it('says whether it runs, raises alerts, or what kept it from being checked', () => {
    expect(compatSaid(RUNS)).toBe('It compiles on WATA and declares 2 alerts (Bull, Bear) and calls alert() once: a signal rule maps them to actions.')
    expect(compatSaid({ ...RUNS, state: 'no_alerts', alertconditions: [], alert_calls: 0, strategy: true })).toBe(
      'It compiles on WATA but declares no alert: add alertcondition() lines in a version of yours to make signals. '
        + 'It is a strategy(): its orders are not signals, only its alerts are.',
    )
    expect(compatSaid({ ...RUNS, state: 'fails', error: 'Unknown function ta.foo' })).toBe('It does not compile on WATA: Unknown function ta.foo.')
    expect(compatSaid({ ...RUNS, state: 'cut' })).toBe('LuxAlgo gave a part of it: sign in to LuxAlgo to have it checked whole.')
  })

  it('is listed by what came of it', () => {
    const failing: LuxAlgoCompat = { ...RUNS, slug: 'other', state: 'fails' }
    expect(checkedOf([RUNS, failing], null)).toEqual([RUNS, failing])
    expect(checkedOf([RUNS, failing], 'fails')).toEqual([failing])
  })
})

describe("a card of the Library", () => {
  const KNOWN: LuxAlgoKnownShort = {
    slug: 'tri-star', scripts: [{ id: '0123456789ab', name: 'Tri-star', made_by: null }], verdict: { tone: 'negative', line: 'No edge.' },
    compat: RUNS, runs: 2, rules: [],
  }

  it('says what WATA took, judged and checked of its indicator', () => {
    expect(cardBadges(KNOWN)).toEqual([
      { label: 'Taken', tone: 'neutral' },
      { label: 'Tested: no', tone: 'danger' },
      { label: 'Runs on WATA', tone: 'success' },
    ])
    expect(cardBadges({ ...KNOWN, verdict: null, compat: null })).toEqual([
      { label: 'Taken', tone: 'neutral' },
      { label: 'Not checked', tone: 'neutral' },
    ])
    expect(cardBadges(undefined)).toEqual([])
  })

  it('knows nothing of an indicator WATA never touched', () => {
    expect(knowsNothing(undefined)).toBe(true)
    expect(knowsNothing({ scripts: [], runs: [], rules: [], verdict: null, compat: null })).toBe(true)
    expect(knowsNothing({ scripts: [], runs: [], rules: [], verdict: null, compat: RUNS })).toBe(false)
  })
})

describe('going live on a script of LuxAlgo', () => {
  it("asks that a licence forbidding commerce, or one WATA could not read, was checked", () => {
    expect(licenceToCheck(ORIGIN)).toBe('CC BY-NC-SA 4.0')
    expect(licenceToCheck({ ...ORIGIN, licence: 'Attribution-NonCommercial 4.0' })).toBe('Attribution-NonCommercial 4.0')
    expect(licenceToCheck({ ...ORIGIN, licence: UNREAD_LICENCE })).toBe(UNREAD_LICENCE)
    expect(licenceToCheck({ ...ORIGIN, licence: '' })).toBe(UNREAD_LICENCE)
    expect(licenceToCheck({ ...ORIGIN, licence: 'Mozilla Public License 2.0' })).toBeNull()
    expect(licenceToCheck(undefined)).toBeNull()
  })
})

describe("LuxAlgo's pictures", () => {
  const CARD = 'https://luxalgo-production.s3.amazonaws.com/library/cards/tri-star-6cd8deca7e.png'

  it('are taken from its image host alone, the one the admin lets pictures come from', () => {
    expect(previewImage(CARD)).toBe(CARD)
    expect(previewImage('https://luxalgo-production.s3.amazonaws.com/library/n010tbbucqa84lksw1nz7t9q.png')).not.toBeNull()
    for (const elsewhere of [
      'http://luxalgo-production.s3.amazonaws.com/library/cards/x.png',
      'https://example.com/library/cards/x.png',
      'https://luxalgo-production.s3.amazonaws.com/other/x.png',
      `${CARD}?tracking=1`,
      'https://luxalgo-production.s3.amazonaws.com/library/cards/x.svg',
      'not a url',
      '',
    ]) {
      expect(previewImage(elsewhere)).toBeNull()
    }
    expect(previewImage(undefined)).toBeNull()
  })

  it('stand in with a tint of the family, the same each time', () => {
    expect(familyTint('trend')).toBe(familyTint('trend'))
    expect(familyTint('trend')).not.toBe(familyTint('volatility'))
    expect(familyTint(undefined)).toMatch(/^linear-gradient\(135deg, hsl\(/)
  })
})

describe('how much of its source LuxAlgo gives', () => {
  it('is said in a few words', () => {
    expect(sourceLinesSaid(undefined)).toBe('Being read')
    expect(sourceLinesSaid({ ...SOURCE, total_lines: 1234 })).toBe('Whole, 1,234 lines')
    expect(sourceLinesSaid({ ...SOURCE, complete: false, shown_lines: 40, total_lines: 311 })).toBe('40 of 311 lines, signed out')
    expect(sourceLinesSaid({ ...SOURCE, available: false, source: undefined })).toBe('None published')
  })
})
