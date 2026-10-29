import { describe, expect, it } from 'vitest'

import type { ReleaseApply, ReleaseContainer, ReleaseState } from '@/lib/types'

import {
  applyOutcome,
  applySucceeded,
  applyUnavailable,
  heldSaid,
  isCareful,
  reasonsSaid,
  refusingImages,
  releaseChipHint,
  releaseSaid,
  requestFollowed,
  REQUEST_GIVE_UP_MS,
  versionSaid,
  waitingContainers,
} from './release'

function container(changes: Partial<ReleaseContainer>): ReleaseContainer {
  return {
    container: 'admin1',
    role: 'admin',
    kind: 'unchanged',
    when: 'skip',
    version: '0.61.0',
    target: null,
    reasons: {},
    held: null,
    group: null,
    held_with: [],
    image: 'wata-base:0.61.0',
    said: '',
    ...changes,
  }
}

const TRADER = container({
  container: 'trader1',
  role: 'trader',
  kind: 'change',
  when: 'pending',
  version: '0.59.1',
  reasons: { code: ['src/trade/rules.py', 'src/trade/fresh_quote.py', 'src/trader/__init__.py', 'src/heartbeat.py'] },
  group: 1,
})
const MONITOR = container({ container: 'position_monitor1', role: 'position_monitor', kind: 'change', version: '0.59.1', group: 1,
  reasons: { code: ['src/trade/rules.py'] }, held_with: ['trader1'] })
const FEED = container({ container: 'capital_feed1', role: 'capital_feed', kind: 'optional', version: '0.59.1',
  reasons: { platform: 'Python 3.14.11, Node v24.22.2' } })

function state(changes: Partial<ReleaseState> = {}): ReleaseState {
  return {
    available: true,
    error: null,
    actions_enabled: true,
    held: { trader1: "the market is open by WATA's hours" },
    applying: null,
    ...changes,
    status: {
      newest: '0.61.0',
      containers: [
        container({}),
        container({ container: 'web_server1', role: 'web_server', version: '0.59.1' }),
        TRADER,
        MONITOR,
        FEED,
      ],
      ...changes.status,
    },
  }
}

describe('what waits and why', () => {
  it('lists the changes and the platform alone, not what runs the same code', () => {
    expect(waitingContainers(state()).map((entry) => entry.container)).toEqual(['trader1', 'position_monitor1', 'capital_feed1'])
    expect(releaseSaid(state())).toBe(
      '0.61.0 is the newest release installed; 1 container runs it; 1 runs an older one with the same code; 3 containers wait for it.',
    )
  })

  it('says what changed in a few words, the files beyond three counted', () => {
    expect(reasonsSaid(TRADER)).toBe(
      'its code (4 files: src/trade/rules.py, src/trade/fresh_quote.py, src/trader/__init__.py and 1 more)',
    )
    expect(reasonsSaid(FEED)).toBe('only Python 3.14.11, Node v24.22.2: optional')
    expect(
      reasonsSaid(container({ kind: 'change', reasons: { libraries: [{ name: 'anthropic', from: '2.3.0', to: '2.4.0' }], compose: ['volumes'] } })),
    ).toBe('its libraries (anthropic 2.3.0 → 2.4.0); its compose definition (volumes)')
  })

  it('says each container’s release beside the newest', () => {
    expect(versionSaid(TRADER, '0.61.0')).toBe('0.59.1 · waits for 0.61.0')
    expect(versionSaid(FEED, '0.61.0')).toBe('0.59.1 · 0.61.0 optional')
    expect(versionSaid(container({ version: '0.59.1' }), '0.61.0')).toBe('0.59.1 · same code as 0.61.0')
    expect(versionSaid(container({}), '0.61.0')).toBe('0.61.0')
    expect(versionSaid(undefined, '0.61.0')).toBe('—')
  })

  it('says what holds one back: the admin’s look, else the group it waits with', () => {
    expect(heldSaid(state(), TRADER)).toBe("the market is open by WATA's hours")
    expect(heldSaid(state(), MONITOR)).toBe('waits with trader1, which change because of the same files')
    expect(heldSaid(state(), FEED)).toBeNull()
  })

  it('asks for a slide where something trades or records', () => {
    expect(isCareful(TRADER)).toBe(true)
    expect(isCareful(FEED)).toBe(true)
    expect(isCareful(container({}))).toBe(false)
  })
})

