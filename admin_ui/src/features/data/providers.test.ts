import { describe, expect, it } from 'vitest'

import type { DataProvider, DataProviders, JsonObject, RecorderSettings } from '@/lib/types'

import {
  catalogItems,
  instrumentProblems,
  listedProviders,
  recorderDraft,
  recorderWarnings,
  sectionToSave,
} from './providers'

const DEFAULTS: RecorderSettings = {
  enabled: false, record_volume: false, max_instruments: 150, refresh_interval_seconds: 300, refresh_rate_ms: 1000,
  retention_months: 0, alert_after_seconds: 300, instruments: [],
}

function provider(changes: Partial<DataProvider>): DataProvider {
  return {
    id: 'saxo', name: 'Saxo Bank', kind: 'live', available: true, description: '', status: 'ok', detail: '',
    enabled: true, active: true, assets: 7, first_t: null, last_t: null, first_day: null, last_day: null, configurable: true, ...changes,
  }
}

const CATALOG = [
  { id: 'saxo', name: 'Saxo Bank', kind: 'live' as const, available: true, description: '' },
  { id: 'dukascopy', name: 'Dukascopy', kind: 'download' as const, available: true, description: '' },
  { id: 'capital', name: 'Capital.com', kind: 'live' as const, available: false, description: '' },
]

describe('the providers listed, and those offered', () => {
  const answer: DataProviders = {
    items: [provider({}), provider({ id: 'dukascopy', name: 'Dukascopy', kind: 'download', active: false, assets: 0 })],
    catalog: CATALOG,
    timezone: 'Europe/Paris',
  }

  it('lists those switched on or holding data', () => {
    expect(listedProviders(answer).map((item) => item.id)).toEqual(['saxo'])
    expect(listedProviders(undefined)).toEqual([])
  })

  it('offers the others, and says which is to come', () => {
    expect(catalogItems(answer).map((item) => [item.entry.id, item.state])).toEqual([
      ['saxo', 'active'], ['dukascopy', 'available'], ['capital', 'coming'],
    ])
  })
})

describe('the price recorder’s settings', () => {
  it('are shown as on disk, each one left out at its default', () => {
    expect(recorderDraft({ enabled: true, max_instruments: 80 }, DEFAULTS)).toEqual({ ...DEFAULTS, enabled: true, max_instruments: 80 })
  })

  it('are written back as on disk, with what was changed and nothing else', () => {
    const onDisk: JsonObject = { enabled: true, comment: 'kept as it is' }
    const draft = { ...recorderDraft(onDisk, DEFAULTS), record_volume: true }

    expect(sectionToSave(onDisk, draft, DEFAULTS)).toEqual({ enabled: true, comment: 'kept as it is', record_volume: true })
  })

  it('keep a setting that was on disk even at its default', () => {
    const onDisk: JsonObject = { enabled: true, max_instruments: 80 }
    const draft = { ...recorderDraft(onDisk, DEFAULTS), max_instruments: 150 }

    expect(sectionToSave(onDisk, draft, DEFAULTS)).toEqual({ enabled: true, max_instruments: 150 })
  })

  it('are written in full for a section that was not there', () => {
    const draft = { ...recorderDraft({}, DEFAULTS), enabled: true }

    expect(sectionToSave({}, draft, DEFAULTS)).toEqual({ enabled: true })
  })
})

describe('the instruments recorded besides the turbos', () => {
  it('are refused when one lacks what identifies it', () => {
    expect(instrumentProblems([{ label: '', uic: null, asset_type: 'CfdOnIndex' }])).toEqual([
      'Instrument 1 needs a label',
      'Instrument 1 needs a UIC, a whole number',
    ])
    expect(instrumentProblems([{ label: 'dax', uic: 4.5, asset_type: '' }])).toEqual([
      'Instrument 1 needs a UIC, a whole number',
      'Instrument 1 needs an asset type',
    ])
  })

  it('are refused under one label twice, or one instrument twice', () => {
    expect(
      instrumentProblems([
        { label: 'dax', uic: 1, asset_type: 'CfdOnIndex' },
        { label: 'dax', uic: 2, asset_type: 'CfdOnIndex' },
        { label: 'dax2', uic: 1, asset_type: 'CfdOnIndex' },
      ]),
    ).toEqual(['Two instruments are labelled "dax"', 'UIC 1 as CfdOnIndex is listed twice'])
  })

  it('are accepted when each is itself', () => {
    expect(instrumentProblems([{ label: 'us100', uic: 4913, asset_type: 'CfdOnIndex' }])).toEqual([])
    expect(instrumentProblems(undefined)).toEqual([])
  })
})

describe('what a save says before it is confirmed', () => {
  const before = recorderDraft({ enabled: true, retention_months: 0 }, DEFAULTS)

  it('says that prices are lost while the recorder is off', () => {
    expect(recorderWarnings(before, { ...before, enabled: false }, [])).toEqual([
      'The recorder stops at its next start: the prices of the time it is off can never be fetched afterwards.',
    ])
  })

  it('says that a retention deletes the months before it, and only when it shortens', () => {
    expect(recorderWarnings(before, { ...before, retention_months: 6 }, [])[0]).toMatch(/^Only 6 months kept: .* for good\.$/)
    expect(recorderWarnings({ ...before, retention_months: 6 }, { ...before, retention_months: 12 }, [])).toEqual([])
  })

  it('says that an instrument labelled like an indice replaces its index', () => {
    const after = { ...before, instruments: [{ label: 'us100', uic: 4913, asset_type: 'CfdOnIndex' }] }

    expect(recorderWarnings(before, after, ['us100'])).toEqual([
      '"us100" is the name of an indice: this instrument is recorded as its index, in place of the one the recorder finds itself.',
    ])
    // Already so before: nothing new is said.
    expect(recorderWarnings(after, after, ['us100'])).toEqual([])
  })

  it('says nothing of a change that can be taken back', () => {
    expect(recorderWarnings(before, { ...before, record_volume: true, max_instruments: 80 }, ['us100'])).toEqual([])
  })
})
