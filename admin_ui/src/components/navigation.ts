import type { Menu, MenuEntry, MenuSection as MenuSectionOptions } from 'ioiti-ui'

/**
 * The menu of the interface, and where each page stands in it: the sidebar
 * lists it (with its icons, AppShell.tsx, and its badges, menuBadges.ts), and
 * the trail of the top bar says it, from this one table. ioiti-ui's shell
 * reads it (`ADMIN_MENU`): which entry a page is under (`menuEntry`) and its
 * trail (`trailFor`) are the package's. An entry under another (`parent`) is
 * listed under it in the sidebar, and its trail goes through it.
 */

/** The interface's name: in the sidebar, and at the end of every tab's title ("Orders · WATA Admin"). */
export const APP_NAME = 'WATA Admin'

export type MenuSection = 'Trading' | 'Analysis' | 'Data' | 'Technical Analysis' | 'AI agent' | 'Operations'

export const MENU_SECTIONS: MenuSection[] = ['Trading', 'Analysis', 'Data', 'Technical Analysis', 'AI agent', 'Operations']

// How the sidebar draws each section. Trading stays open; the others fold under their heading, each reader's choice
// kept by the browser, the one of the page shown always open. The AI agent is one entry, at the sidebar's foot above
// Collapse, apart from the sections (without a heading there, it read as one of the section above's): no step of a
// section in its trail ("Home › AI agent › Memory").
const SECTION_OPTIONS: Record<MenuSection, Omit<MenuSectionOptions<MenuSection>, 'id'>> = {
  Trading: {},
  Analysis: { foldable: true },
  Data: { foldable: true },
  'Technical Analysis': { foldable: true },
  'AI agent': { place: 'footer' },
  Operations: { foldable: true },
}

export const MENU: MenuEntry<MenuSection>[] = [
  { id: 'dashboard', path: '/', label: 'Dashboard', section: 'Trading' },
  { id: 'positions', path: '/positions', label: 'Positions', section: 'Trading' },
  { id: 'orders', path: '/orders', label: 'Orders', section: 'Trading' },
  { id: 'signals', path: '/signals', label: 'Signals', section: 'Trading' },
  { id: 'watchlist', path: '/watchlist', label: 'Watchlist', section: 'Trading' },
  { id: 'performance', path: '/performance', label: 'Performance', section: 'Analysis' },
  { id: 'report', path: '/report', label: 'Report', section: 'Analysis' },
  { id: 'exits', path: '/exits', label: 'Exits', section: 'Analysis' },
  { id: 'timing', path: '/timing', label: 'Execution timing', section: 'Analysis' },
  { id: 'backtests', path: '/backtests', label: 'Backtests', section: 'Analysis' },
  // Each shadow rule's paper days and report, and the signals WATA received, traded on paper.
  { id: 'paper', path: '/backtests/paper', label: 'Paper trading', section: 'Analysis', parent: 'backtests' },
  { id: 'experiments', path: '/backtests/experiments', label: 'Experiments', section: 'Analysis', parent: 'backtests' },
  { id: 'providers', path: '/data/providers', label: 'Providers', section: 'Data' },
  { id: 'assets', path: '/data/assets', label: 'Assets View', section: 'Data' },
  { id: 'ta-indicators', path: '/ta/indicators', label: 'Indicators', section: 'Technical Analysis' },
  // LuxAlgo's concepts and indicators, read through the admin; an indicator taken becomes one of the Indicators.
  { id: 'ta-luxalgo', path: '/ta/luxalgo', label: 'LuxAlgo Library', section: 'Technical Analysis', parent: 'ta-indicators' },
  { id: 'ta-charts', path: '/ta/charts', label: 'Charts', section: 'Technical Analysis' },
  // "Signal rules": the Trading section's Signals page sends signals by hand.
  { id: 'ta-signals', path: '/ta/signals', label: 'Signal rules', section: 'Technical Analysis' },
  // How the engine runs the rules, under them.
  { id: 'ta-monitor', path: '/ta/monitor', label: 'Engine monitor', section: 'Technical Analysis', parent: 'ta-signals' },
  // Claude from these pages: its conversations (its page, a conversation's under it), what it keeps between them,
  // what they made, what it does by itself.
  { id: 'agent', path: '/agent', label: 'AI agent', section: 'AI agent' },
  { id: 'agent-memory', path: '/agent/memory', label: 'Memory', section: 'AI agent', parent: 'agent' },
  { id: 'agent-artefacts', path: '/agent/artefacts', label: 'Artefacts', section: 'AI agent', parent: 'agent' },
  { id: 'agent-routines', path: '/agent/routines', label: 'Routines', section: 'AI agent', parent: 'agent' },
  { id: 'agent-queue', path: '/agent/queue', label: 'Research queue', section: 'AI agent', parent: 'agent' },
  { id: 'system', path: '/system', label: 'System', section: 'Operations' },
  // The server's disk, as watadisk samples it every hour on the host, and where it goes.
  { id: 'disk', path: '/system/disk', label: 'Disk', section: 'Operations', parent: 'system' },
  { id: 'logs', path: '/logs', label: 'Logs', section: 'Operations', parent: 'system' },
  { id: 'audit', path: '/audit', label: 'Audit trail', section: 'Operations', parent: 'system' },
  { id: 'config', path: '/config', label: 'Configuration', section: 'Operations' },
]

/** The menu as ioiti-ui's shell takes it. */
export const ADMIN_MENU: Menu<MenuSection> = {
  sections: MENU_SECTIONS.map((id) => ({ id, ...SECTION_OPTIONS[id] })),
  entries: MENU,
}

/** The way home of the admin's 404s: the dashboard is its home page. */
export const DASHBOARD_HOME = { href: '/', label: 'Back to the dashboard' }

// The pages that take the whole width: an editor beside its test, a chart.
const WIDE_PAGES = [/^\/ta\/indicators\/[^/]+\/?$/, /^\/ta\/charts\/?$/]

/** A page without the width cap of the others. */
export function isWidePage(pathname: string): boolean {
  return WIDE_PAGES.some((pattern) => pattern.test(pathname))
}