describe('applying', () => {
  it('cannot be asked without the server, the actions, while one runs, or with nothing waiting', () => {
    expect(applyUnavailable(state())).toBeNull()
    expect(applyUnavailable(state({ available: false, error: 'watadeploy has said nothing' }))).toBe('watadeploy has said nothing')
    expect(applyUnavailable(state({ actions_enabled: false }))).toMatch(/admin.actions_enabled/)
    const apply = { id: 'a', kind: 'apply', by: 'x', force: false, version: '0.61.0', request: null, started_at: '', steps: [], done: false } as ReleaseApply
    expect(applyUnavailable(state({ applying: apply }))).toBe('An apply is under way: one at a time')
    expect(applyUnavailable(state({ status: { newest: '0.61.0', containers: [container({})] } }))).toBe(
      'Every container runs the code of 0.61.0',
    )
  })

  it('says what an apply came to', () => {
    const apply: ReleaseApply = {
      id: 'a',
      kind: 'apply',
      by: 'admin (the System page)',
      force: false,
      version: '0.61.0',
      request: 'r',
      started_at: '',
      done: true,
      steps: [
        { container: 'admin1', role: 'admin', before: 'wata-base:0.59.1', after: 'wata-base:0.61.0', state: 'ready', detail: 'answering (200)', started_at: null },
        { container: 'trader1', role: 'trader', before: 'wata-base:0.59.1', after: 'wata-base:0.61.0', state: 'rolled_back', detail: 'did not stay up; back on wata-base:0.59.1', started_at: null },
        { container: 'scheduler1', role: 'scheduler', before: null, after: 'wata-base:0.61.0', state: 'skipped', detail: null, started_at: null },
      ],
    }
    expect(applyOutcome(apply)).toBe(
      'admin1: at work on 0.61.0; trader1: did not stay up; back on wata-base:0.59.1; not applied: scheduler1.',
    )
    expect(applySucceeded(apply)).toBe(false)
    expect(applySucceeded({ ...apply, steps: apply.steps.slice(0, 1) })).toBe(true)
  })

  it('follows a request until the server says what became of it, and gives up after its path unit would have', () => {
    const asked = 1_000
    expect(requestFollowed(state(), 'r1', asked, asked + 1_000)).toBeNull()
    expect(requestFollowed(state(), 'r1', asked, asked + REQUEST_GIVE_UP_MS + 1)?.state).toBe('lost')
    const refused = state({ status: { requests: [{ id: 'r1', state: 'refused', detail: 'asked too long ago: ask again', at: '' }] } })
    expect(requestFollowed(refused, 'r1', asked, asked + 1)).toEqual({ state: 'refused', detail: 'asked too long ago: ask again' })
    expect(requestFollowed(state(), null, null, 0)).toBeNull()
  })

  it('names the images that would not start on config.json as it is', () => {
    const found = state({
      status: { config: { at: '', images: [
        { image: 'wata-base:0.59.1', version: '0.59.1', containers: ['trader1'], accepted: false, detail: 'ValueError: x' },
        { image: 'wata-base:0.61.0', version: '0.61.0', containers: [], accepted: true, detail: 'accepted' },
      ] } },
    })
    expect(refusingImages(found).map((image) => image.image)).toEqual(['wata-base:0.59.1'])
  })

  it('says in the top bar what waits, or that a deploy applies', () => {
    expect(releaseChipHint({ newest: '0.61.0', containers: {}, waiting: ['trader1', 'scheduler1'], applying: false })).toBe(
      '0.61.0 waits for trader1 and scheduler1: apply it on the System page, once nothing holds it back.',
    )
    expect(releaseChipHint({ newest: '0.61.0', containers: {}, waiting: [], applying: true })).toMatch(/applying 0.61.0/)
  })
})
