import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ChartCandlestick,
  ChartLine,
  ChartSpline,
  ClipboardList,
  Database,
  Eye,
  FileText,
  FlaskConical,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Radar,
  Radio,
  Receipt,
  RotateCw,
  ScrollText,
  SlidersHorizontal,
  SquareFunction,
  Timer,
  TrendingUp,
  Undo2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

import { CommandPalette } from '@/components/CommandPalette'
import { Hint } from '@/components/Hint'
import { MainScrollContext } from '@/components/main-scroll-context'
import { isWidePage, MENU, MENU_SECTIONS, menuEntry } from '@/components/navigation'
import { PageTrailProvider, TopBarTrail } from '@/components/PageTrail'
import { RouterLink } from '@/components/RouterLink'
import { ServiceStatusBadge } from '@/components/status'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Sidebar, SidebarTrigger, useSidebar } from '@/components/ui/sidebar'
import type { SidebarSection } from '@/components/ui/sidebar'
import { useAuth, useSession } from '@/features/auth/auth-context'
import { EventLogProvider } from '@/features/live/EventLogProvider'
import { PendingRemovalsProvider } from '@/features/backtests/PendingRemovalsProvider'
import { LivePill } from '@/features/dashboard/LivePill'
import { EventsBell } from '@/features/live/EventsBell'
import { FollowProvider } from '@/features/live/FollowProvider'
import { recordLiveValues } from '@/features/positions/liveSamples'
import { useLiveEvents } from '@/features/live/useLiveEvents'
import { restartNeededHint } from '@/features/system/restarts'
import { api } from '@/lib/api'
import { SAXO_SERVICE_ID } from '@/lib/types'
import { cn } from '@/lib/utils'

// The menu itself is in navigation.ts, which the trail of the top bar reads too.
const ICONS: Record<string, SidebarSection['items'][number]['icon']> = {
  dashboard: <LayoutDashboard />,
  positions: <ChartCandlestick />,
  orders: <Receipt />,
  signals: <Radio />,
  watchlist: <Eye />,
  performance: <TrendingUp />,
  report: <FileText />,
  exits: <Undo2 />,
  timing: <Timer />,
  backtests: <FlaskConical />,
  providers: <Database />,
  assets: <ChartLine />,
  'ta-indicators': <SquareFunction />,
  'ta-charts': <ChartSpline />,
  'ta-signals': <Radar />,
  'ta-monitor': <Gauge />,
  system: <Activity />,
  logs: <ScrollText />,
  audit: <ClipboardList />,
  config: <SlidersHorizontal />,
}

// Each entry a link: it can be opened in another tab as well as followed.
const SECTIONS: SidebarSection[] = MENU_SECTIONS.map((title) => ({
  title,
  items: MENU.filter((entry) => entry.section === title).map(({ id, label, path }) => ({ id, label, icon: ICONS[id], href: path })),
}))

// The 400 steps are made for a dark surface; on a light one the 800 steps carry the text.
const CHIP_TONE = {
  warning: 'bg-amber-500/15 text-amber-800 hover:bg-amber-500/25 dark:bg-amber-500/10 dark:text-amber-400 dark:hover:bg-amber-500/20',
  danger: 'bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-400',
}

interface StateChipProps {
  to: string
  tone: keyof typeof CHIP_TONE
  icon: ReactNode
  label: string
  /** What it means and what to do, for the tooltip. */
  hint: string
}

/**
 * A state the operator must not miss, on whichever page they are: shown only
 * while it applies, and a link to the page where it is dealt with. On a narrow
 * screen the icon stays and the words are its name; what it means is its hint.
 */
function StateChip({ to, tone, icon, label, hint }: StateChipProps) {
  return (
    <Hint content={hint} side="bottom">
      <Link
        to={to}
        aria-label={label}
        className={cn(
          'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2 text-xs font-medium transition-colors lg:px-2.5',
          CHIP_TONE[tone],
        )}
      >
        {icon}
        <span className="hidden lg:inline">{label}</span>
      </Link>
    </Hint>
  )
}

function SidebarHeader() {
  const { version } = useSession()
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-7 shrink-0 place-items-center rounded-lg bg-foreground text-xs font-bold text-background">
        W
      </div>
      <div className="min-w-0 leading-tight">
        <p className="truncate text-sm font-semibold">WATA Admin</p>
        <p className="truncate text-[11px] text-muted-foreground">v{version}</p>
      </div>
    </div>
  )
}

