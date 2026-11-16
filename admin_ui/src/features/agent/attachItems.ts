// What the attach picker offers (AttachMenu.tsx): a file of the operator's computer first, then WATA's things as the
// pages list them, each attached as its page asks the agent about it (AskAgentLink), and a number or a day typed.
// Read through the pages' own queries, only while the picker is open, as the palette's (components/commandItems.ts).
// The picker stays open to attach several: what this message attaches is ticked, and chosen again it is taken back.
// LuxAlgo's Library is a list of its own, searched by LuxAlgo for what is typed in it. Pure.

import { formatDateTime, formatDay, formatPercent } from 'ioiti-ui'
import type { SpotlightSearchItem } from 'ioiti-ui'

import { localDay, sourceLabel, STATE_LABEL } from '@/features/backtests/model'
import { barsLabel, MODE_LABEL } from '@/features/ta/signals/ruleModel'
import { KIND_LABEL } from '@/features/ta/templates'
import type {
  AgentSubject,
  BacktestRunListed,
  DailyPerformance,
  Health,
  JournalEntry,
  LuxAlgoSearchResult,
  Position,
  TaRule,
  TaScriptSummary,
} from '@/lib/types'

import { attachmentKey, fullSaid, MAX_ATTACHED } from './attachments'

export interface AttachLists {
  runs?: readonly BacktestRunListed[]
  scripts?: readonly TaScriptSummary[]
  rules?: readonly TaRule[]
  positions?: readonly Position[]
  journal?: readonly JournalEntry[]
  days?: readonly DailyPerformance[]
  health?: Health | null
  /** LuxAlgo's indicators found for what is typed in its list, while the agent has LuxAlgo's tools. */
  luxalgo?: readonly LuxAlgoSearchResult[]
}

/** How the categories are named, in the order they are listed. */
export const CATEGORY = {
  file: 'From your computer',
  run: 'Backtests',
  script: 'Scripts',
  rule: 'Signal rules',
  position: 'Positions',
  signal: 'Signals',
  day: 'Trading days',
  incident: 'Incidents',
  luxalgo: 'LuxAlgo Library',
} as const

function positionLabel(position: Position): string {
  return [position.action, position.instrument_name].filter(Boolean).join(' ') || `position ${position.position_id}`
}

/** The id of LuxAlgo's list among the entries: the picker searches it through LuxAlgo, not by itself. */
export const LUXALGO_LIST = 'luxalgo'
/** LuxAlgo's Library is searched from this many characters typed in its list. */
export const LUXALGO_FROM_CHARS = 3

/**
 * What each list becomes: an entry that attaches its thing. What the conversation holds already (and the page's
 * subject) is left out; what this message attaches is offered ticked, to be taken back; once full, only those.
 */
