import type { MenuEntry } from 'ioiti-ui'

import { SAXO_SERVICE_ID } from '@/lib/types'
import type { AgentState, Health, Overview, ServiceHealth, Session } from '@/lib/types'

/**
 * The badges of the sidebar's entries: what each page has to show now, read from what the shell reads anyway (the
 * health, the overview, the session) and the AI agent's state. A count is quiet; a state says itself in a word, in
 * its tone. An entry closed, a section folded or the rail say their badges by a dot (ioiti-ui's sidebar). The top
 * bar's summary says the same states (stateItems.ts): a badge says where to go for one.
 */

export type MenuBadge = Pick<MenuEntry, 'badge' | 'badgeTone'>

export interface MenuFacts {
  health?: Health
  overview?: Overview
  session: Session
  /** The AI agent's state; absent while it is not read (or off). */
  agent?: AgentState
}

const DOWN: MenuBadge = { badge: 'Down', badgeTone: 'danger' }
const WARNING: MenuBadge = { badge: 'Warning', badgeTone: 'warning' }

/** The worst of some entries of the health, as a badge: nothing while they are well (or unknown, or off). */
function healthBadge(services: readonly ServiceHealth[], ids: readonly string[], down = DOWN, warning = WARNING): MenuBadge | undefined {
  const chosen = services.filter((service) => ids.includes(service.id))
  if (chosen.some((service) => service.status === 'down')) return down
  if (chosen.some((service) => service.status === 'warning')) return warning
  return undefined
}

function counted(count: number, one: string, several: string): string {
  return `${count} ${count === 1 ? one : several}`
}

/** Each entry's badge, by the entry's id (navigation.ts); an entry with nothing to show is absent. */
export function menuBadges({ health, overview, session, agent }: MenuFacts): Record<string, MenuBadge> {
  const services = health?.services ?? []
  const badges: Record<string, MenuBadge | undefined> = {}

  // Trading: new entries paused (the operator's, or a guard's), the positions open, the sales Saxo refuses.
  if (overview?.entries_paused_since) badges.dashboard = { badge: 'Paused', badgeTone: 'warning' }
  const open = overview?.open_positions ?? []
  if (open.length > 0) badges.positions = { badge: open.length }
  const refused = open.filter((position) => (position.sell_refusals?.count ?? 0) > 0).length
  if (refused > 0) badges.orders = { badge: `${refused} refused`, badgeTone: 'danger' }

  // The services a page is about, as the health judges them.
  badges.watchlist = healthBadge(services, ['watchlist_manager'])
  badges.providers = healthBadge(services, ['price_recorder', 'capital_feed'])
  badges['ta-monitor'] = healthBadge(services, ['ta_engine'])
  // Edge Watch says a warning for live trades judged broken or worth watching, and for a guard that cannot act.
  badges.performance = healthBadge(services, ['edge_watch'], DOWN, { badge: 'Watch', badgeTone: 'warning' })
  badges.disk = healthBadge(services, ['disk'], { badge: 'Critical', badgeTone: 'danger' })

  // The AI agent: a research task's question waits for the operator, then its pause, then what it is doing.
  if (agent?.enabled) {
    const asking = agent.asking?.length ?? 0
    const underWay = agent.under_way?.length ?? 0
    if (asking > 0) badges.agent = { badge: counted(asking, 'question', 'questions'), badgeTone: 'warning' }
    else if (agent.pause) badges.agent = { badge: 'Paused', badgeTone: 'warning' }
    else if (underWay > 0) badges.agent = { badge: underWay }
  }

  // The System: Saxo's login first (nothing trades without it), the services, then a release and restarts.
  const saxoDown = services.some((service) => service.id === SAXO_SERVICE_ID && service.status === 'down')
  const release = session.release
  if (saxoDown) badges.system = { badge: 'Authorise', badgeTone: 'danger' }
  else if (health?.status === 'down') badges.system = DOWN
  else if (health?.status === 'warning') badges.system = WARNING
  else if (release?.applying) badges.system = { badge: 'Deploying', badgeTone: 'warning' }
  else if (release && release.waiting.length > 0) badges.system = { badge: 'Release', badgeTone: 'warning' }
  else if (session.restart_required && session.restart_services) badges.system = { badge: 'Restart', badgeTone: 'warning' }
  // A change saved that the control service cannot place: the Configuration says which services read it.
  if (session.restart_required && !session.restart_services) badges.config = { badge: 'Restart', badgeTone: 'warning' }

  return Object.fromEntries(Object.entries(badges).filter((entry): entry is [string, MenuBadge] => entry[1] !== undefined))
}
