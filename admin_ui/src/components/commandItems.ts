import { formatDay } from 'ioiti-ui'
import type { SpotlightSearchItem } from 'ioiti-ui'

import { MENU } from '@/components/navigation'
import { KIND_LABEL as MEMORY_KIND_LABEL, memoryLink } from '@/features/agent/memoryModel'
import { sourceLabel, STATE_LABEL } from '@/features/backtests/model'
import { assetHref, assetKeywords, PROVIDER_LABELS } from '@/features/data/assets'
import { isSignalUuid, orderLink, positionLink, signalLink } from '@/features/positions/orderLife'
import { instrumentLabel } from '@/features/prices/chartData'
import { barsLabel, MODE_LABEL, rulePath } from '@/features/ta/signals/ruleModel'
import { KIND_LABEL } from '@/features/ta/templates'
import type {
  AgentConversationListed,
  AgentMemoryEntry,
  BacktestConfiguration,
  BacktestRunListed,
  DataAsset,
  TaRule,
  TaScriptSummary,
} from '@/lib/types'

/**
 * What the palette (⌘K) goes to, as entries: the pages of the menu and what
 * the pages list. Places only: nothing here trades or changes anything.
 */

export function pageItems(): SpotlightSearchItem[] {
  return [
    ...MENU.map((entry) => ({ id: `page-${entry.id}`, title: entry.label, category: entry.section, href: entry.path })),
    { id: 'new-backtest', title: 'New backtest', category: 'Analysis', href: '/backtests/new', keywords: ['create', 'run'] },
    { id: 'new-script', title: 'New script', category: 'Technical Analysis', href: '/ta/indicators/new', keywords: ['create', 'indicator', 'strategy', 'library', 'pine'] },
  ]
}

/** The backtest configurations: their page outlives their runs, a nightly one replaced each evening. */
export function configurationItems(configurations: readonly BacktestConfiguration[]): SpotlightSearchItem[] {
  return configurations.map((configuration) => ({
    id: `configuration-${configuration.id}`,
    title: configuration.name,
    category: 'Backtest configuration',
    description: `${sourceLabel(configuration)} · ${configuration.count} run${configuration.count === 1 ? '' : 's'}${configuration.nightly?.on ? ' · each evening' : ''}`,
    href: `/backtests/configurations/${configuration.id}`,
    keywords: [configuration.id, ...configuration.tags],
  }))
}

export function runItems(runs: readonly BacktestRunListed[]): SpotlightSearchItem[] {
  return runs.map((run) => ({
    id: `run-${run.id}`,
    title: run.name,
    category: 'Backtest',
    description: `${sourceLabel(run)} · ${formatDay(run.from)} to ${formatDay(run.to)} · ${STATE_LABEL[run.status.state]}`,
    href: `/backtests/${run.id}`,
    keywords: [run.id, ...run.tags],
  }))
}

/** The AI agent's conversations, by their title and what they are about. */
export function conversationItems(conversations: readonly AgentConversationListed[]): SpotlightSearchItem[] {
  return conversations.map((conversation) => ({
    id: `conversation-${conversation.id}`,
    title: conversation.title,
    category: 'AI agent',
    description: conversation.subject ? `About ${conversation.subject.label}` : undefined,
    href: `/agent/${conversation.id}`,
    keywords: [conversation.id, conversation.subject?.id ?? ''],
  }))
}

/** The AI agent's memory, an entry by its name and its line: each opens on the Memory page. */
export function memoryItems(entries: readonly AgentMemoryEntry[]): SpotlightSearchItem[] {
  return entries.map((entry) => ({
    id: `memory-${entry.name}`,
    title: entry.name,
    category: 'Memory',
    description: `${MEMORY_KIND_LABEL[entry.kind]} · ${entry.description}`,
    href: memoryLink(entry.name),
    keywords: [entry.kind],
  }))
}

/** A rule leads to its own page. */
export function ruleItems(rules: readonly TaRule[]): SpotlightSearchItem[] {
  return rules.map((rule) => ({
    id: `rule-${rule.id}`,
    title: rule.name,
    category: 'Signal rule',
    description: `${MODE_LABEL[rule.mode]} · ${rule.asset.key} · ${barsLabel(rule.bars)}`,
    href: rulePath(rule.id),
    keywords: [rule.indice, rule.script_name ?? ''],
  }))
}

export function scriptItems(scripts: readonly TaScriptSummary[]): SpotlightSearchItem[] {
  return scripts.map((script) => ({
    id: `script-${script.id}`,
    title: script.name,
    category: KIND_LABEL[script.kind],
    description: `Version ${script.version}`,
    href: `/ta/indicators/${script.id}`,
  }))
}