export function attachItems({ lists, attached, pending, room, query, timeZone, luxalgo, onAttach, onDetach, onFile }: {
  lists: AttachLists
  /** The keys of everything attached already (attachmentKey): the conversation's, the page's subject, this message's. */
  attached: ReadonlySet<string>
  /** The keys of what this message attaches, among them: ticked, and taken back when chosen again. */
  pending: ReadonlySet<string>
  /** How many more may be attached. */
  room: number
  query: string
  timeZone: string
  /** LuxAlgo's Library offered as a list of its own, while the agent has LuxAlgo's tools. */
  luxalgo?: boolean
  onAttach: (subject: AgentSubject) => void
  onDetach: (key: string) => void
  onFile: () => void
}): { items: SpotlightSearchItem[]; picked: string[] } {
  const items: SpotlightSearchItem[] = room > 0 ? [{
    id: 'file',
    title: 'A file from your computer…',
    category: CATEGORY.file,
    description: 'An image, a PDF, a CSV or a Pine script, sent to Anthropic with the request (a text cleaned of what looks like a secret)',
    keywords: ['upload', 'image', 'screenshot', 'png', 'pdf', 'csv', 'pine'],
    action: onFile,
  }] : []
  const picked: string[] = []
  const found: SpotlightSearchItem[] = []
  const offer = (subject: AgentSubject, entry: Omit<SpotlightSearchItem, 'action'>, into = items) => {
    const key = attachmentKey(subject)
    if (pending.has(key)) {
      picked.push(entry.id)
      into.push({ ...entry, action: () => onDetach(key) })
    } else if (!attached.has(key) && room > 0) {
      into.push({ ...entry, action: () => onAttach(subject) })
    }
  }

  // A number or a day typed: a position by its number, a trading day, whether listed or not. Each holds what was
  // typed among its words: the picker shows only the entries that hold every word typed.
  const typed = query.trim()
  const positions = lists.positions ?? []
  if (/^\d{4,}$/.test(typed) && !positions.some((position) => position.position_id === typed)) {
    offer({ kind: 'position', id: typed, label: `position ${typed}` },
      { id: `typed-position-${typed}`, title: `Position ${typed}`, category: CATEGORY.position, description: 'By its number',
        keywords: [typed] })
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(typed) && !(lists.days ?? []).some((day) => day.day_date === typed)) {
    offer({ kind: 'day', id: typed, label: `the day of ${typed}` },
      { id: `typed-day-${typed}`, title: `The trading day ${formatDay(typed)}`, category: CATEGORY.day, description: 'By its date',
        keywords: [typed] })
  }

  for (const run of lists.runs ?? []) {
    offer({ kind: 'run', id: run.id, label: run.name }, {
      id: `run-${run.id}`, title: run.name, category: CATEGORY.run, keywords: [run.id, ...run.tags],
      description: `${sourceLabel(run)} · ${formatDay(run.from)} to ${formatDay(run.to)} · ${STATE_LABEL[run.status.state]}`,
    })
  }
  for (const script of lists.scripts ?? []) {
    offer({ kind: 'script', id: script.id, version: script.version, label: script.name }, {
      id: `script-${script.id}`, title: script.name, category: CATEGORY.script,
      description: `${KIND_LABEL[script.kind]} · version ${script.version}`, keywords: [script.id],
    })
  }
  for (const rule of lists.rules ?? []) {
    offer({ kind: 'rule', id: rule.id, label: rule.name }, {
      id: `rule-${rule.id}`, title: rule.name, category: CATEGORY.rule, keywords: [rule.id, rule.indice],
      description: `${MODE_LABEL[rule.mode]} · ${rule.asset.key} · ${barsLabel(rule.bars)}`,
    })
  }
  for (const position of positions) {
    const label = positionLabel(position)
    offer({ kind: 'position', id: position.position_id, label }, {
      id: `position-${position.position_id}`, title: `Position ${position.position_id}`, category: CATEGORY.position,
      description: [label, position.execution_time_open ? formatDateTime(position.execution_time_open, timeZone) : null,
        position.position_status].filter(Boolean).join(' · '),
      keywords: [position.position_id, position.instrument_name ?? ''],
    })
  }
  for (const entry of lists.journal ?? []) {
    if (!entry.decided_at) continue
    // Its day as the journal counts it: the day its decision was taken, in the trading timezone (JournalPanel).
    const day = localDay(entry.decided_at, timeZone)
    offer({ kind: 'signal', id: entry.signal_uuid, day, label: `${entry.action ?? 'the'} signal of ${day}` }, {
      id: `signal-${entry.signal_uuid}`, title: `${entry.action ?? 'A'} signal, ${formatDateTime(entry.decided_at, timeZone)}`,
      category: CATEGORY.signal, description: [entry.outcome, entry.reason].filter(Boolean).join(' · '),
      keywords: [entry.signal_uuid, entry.indice ?? ''],
    })
  }
  for (const day of lists.days ?? []) {
    offer({ kind: 'day', id: day.day_date, label: `the day of ${day.day_date}` }, {
      id: `day-${day.day_date}`, title: `The trading day ${formatDay(day.day_date)}`, category: CATEGORY.day,
      description: `${day.trade_count} trade${day.trade_count === 1 ? '' : 's'} · ${formatPercent(day.performance_percent, true)}`,
      keywords: [day.day_date],
    })
  }
  const health = lists.health
  if (health) {
    // At the moment the health was read, as the System page asks about an entry (YYYY-MM-DD HH:MM, UTC).
    const at = health.checked_at.slice(0, 16).replace('T', ' ')
    for (const service of health.services.filter((entry) => entry.status !== 'ok')) {
      offer({ kind: 'incident', id: service.id, at, label: `${service.name} at ${at} UTC` }, {
        id: `incident-${service.id}`, title: `${service.name}: ${service.status}`, category: CATEGORY.incident,
        description: service.detail, keywords: [service.id],
      })
    }
  }
  // LuxAlgo's indicators found by LuxAlgo for what is typed in its list, listed as found (the picker's filter={false}).
  for (const indicator of lists.luxalgo ?? []) {
    if (indicator.kind !== 'indicator') continue
    offer({ kind: 'luxalgo', id: indicator.slug, label: `LuxAlgo's ${indicator.name}` }, {
      id: `luxalgo-${indicator.slug}`, title: indicator.name, category: CATEGORY.luxalgo,
      description: indicator.description ? indicator.description.slice(0, 140) : 'An indicator of LuxAlgo\'s Library',
      keywords: [indicator.slug],
    }, found)
  }
  if (luxalgo) {
    items.push({
      id: LUXALGO_LIST,
      title: 'LuxAlgo’s Library',
      category: CATEGORY.luxalgo,
      description: 'Its indicators, searched by LuxAlgo for what is typed',
      keywords: ['luxalgo', 'indicator', 'library'],
      placeholder: `An indicator’s name or idea, ${LUXALGO_FROM_CHARS} characters at least…`,
      items: found,
    })
  }
  return { items, picked }
}

/** Under the picker's list: what this message attaches, how many more it may, and how to finish. */
export function attachFooter(count: number, room: number): string {
  if (room <= 0) return `${fullSaid(MAX_ATTACHED) ?? ''} · Esc when done`
  const attached = count > 0 ? `${count} attached · ` : ''
  return `${attached}${room} more possible · Esc when done`
}
