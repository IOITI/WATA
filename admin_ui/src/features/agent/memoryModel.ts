// The AI agent's memory as the pages say it (src/admin/agent_memory.py): its kinds, who wrote and read an entry,
// each line of its history in a sentence, what a change changed, and the checks of the form, which are the server's.

import type {
  AgentMemoryAction,
  AgentMemoryChange,
  AgentMemoryDraft,
  AgentMemoryEntry,
  AgentMemoryKind,
  AgentMemoryVersion,
} from '@/lib/types'

export const MEMORY_PATH = '/agent/memory'
export const KINDS: AgentMemoryKind[] = ['preference', 'lesson', 'finding', 'reference']
// The server's own pattern (NAME in src/admin/agent_memory.py).
export const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{1,59}$/
export const MAX_NAME = 60

export const KIND_LABEL: Record<AgentMemoryKind, string> = {
  preference: 'Preference',
  lesson: 'Lesson',
  finding: 'Finding',
  reference: 'Reference',
}

export const KIND_HINT: Record<AgentMemoryKind, string> = {
  preference: 'What you want or refuse: how it answers, what it tries, what it never proposes.',
  lesson: 'How WATA, its data and its tools behave: a pitfall, a unit, a limit.',
  finding: 'What a study concluded, with the runs, trades and days it rests on.',
  reference: 'Where something worth going back to is: a run, a script, a rule, an experiment.',
}

export const ACTION_LABEL: Record<AgentMemoryAction, string> = {
  created: 'Created',
  changed: 'Changed',
  renamed: 'Renamed',
  forgotten: 'Forgotten',
  restored: 'Brought back',
  locked: 'Locked',
  unlocked: 'Unlocked',
  paused: 'Changes paused',
  resumed: 'Changes resumed',
}

/** What was done to an entry, after who did it: "The AI agent saved …". */
export const MEMORY_DONE: Record<AgentMemoryAction, string> = {
  created: 'saved',
  changed: 'changed',
  renamed: 'renamed',
  forgotten: 'forgot',
  restored: 'brought back',
  locked: 'locked',
  unlocked: 'unlocked',
  paused: "paused the agent's changes",
  resumed: "resumed the agent's changes",
}

/** Where an entry is shown: the Memory page, filtered to it and opened. */
export function memoryLink(name: string): string {
  return `${MEMORY_PATH}?find=${encodeURIComponent(name)}`
}

/** Who did something: you, or the agent. */
export function authorSaid(by: 'operator' | 'agent' | null | undefined): string {
  return by === 'operator' ? 'You' : 'The AI agent'
}

/** Who read an entry last: the agent (in one of its conversations), or Claude Code by its token's name. */
export function readerSaid(readBy: string | null | undefined): string | null {
  if (!readBy) return null
  return readBy.startsWith('agent:') ? 'The AI agent' : `Claude Code (${readBy})`
}

/** The agent's conversation an entry or a change came from, when one did. */
export function conversationOfReader(readBy: string | null | undefined): string | null {
  return readBy?.startsWith('agent:') ? readBy.slice('agent:'.length) : null
}

/** A line of the history in a sentence: "The AI agent saved range-ticks", "You renamed x (it was y)". */
export function changeSentence(line: AgentMemoryChange): string {
  const who = authorSaid(line.by)
  if (line.action === 'paused' || line.action === 'resumed') return `${who} ${MEMORY_DONE[line.action]}`
  const renamed = line.action === 'renamed' && line.from ? ` (it was ${line.from})` : ''
  const why = line.action === 'forgotten' && line.why ? `: ${line.why}` : ''
  return `${who} ${MEMORY_DONE[line.action]} ${line.name ?? 'an entry'}${renamed}${why}`
}

export function actionTone(action: AgentMemoryAction): 'neutral' | 'success' | 'warning' | 'danger' {
  if (action === 'forgotten') return 'danger'
  if (action === 'paused' || action === 'locked') return 'warning'
  if (action === 'created' || action === 'restored') return 'success'
  return 'neutral'
}

/** Whether a line of the history holds a version of an entry to bring back. */
export function restorable(line: AgentMemoryChange): boolean {
  return !!line.name && !!(line.after ?? line.before)
}

/** The version a line brings back: as it left the entry, or as it was before, for the line that forgot it. */
export function versionOf(line: AgentMemoryChange): AgentMemoryVersion | null {
  return line.after ?? line.before ?? null
}

const FIELD_LABEL: Record<keyof AgentMemoryVersion, string> = {
  kind: 'kind',
  description: 'description',
  body: 'text',
  locked: 'lock',
}

/** What a change changed of an entry, in words: "the text and the description". */
export function changedParts(line: AgentMemoryChange): string[] {
  const { before, after } = line
  if (!before || !after) return []
  return (Object.keys(FIELD_LABEL) as (keyof AgentMemoryVersion)[])
    .filter((field) => before[field] !== after[field])
    .map((field) => FIELD_LABEL[field])
}

/** Why a name cannot be an entry's, or null: the server's rule. */
export function nameProblem(name: string): string | null {
  if (!name) return 'Give it a name'
  if (!NAME_PATTERN.test(name)) return '2 to 60 lowercase letters, digits and dashes, starting with a letter or a digit'
  return null
}

/** A name made from words: lowercase, dashes for the rest, cut at 60. */
export function nameFrom(words: string): string {
  return words
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX_NAME)
    .replace(/-+$/, '')
}

/** What keeps the form from being sent, or null: the server checks it again, secrets included. */
export function draftProblem(draft: AgentMemoryDraft, limits: { description: number; body: number }): string | null {
  const named = nameProblem(draft.name)
  if (named) return named
  const description = draft.description.trim()
  if (!description) return 'Say in a line when it matters'
  if (/[\r\n]/.test(description)) return 'The description is one line'
  if (description.length > limits.description) return `The description is ${limits.description} characters at most`
  if (!draft.body.trim()) return 'Write what it should remember'
  if (draft.body.trim().length > limits.body) return `The text is ${limits.body.toLocaleString('en-GB')} characters at most`
  return null
}

export function draftOf(entry: AgentMemoryEntry | null): AgentMemoryDraft {
  return entry
    ? { name: entry.name, kind: entry.kind, description: entry.description, body: entry.body, locked: entry.locked }
    : { name: '', kind: 'preference', description: '', body: '', locked: true }
}

/** The figures of the page's tiles. */
export function memoryFigures(entries: readonly AgentMemoryEntry[]) {
  const last = entries.reduce<string | null>((latest, entry) => (!latest || entry.updated_at > latest ? entry.updated_at : latest), null)
  return {
    entries: entries.length,
    operator: entries.filter((entry) => entry.created_by === 'operator').length,
    agent: entries.filter((entry) => entry.created_by === 'agent').length,
    locked: entries.filter((entry) => entry.locked).length,
    last,
  }
}

/** The memory as a file, for a copy kept elsewhere: the server's files are outside the database's backups. */
export function exportedMemory(entries: readonly AgentMemoryEntry[], pause: unknown, now: Date): string {
  return `${JSON.stringify({ exported_at: now.toISOString(), entries, pause }, null, 1)}\n`
}
