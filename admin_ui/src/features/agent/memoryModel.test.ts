import { describe, expect, it } from 'vitest'

import type { AgentMemoryChange, AgentMemoryEntry } from '@/lib/types'

import {
  changedParts,
  changeSentence,
  conversationOfReader,
  draftOf,
  draftProblem,
  exportedMemory,
  memoryFigures,
  memoryLink,
  nameFrom,
  nameProblem,
  readerSaid,
  restorable,
  versionOf,
} from './memoryModel'

const LIMITS = { description: 200, body: 4000 }
const VERSION = { kind: 'lesson' as const, description: 'R60 is 6 points on capital/US100', body: 'R6000 on Dukascopy.', locked: false }

function line(changes: Partial<AgentMemoryChange>): AgentMemoryChange {
  return {
    seq: 1, at: '2026-10-29T08:00:00Z', action: 'created', name: 'range-ticks', by: 'agent', address: null,
    conversation: '20261029-080000-a1b2c3', title_of: 'Ticks', request: 0, before: null, after: VERSION, ...changes,
  }
}

function entry(changes: Partial<AgentMemoryEntry>): AgentMemoryEntry {
  return {
    name: 'range-ticks', ...VERSION, revision: 1, created_at: '2026-10-29T08:00:00Z', created_by: 'agent',
    created_in: null, updated_at: '2026-10-29T08:00:00Z', updated_by: 'agent', updated_in: null, read_at: null,
    read_by: null, ...changes,
  }
}

describe("the AI agent's memory", () => {
  it('names an entry from its words as the server accepts them', () => {
    expect(nameFrom('The verdict first, five lines at most!')).toBe('the-verdict-first-five-lines-at-most')
    expect(nameFrom('Équipe — R60 sur US100')).toBe('equipe-r60-sur-us100')
    expect(nameFrom(`${'word '.repeat(20)}`)).toMatch(/^[a-z0-9][a-z0-9-]{1,59}$/)
    expect(nameFrom(`${'word '.repeat(20)}`).endsWith('-')).toBe(false)
    expect(nameProblem('')).toBe('Give it a name')
    expect(nameProblem('Range-Ticks')).toMatch(/lowercase letters/)
    expect(nameProblem('-ticks')).toMatch(/starting with a letter or a digit/)
    expect(nameProblem('r')).not.toBeNull()
    expect(nameProblem('range-ticks')).toBeNull()
  })

  it('says what keeps a form from being sent, as the server would', () => {
    const fine = { ...draftOf(null), name: 'answer-short', description: 'The verdict first', body: 'Five lines.' }

    expect(draftOf(null)).toEqual({ name: '', kind: 'preference', description: '', body: '', locked: true })
    expect(draftProblem(fine, LIMITS)).toBeNull()
    expect(draftProblem({ ...fine, description: ' ' }, LIMITS)).toBe('Say in a line when it matters')
    expect(draftProblem({ ...fine, description: 'Two\nlines' }, LIMITS)).toBe('The description is one line')
    expect(draftProblem({ ...fine, description: 'x'.repeat(201) }, LIMITS)).toMatch(/200 characters at most/)
    expect(draftProblem({ ...fine, body: '' }, LIMITS)).toBe('Write what it should remember')
    expect(draftProblem({ ...fine, body: 'x'.repeat(4001) }, LIMITS)).toMatch(/4,000 characters at most/)
  })

  it('says each line of its history in a sentence, and what a change changed', () => {
    expect(changeSentence(line({}))).toBe('The AI agent saved range-ticks')
    expect(changeSentence(line({ action: 'renamed', by: 'operator', name: 'tick-sizes', from: 'range-ticks' })))
      .toBe('You renamed tick-sizes (it was range-ticks)')
    expect(changeSentence(line({ action: 'forgotten', why: 'Said twice', after: null, before: VERSION })))
      .toBe('The AI agent forgot range-ticks: Said twice')
    expect(changeSentence(line({ action: 'paused', by: 'operator', name: null, after: null }))).toBe("You paused the agent's changes")
    expect(changedParts(line({ action: 'changed', before: VERSION, after: { ...VERSION, body: 'New.', locked: true } })))
      .toEqual(['text', 'lock'])
    expect(changedParts(line({}))).toEqual([])
  })

  it('brings back the version a line left, or the one a forgetting took away', () => {
    expect(restorable(line({}))).toBe(true)
    expect(versionOf(line({ action: 'forgotten', after: null, before: VERSION }))).toEqual(VERSION)
    expect(restorable(line({ action: 'paused', name: null, after: null }))).toBe(false)
  })

  it('says who read an entry last and leads to it', () => {
    expect(readerSaid('agent:20261029-080000-a1b2c3')).toBe('The AI agent')
    expect(conversationOfReader('agent:20261029-080000-a1b2c3')).toBe('20261029-080000-a1b2c3')
    expect(readerSaid('claude-mac')).toBe('Claude Code (claude-mac)')
    expect(conversationOfReader('claude-mac')).toBeNull()
    expect(readerSaid(null)).toBeNull()
    expect(memoryLink('range-ticks')).toBe('/agent/memory?find=range-ticks')
  })

  it('counts the entries by author and lock, and writes them as a file to keep', () => {
    const entries = [
      entry({}),
      entry({ name: 'answer-short', kind: 'preference', created_by: 'operator', locked: true, updated_at: '2026-10-29T09:00:00Z' }),
    ]

    expect(memoryFigures(entries)).toEqual({ entries: 2, operator: 1, agent: 1, locked: 1, last: '2026-10-29T09:00:00Z' })
    expect(memoryFigures([]).last).toBeNull()
    const file = JSON.parse(exportedMemory(entries, null, new Date('2026-10-29T10:00:00Z')))
    expect(file.exported_at).toBe('2026-10-29T10:00:00.000Z')
    expect(file.entries.map((kept: AgentMemoryEntry) => kept.name)).toEqual(['range-ticks', 'answer-short'])
  })
})
