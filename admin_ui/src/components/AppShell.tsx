import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ChartCandlestick,
  ChartLine,
  CirclePause,
  ClipboardList,
  Database,
  Eye,
  FileText,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Radio,
  Receipt,
  RotateCw,
  ScrollText,
  SlidersHorizontal,
  Timer,
  TrendingUp,
  Undo2,
} from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router'

import { ServiceStatusBadge } from '@/components/status'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Sidebar, SidebarTrigger, useSidebar } from '@/components/ui/sidebar'
import type { SidebarSection } from '@/components/ui/sidebar'
import { useAuth, useSession } from '@/features/auth/auth-context'
import { NotificationToggle } from '@/features/live/NotificationToggle'
import { useLiveEvents } from '@/features/live/useLiveEvents'
import { api } from '@/lib/api'
import { SAXO_SERVICE_ID } from '@/lib/types'
import { cn } from '@/lib/utils'

interface NavEntry {
  id: string
  path: string
  label: string
}

const NAVIGATION: (NavEntry & { section: string; icon: SidebarSection['items'][number]['icon'] })[] = [
  { id: 'dashboard', path: '/', label: 'Dashboard', section: 'Trading', icon: <LayoutDashboard /> },
  { id: 'positions', path: '/positions', label: 'Positions', section: 'Trading', icon: <ChartCandlestick /> },
  { id: 'orders', path: '/orders', label: 'Orders', section: 'Trading', icon: <Receipt /> },
  { id: 'signals', path: '/signals', label: 'Signals', section: 'Trading', icon: <Radio /> },
  { id: 'watchlist', path: '/watchlist', label: 'Watchlist', section: 'Trading', icon: <Eye /> },
  { id: 'performance', path: '/performance', label: 'Performance', section: 'Analysis', icon: <TrendingUp /> },
  { id: 'report', path: '/report', label: 'Report', section: 'Analysis', icon: <FileText /> },
  { id: 'exits', path: '/exits', label: 'Exits', section: 'Analysis', icon: <Undo2 /> },
  { id: 'timing', path: '/timing', label: 'Execution timing', section: 'Analysis', icon: <Timer /> },
  { id: 'backtests', path: '/backtests', label: 'Backtests', section: 'Analysis', icon: <FlaskConical /> },
  { id: 'providers', path: '/data/providers', label: 'Providers', section: 'Data', icon: <Database /> },
  { id: 'assets', path: '/data/assets', label: 'Assets View', section: 'Data', icon: <ChartLine /> },
  { id: 'system', path: '/system', label: 'System', section: 'Operations', icon: <Activity /> },
  { id: 'logs', path: '/logs', label: 'Logs', section: 'Operations', icon: <ScrollText /> },
  { id: 'audit', path: '/audit', label: 'Audit trail', section: 'Operations', icon: <ClipboardList /> },
  { id: 'config', path: '/config', label: 'Configuration', section: 'Operations', icon: <SlidersHorizontal /> },
]

const SECTIONS: SidebarSection[] = ['Trading', 'Analysis', 'Data', 'Operations'].map((title) => ({
  title,
  items: NAVIGATION.filter((entry) => entry.section === title).map(({ id, label, icon }) => ({ id, label, icon })),
}))

function activeEntry(pathname: string): NavEntry | undefined {
  return NAVIGATION.find((entry) =>
    entry.path === '/' ? pathname === '/' : pathname === entry.path || pathname.startsWith(`${entry.path}/`),
  )
}

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
 * screen the icon stays and the words go to the tooltip.
 */
function StateChip({ to, tone, icon, label, hint }: StateChipProps) {
  return (
    <Link
      to={to}
      title={hint}
      aria-label={`${label}. ${hint}`}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2 text-xs font-medium transition-colors lg:px-2.5',
        CHIP_TONE[tone],
      )}
    >
      {icon}
      <span className="hidden lg:inline">{label}</span>
    </Link>
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
  const session = useSession()
  const { logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [mobileOpen, setMobileOpen] = useState(false)

  const current = activeEntry(location.pathname)
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  // Shared with the dashboard, which refreshes it more often; a pause or a
  // resume is also announced by a live event, which refreshes it at once.
  const overviewQuery = useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 60_000 })
  const live = useLiveEvents()

  const entriesPaused = !!overviewQuery.data?.entries_paused_since
  const saxoNeedsAuthorisation =
    healthQuery.data?.services.find((service) => service.id === SAXO_SERVICE_ID)?.status === 'down'

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar
        sections={SECTIONS}
        activeId={current?.id ?? ''}
        onActiveChange={(id) => {
          const entry = NAVIGATION.find((candidate) => candidate.id === id)
          if (entry) navigate(entry.path)
        }}
        header={<SidebarHeader />}
        footer={<SignOutButton onSignOut={() => void logout()} />}
        mobileOpen={mobileOpen}
        onMobileOpenChange={setMobileOpen}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6">
          <SidebarTrigger onClick={() => setMobileOpen(true)} />
          <div className="flex min-w-0 flex-1 items-center gap-2">
            {saxoNeedsAuthorisation && (
              <StateChip
                to="/system"
                tone="danger"
                icon={<KeyRound className="size-3.5" aria-hidden />}
                label="Saxo: authorise"
                hint="The Saxo token can no longer be renewed: nothing can be traded until a new authorisation is given, on the System page."
              />
            )}
            {entriesPaused && (
              <StateChip
                to="/"
                tone="warning"
                icon={<CirclePause className="size-3.5" aria-hidden />}
                label="Entries paused"
                hint="Signals that would open a position are refused until entries are resumed, on the dashboard."
              />
            )}
            {session.restart_required && (
              <StateChip
                to="/config"
                tone="warning"
                icon={<RotateCw className="size-3.5" aria-hidden />}
                label="Restart needed"
                hint="The configuration changed since the services started: they apply it at their next start."
              />
            )}
          </div>
          <span
            className="hidden shrink-0 items-center gap-1.5 text-xs text-muted-foreground sm:flex"
            title={
              live
                ? 'Changes are pushed to this page as they happen'
                : 'Not receiving live updates: the page refreshes on a timer'
            }
          >
            <span aria-hidden className={live ? 'size-1.5 rounded-full bg-positive' : 'size-1.5 rounded-full bg-chart-muted'} />
            {live ? 'Live' : 'Polling'}
          </span>
          <NotificationToggle />
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
        <main className="relative flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
