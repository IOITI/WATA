import { MENU } from '@/components/navigation'
import type { SpotlightSearchItem } from '@/components/ui/spotlight-search'
import { sourceLabel, STATE_LABEL } from '@/features/backtests/model'
import { assetHref, assetKeywords, PROVIDER_LABELS } from '@/features/data/assets'
import { orderLink, positionLink } from '@/features/positions/orderLife'
import { instrumentLabel } from '@/features/prices/chartData'
import { barsLabel, MODE_LABEL } from '@/features/ta/signals/ruleModel'
import { KIND_LABEL } from '@/features/ta/templates'
import { formatDay } from '@/lib/format'
import type { BacktestRunListed, DataAsset, TaRule, TaScriptSummary } from '@/lib/types'

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

/** A rule has no page of its own: it leads to the list it is changed from. */
export function ruleItems(rules: readonly TaRule[]): SpotlightSearchItem[] {
  return rules.map((rule) => ({
    id: `rule-${rule.id}`,
    title: rule.name,
    category: 'Signal rule',
    description: `${MODE_LABEL[rule.mode]} · ${rule.asset.key} · ${barsLabel(rule.bars)}`,
    href: '/ta/signals',
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

/** A number typed may be a position's or an order's: their pages find it. */
export function idItems(query: string): SpotlightSearchItem[] {
  const id = query.trim()
  if (!/^\d{4,}$/.test(id)) return []
  return [
    { id: `position-${id}`, title: `Position ${id}`, category: 'Trading', href: positionLink(id), keywords: [id] },
    { id: `order-${id}`, title: `Order ${id}`, category: 'Trading', href: orderLink(id), keywords: [id] },
  ]
}
