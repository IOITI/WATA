import { describe, expect, it } from 'vitest'

import type { QueueItem } from '@/lib/types'

import { asYours, criteriaSaid, draftProblem, emptyDraft, filedBy, hypothesisFromAutopsy, movedTo, parseDays, sourceRun, splitQueue } from './queueModel'

function item(id: string, by = 'operator', state: QueueItem['state'] = 'waiting', extra: Partial<QueueItem> = {}): QueueItem {
  return {
    id, title: id, hypothesis: 'h', family: 'f', rationale: null, criteria: [], motivated_by: [], by, conversation_from: null,
    state, order: 0, created_at: '2026-11-20T10:00:00Z', updated_at: null, conversation: null, experiment: null,
    conclusion: null, ...extra,
  }
}

describe('the research queue, in words', () => {
  it('says who filed a hypothesis and what it came from', () => {
    expect(filedBy(item('a'))).toBe('You')
    expect(filedBy(item('a', 'operator', 'waiting', { source: 'autopsy:20261114-113341-aa2d8f' }))).toBe('You, from a run’s autopsy')
    expect(sourceRun(item('a', 'operator', 'waiting', { source: 'autopsy:20261114-113341-aa2d8f' }))).toBe('20261114-113341-aa2d8f')
    expect(filedBy(item('b', 'agent:20261120-180000-abcdef'))).toBe('A research task')
    expect(criteriaSaid([{ figure: 'edge_t', bound: 'at_least', value: 2.6 }, { figure: 'pbo', bound: 'at_most', value: 0.5 }]))
      .toBe('edge t at least 2.6; pbo at most 0.5')
  })

  it('moves an operator’s hypothesis among theirs and takes a task’s as theirs, last', () => {
    const { waiting, others } = splitQueue([item('a'), item('b'), item('c'), item('t', 'agent:x'), item('d', 'operator', 'done')])

    expect(waiting.map((entry) => entry.id)).toEqual(['a', 'b', 'c', 't'])
    expect(others.map((entry) => entry.id)).toEqual(['d'])
    expect([movedTo(waiting, 'b', -1), movedTo(waiting, 'b', 1)]).toEqual([0, 2])
    expect([movedTo(waiting, 'a', -1), movedTo(waiting, 'c', 1), movedTo(waiting, 't', -1)]).toEqual([null, null, null])
    expect(asYours(waiting)).toBe(3)
  })

  it('checks the form before it is sent', () => {
    const draft = { ...emptyDraft(), title: 'Gap fill', hypothesis: 'The gap fills by 10:30', family: 'gaps' }

    expect(draftProblem(emptyDraft())).toBe('Give it a title')
    expect(draftProblem({ ...draft, family: ' ' })).toBe('Name its family of ideas')
    expect(draftProblem({ ...draft, motivated_by: parseDays('2026-11-11, 11/12') })).toBe('11/12 is not a day (YYYY-MM-DD)')
    expect(draftProblem({ ...draft, motivated_by: parseDays(' 2026-11-11 2026-11-12,') })).toBeNull()
  })

  it('makes a hypothesis of what a run’s autopsy found held', () => {
    const held = { name: 'move_15', said: 'The index’s move over 15 minutes at or above 4', q: 0.04, sentence: 'Trades entered after a move over 15 minutes at or above 4 made more.' }
    const draft = hypothesisFromAutopsy(held, { choose_days: ['2026-10-15', '2026-11-03'], test_days: ['2026-11-04', '2026-11-13'] },
      { id: '20261114-113341-aa2d8f', name: 'Kernel VWAP', family: 'kernel-avg-vwap' })

    expect(draft.title).toBe('The index’s move over 15 minutes at or above 4')
    expect(draft.hypothesis).toBe(held.sentence)
    expect(draft.family).toBe('kernel-avg-vwap')
    expect(draft.source).toBe('autopsy:20261114-113341-aa2d8f')
    expect(draft.rationale).toContain('chosen on 2026-10-15 to 2026-11-03, held on 2026-11-04 to 2026-11-13 (q 0.04)')
    expect(hypothesisFromAutopsy(held, {}, { id: 'r', name: null, family: null }).family).toBe('autopsy')
  })
})