function SignOutButton({ onSignOut }: { onSignOut: () => void }) {
  const { collapsed } = useSidebar()
  return (
    <button
      type="button"
      onClick={onSignOut}
      aria-label="Sign out"
      className="flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm text-neutral-600 outline-none transition-colors hover:bg-neutral-200/70 hover:text-neutral-900 focus-visible:ring-2 focus-visible:ring-neutral-400/60 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-white dark:focus-visible:ring-neutral-600/60"
    >
      <span className="grid size-5 shrink-0 place-items-center">
        <LogOut className="size-[18px]" />
      </span>
      {!collapsed && <span className="flex-1 whitespace-nowrap text-left">Sign out</span>}
    </button>
  )
}

export function AppShell() {
  return (
    <PageTrailProvider>
      <EventLogProvider>
        <FollowProvider>
          <PendingRemovalsProvider>
            <Shell />
          </PendingRemovalsProvider>
        </FollowProvider>
      </EventLogProvider>
    </PageTrailProvider>
  )
}

function Shell() {
  const session = useSession()
  const { logout } = useAuth()
  const location = useLocation()
  const wide = isWidePage(location.pathname)
  const [mobileOpen, setMobileOpen] = useState(false)
  const main = useRef<HTMLElement>(null)
  // Another page opens at its top: where the one left was scrolled to means nothing there. A change
  // of the query alone (a trade opened on a day of a backtest) leaves the page where it is.
  useEffect(() => {
    if (main.current) main.current.scrollTop = 0
  }, [location.pathname])

  const current = menuEntry(location.pathname)
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  // Shared with the dashboard, which refreshes it more often; a pause or a
  // resume is also announced by a live event, which refreshes it at once. While
  // a position is open its live value is read as often as the dashboard reads it.
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: (query) => (query.state.data?.open_positions?.length ? 5_000 : 60_000),
  })
  // Every page's reading of the live values, kept for the lines of the open positions.
  useEffect(() => {
    if (overviewQuery.data) recordLiveValues(overviewQuery.data.open_positions ?? [])
  }, [overviewQuery.data])
  const live = useLiveEvents()

  const saxoNeedsAuthorisation =
    healthQuery.data?.services.find((service) => service.id === SAXO_SERVICE_ID)?.status === 'down'

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar
        sections={SECTIONS}
        activeId={current?.id ?? ''}
        linkComponent={RouterLink}
        header={<SidebarHeader />}
        footer={<SignOutButton onSignOut={() => void logout()} />}
        mobileOpen={mobileOpen}
        onMobileOpenChange={setMobileOpen}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6">
          <SidebarTrigger onClick={() => setMobileOpen(true)} />
          {/* Where the page is, on every page: it takes the room left, and gives it up first. */}
          <div className="flex min-w-0 flex-1 items-center">
            <TopBarTrail />
          </div>
          <div className="flex shrink-0 items-center gap-2 empty:hidden">
            {saxoNeedsAuthorisation && (
              <StateChip
                to="/system"
                tone="danger"
                icon={<KeyRound className="size-3.5" aria-hidden />}
                label="Saxo: authorise"
                hint="The Saxo token can no longer be renewed: nothing can be traded until a new authorisation is given, on the System page."
              />
            )}
            {session.restart_required && (
              <StateChip
                to={session.restart_services ? '/system' : '/config'}
                tone="warning"
                icon={<RotateCw className="size-3.5" aria-hidden />}
                label="Restart needed"
                hint={restartNeededHint(session)}
              />
            )}
          </div>
          <LivePill />
          <Hint
            content={live ? 'Changes are pushed to this page as they happen' : 'Not receiving live updates: the page refreshes on a timer'}
            side="bottom"
            wrap
            className="hidden shrink-0 sm:inline-flex"
          >
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className={live ? 'size-1.5 rounded-full bg-positive' : 'size-1.5 rounded-full bg-chart-muted'} />
              {live ? 'Live' : 'Polling'}
            </span>
          </Hint>
          <CommandPalette />
          <EventsBell />
          <ThemeToggle />
          {healthQuery.data && (
            <Link to="/system" className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              <span className="hidden sm:inline">Services</span>
              <ServiceStatusBadge status={healthQuery.data.status} />
            </Link>
          )}
        </header>

        {/* Positioned, so that what it scrolls stays in it: text for screen readers placed
            against the page would otherwise make the window itself scroll, onto nothing. */}
        <main ref={main} className="relative flex-1 overflow-y-auto">
          <div className={cn('mx-auto flex w-full flex-col gap-6 p-4 sm:p-6', wide ? 'max-w-none' : 'max-w-7xl')}>
            <MainScrollContext.Provider value={main}>
              <Outlet />
            </MainScrollContext.Provider>
          </div>
        </main>
      </div>
    </div>
  )
}
