import { useQuery } from '@tanstack/react-query'
import { Hint, SidebarFooterButton, StateChip, StatusBadge, ThemeToggle } from 'ioiti-ui'
import type { Menu } from 'ioiti-ui'
import { RouterAppShell, RouterLink } from 'ioiti-ui/react-router'
import {
  Activity,
  Bot,
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
  Package,
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
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { CommandPalette } from '@/components/CommandPalette'
import { isWidePage, MENU, MENU_SECTIONS } from '@/components/navigation'
import type { MenuSection } from '@/components/navigation'
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

// The menu itself is in navigation.ts, which the trail of the top bar reads too.
const ICONS: Record<string, ReactNode> = {
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
  agent: <Bot />,
  'agent-artefacts': <Package />,
  system: <Activity />,
  logs: <ScrollText />,
  audit: <ClipboardList />,
  config: <SlidersHorizontal />,
}

// Each entry a link: it can be opened in another tab as well as followed.
const SIDEBAR_MENU: Menu<MenuSection> = {
  sections: MENU_SECTIONS,
  entries: MENU.map((entry) => ({ ...entry, icon: ICONS[entry.id] })),
}

/**
 * ioiti-ui's shell (the sidebar, the top bar with the trail of where the page is, the page) with what
 * is WATA's in it: the states the operator must not miss, the live pill, the palette, the bell, the
 * services' state, signing out.
 */
export function AppShell() {
  return (
    <EventLogProvider>
      <FollowProvider>
        <PendingRemovalsProvider>
          <Shell />
        </PendingRemovalsProvider>
      </FollowProvider>
    </EventLogProvider>
  )
}

function Shell() {
  const session = useSession()
  const { logout } = useAuth()
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
    <RouterAppShell
      menu={SIDEBAR_MENU}
      appDetail={`v${session.version}`}
      linkComponent={RouterLink}
      isWide={isWidePage}
      sidebarFooter={<SidebarFooterButton icon={<LogOut />} label="Sign out" onClick={() => void logout()} />}
      topBarChips={
        <>
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
        </>
      }
      topBarEnd={
        <>
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
              <StatusBadge status={healthQuery.data.status} />
            </Link>
          )}
        </>
      }
    />
  )
}
