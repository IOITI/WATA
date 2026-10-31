import { describe, expect, it } from 'vitest'

import type { AgentConversation, AgentFile, AgentModel, AgentSubject } from '@/lib/types'

import type { Pending } from './attachments'
import { attachmentKey, comparePreset, fileDetail, fileKind, fullSaid, heldBy, MAX_ATTACHED, refusalOf, sendBlocked, sizeText, toAttach } from './attachments'
import { scaledSize, MAX_EDGE, MAX_PIXELS } from './imageScale'

const RUN: AgentSubject = { kind: 'run', id: '20261027-101010-abcdef', label: 'Cross R60' }
const STAGED: AgentFile = { kind: 'file', id: 'f0123456789', name: 'chart.png', type: 'image', media_type: 'image/png', size: 2048, masked: 0 }
const HAIKU = { id: 'claude-haiku-5-5', label: 'Claude Haiku 5.5', images: false, pdfs: null } as AgentModel

describe('what is attached to a request', () => {
  it('tells two things apart as the server does: a script by its version, an incident by its moment', () => {
    expect(attachmentKey({ kind: 'script', id: 'a1', version: 3 })).toBe('script:a1@v3')
    expect(attachmentKey({ kind: 'incident', id: 'trader', at: '2026-10-30 14:01' })).toBe('incident:trader@2026-10-30 14:01')
    expect(attachmentKey(RUN)).toBe(`run:${RUN.id}`)
  })

  it('sends the things and the files the server staged, not those still uploading or refused', () => {
    const pending: Pending[] = [
      { key: 'item-1', kind: 'item', subject: { kind: 'signal', id: 'c0ffee', day: '2026-10-28', label: 'short signal' } },
      { key: 'file-1', kind: 'file', name: 'chart.png', size: 2048, type: 'image', state: 'ready', file: STAGED },
      { key: 'file-2', kind: 'file', name: 'export.csv', size: 10, type: 'text', state: 'uploading' },
      { key: 'file-3', kind: 'file', name: 'tool.exe', size: 10, type: null, state: 'error', error: 'No' },
    ]

    expect(toAttach(pending)).toEqual([
      { kind: 'signal', id: 'c0ffee', version: undefined, day: '2026-10-28', at: undefined },
      { kind: 'file', id: 'f0123456789' },
    ])
  })

  it('knows a file as the server will, and says what it may weigh', () => {
    expect(fileKind('Screenshot.PNG', 'image/png')).toBe('image')
    expect(fileKind('statement.pdf', '')).toBe('pdf')
    expect(fileKind('export.csv', 'text/csv')).toBe('text')
    expect(fileKind('cross.pine', '')).toBe('text')
    expect(fileKind('page.svg', 'image/svg+xml')).toBeNull()
    expect(refusalOf(null, 10)).toMatch(/^Attach an image/)
    expect(refusalOf('text', 300 * 1024)).toBe('A text file is 200 kB at most')
    expect(refusalOf('pdf', 1024)).toBeNull()
    expect([sizeText(512), sizeText(2048), sizeText(3.5 * 1024 * 1024)]).toEqual(['512 B', '2 kB', '3.5 MB'])
  })

  it('says beside a file where its upload is, its size and what was masked of it', () => {
    const file = { key: 'f', kind: 'file' as const, name: 'calls.txt', size: 900, type: 'text' as const }

    expect(fileDetail({ ...file, state: 'uploading' })).toBe('Uploading…')
    expect(fileDetail({ ...file, state: 'error', error: 'A text file is 200 kB at most' })).toBe('A text file is 200 kB at most')
    expect(fileDetail({ ...file, state: 'ready', file: { ...STAGED, type: 'text', size: 880, masked: 2 } })).toBe('880 B · 2 values masked')
  })

  it('holds the request while a file uploads or was refused, and for a model that reads none of its images', () => {
    const ready: Pending = { key: 'f', kind: 'file', name: 'chart.png', size: 2048, type: 'image', state: 'ready', file: STAGED }

    expect(sendBlocked([{ ...ready, state: 'uploading', file: undefined }], undefined)).toBe('Waiting for chart.png to be uploaded')
    expect(sendBlocked([{ ...ready, state: 'error' }], undefined)).toBe('chart.png could not be taken: remove it to send')
    expect(sendBlocked([ready], HAIKU)).toBe('Claude Haiku 5.5 does not read images: choose another model')
    // What the conversation holds counts too: the history sends it again.
    expect(sendBlocked([], HAIKU, [STAGED])).toBe('Claude Haiku 5.5 does not read images: choose another model')
    expect(sendBlocked([ready], { ...HAIKU, images: null })).toBeNull()
  })

  it('counts what a conversation holds, its subject alone for one of before, five at most', () => {
    const conversation = { requests: [
      { attached: [RUN, STAGED] },
      { shared: null },
      { shared: RUN },
    ] } as unknown as AgentConversation

    expect(heldBy(conversation)).toEqual([RUN, STAGED, RUN])
    expect(fullSaid(MAX_ATTACHED - 1)).toBeNull()
    expect(fullSaid(MAX_ATTACHED)).toBe('5 things attached at most in a conversation')
  })

  it('offers to compare two runs or more', () => {
    expect(comparePreset([RUN])).toBeNull()
    expect(comparePreset([RUN, { ...RUN, id: 'b' }])?.label).toBe('Compare the runs')
  })
})

describe('an image scaled before it is uploaded', () => {
  it('fits the largest the API reads, its proportions kept, and is left as it is when it fits', () => {
    expect(scaledSize(1920, 1080)).toBeNull()
    // A Retina screenshot: its long edge first, then its pixels.
    expect(scaledSize(5120, 2880)).toEqual({ width: 2576, height: 1449 })
    const wide = scaledSize(2560, 2560)!
    expect(wide.width * wide.height).toBeLessThanOrEqual(MAX_PIXELS)
    expect(Math.max(wide.width, wide.height)).toBeLessThanOrEqual(MAX_EDGE)
    expect(Math.abs(wide.width - wide.height)).toBeLessThanOrEqual(1)
    expect(scaledSize(0, 100)).toBeNull()
  })
})