/** The assets with prices to look at: a turbo no longer quoted is left out (thousands of them). */
export function assetItems(assets: readonly DataAsset[]): SpotlightSearchItem[] {
  return assets
    .filter((asset) => !(asset.role === 'turbo' && asset.status === 'ended'))
    .map((asset) => ({
      id: `asset-${asset.ref}`,
      title: instrumentLabel(asset),
      category: 'Asset',
      description: `${PROVIDER_LABELS[asset.provider]} · ${asset.symbol ?? asset.key}`,
      href: assetHref(asset),
      // A knock-out level is found as typed, without its thousands separator.
      keywords: assetKeywords(asset),
    }))
}

/**
 * What the pages list, each kind as it reaches the palette: entries made from it, undefined while it is read, null
 * where there is none to list (backtests off, the AI agent off).
 */
export interface PaletteLists {
  configurations?: SpotlightSearchItem[] | null
  runs?: SpotlightSearchItem[] | null
  rules?: SpotlightSearchItem[] | null
  scripts?: SpotlightSearchItem[] | null
  assets?: SpotlightSearchItem[] | null
  conversations?: SpotlightSearchItem[] | null
  memory?: SpotlightSearchItem[] | null
}

/** The lists the palette opens, in this order, each searched alone ("Backtest runs ›"). */
const LISTS: { key: keyof PaletteLists; title: string; one: string; many: string; placeholder: string }[] = [
  { key: 'configurations', title: 'Backtest configurations', one: 'configuration', many: 'configurations', placeholder: 'A configuration’s name or tag…' },
  { key: 'runs', title: 'Backtest runs', one: 'run', many: 'runs', placeholder: 'A run’s name, id or tag…' },
  { key: 'rules', title: 'Signal rules', one: 'rule', many: 'rules', placeholder: 'A rule’s name, its script or indice…' },
  { key: 'scripts', title: 'Scripts', one: 'script', many: 'scripts', placeholder: 'A script’s name…' },
  { key: 'assets', title: 'Assets', one: 'asset', many: 'assets', placeholder: 'An asset, a turbo’s knock-out level…' },
  { key: 'conversations', title: 'AI agent conversations', one: 'conversation', many: 'conversations', placeholder: 'A conversation’s title or subject…' },
  { key: 'memory', title: 'Memory entries', one: 'entry', many: 'entries', placeholder: 'An entry’s name or line…' },
]

/**
 * A kind of thing as a list of its own. Its entries are always an array, empty while they are read: the palette
 * finds the list it shows again by its id only while its entries are given (ioiti-ui's `items`).
 */
export function listItems(lists: PaletteLists): SpotlightSearchItem[] {
  return LISTS.flatMap(({ key, title, one, many, placeholder }) => {
    const entries = lists[key]
    if (entries === null) return []
    return [{
      id: `list-${key}`,
      title,
      category: 'Lists',
      description: entries === undefined ? 'Being read…' : `${entries.length} ${entries.length === 1 ? one : many}`,
      placeholder,
      items: entries ?? [],
    }]
  })
}

/**
 * Everything the palette offers for what is typed at its top. With nothing typed: the lists, then the pages (the
 * palette puts the entries chosen last before them). Typed: a number as a position or an order, the pages, the lists,
 * and every thing the lists hold, each found by its own words as before.
 */
export function paletteItems({ query, pages, lists }: { query: string; pages: SpotlightSearchItem[]; lists: PaletteLists }): SpotlightSearchItem[] {
  const listed = listItems(lists)
  if (query.trim() === '') return [...listed, ...pages]
  return [...idItems(query), ...pages, ...listed, ...LISTS.flatMap(({ key }) => lists[key] ?? [])]
}

/** A number typed may be a position's or an order's: their pages find it. A signal's uuid (pasted) opens its page. */
export function idItems(query: string): SpotlightSearchItem[] {
  const id = query.trim()
  const uuid = id.toLowerCase()
  if (isSignalUuid(uuid)) {
    return [{ id: `signal-${uuid}`, title: `Signal ${uuid.slice(0, 8)}…`, category: 'Trading', href: signalLink(uuid), keywords: [uuid] }]
  }
  if (!/^\d{4,}$/.test(id)) return []
  return [
    { id: `position-${id}`, title: `Position ${id}`, category: 'Trading', href: positionLink(id), keywords: [id] },
    { id: `order-${id}`, title: `Order ${id}`, category: 'Trading', href: orderLink(id), keywords: [id] },
  ]
}
