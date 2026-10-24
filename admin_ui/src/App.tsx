import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'framer-motion'
import { lazy, Suspense, useState } from 'react'
import type { ReactNode } from 'react'
import { createBrowserRouter, createRoutesFromElements, Navigate, Outlet, Route, RouterProvider, useLocation } from 'react-router'

import { AppShell } from '@/components/AppShell'
import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { Skeleton } from '@/components/ui/skeleton'
import { AuditPage } from '@/features/audit/AuditPage'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { BacktestsPage } from '@/features/backtests/BacktestsPage'
import { NewBacktestPage } from '@/features/backtests/NewBacktestPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { ConfigPage } from '@/features/config/ConfigPage'
import {
  ConfigFileView,
  ConfigGroupPage,
  ConfigJsonView,
  ConfigOverview,
  ConfigVersionsView,
} from '@/features/config/ConfigViews'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { legacyPricesTarget } from '@/features/data/assets'
import { ProviderPage } from '@/features/data/ProviderPage'
import { ProvidersPage } from '@/features/data/ProvidersPage'
import { LogsPage } from '@/features/logs/LogsPage'
import { NotFoundPage } from '@/features/NotFoundPage'
import { OrdersPage } from '@/features/positions/OrdersPage'
import { PositionsPage } from '@/features/positions/PositionsPage'
import { SignalsPage } from '@/features/signals/SignalsPage'
import { SystemPage } from '@/features/system/SystemPage'
import { TimingPage } from '@/features/timing/TimingPage'
import { WatchlistPage } from '@/features/watchlist/WatchlistPage'
import { ApiError } from '@/lib/api'

// The charting library is a large part of the bundle and only these pages use it.
const PerformancePage = lazy(() =>
  import('@/features/performance/PerformancePage').then((module) => ({ default: module.PerformancePage })),
)
const ReportPage = lazy(() =>
  import('@/features/report/ReportPage').then((module) => ({ default: module.ReportPage })),
)
const ExitsPage = lazy(() => import('@/features/exits/ExitsPage').then((module) => ({ default: module.ExitsPage })))
// Their own charting library, for candles: loaded with the pages as well.
const AssetsPage = lazy(() => import('@/features/data/AssetsPage').then((module) => ({ default: module.AssetsPage })))
const AssetPage = lazy(() => import('@/features/data/AssetPage').then((module) => ({ default: module.AssetPage })))
// Technical Analysis: an editor and charts of their own (CodeMirror, Vela, PineTS), loaded with these pages only.
const IndicatorsPage = lazy(() => import('@/features/ta/IndicatorsPage').then((module) => ({ default: module.IndicatorsPage })))
const ScriptEditorPage = lazy(() =>
  import('@/features/ta/ScriptEditorPage').then((module) => ({ default: module.ScriptEditorPage })),
)
const ChartsPage = lazy(() => import('@/features/ta/ChartsPage').then((module) => ({ default: module.ChartsPage })))
const SignalRulesPage = lazy(() =>
  import('@/features/ta/signals/SignalRulesPage').then((module) => ({ default: module.SignalRulesPage })),
)
const MonitorPage = lazy(() => import('@/features/ta/monitor/MonitorPage').then((module) => ({ default: module.MonitorPage })))

// A run's pages: the performance charts, and a day's candles (Lightweight Charts).
const BacktestRunPage = lazy(() => import('@/features/backtests/RunPage').then((module) => ({ default: module.RunPage })))
const BacktestDayPage = lazy(() => import('@/features/backtests/DayPage').then((module) => ({ default: module.DayPage })))
const BacktestComparePage = lazy(() =>
  import('@/features/backtests/ComparePage').then((module) => ({ default: module.ComparePage })),
)
const ExperimentsPage = lazy(() =>
  import('@/features/backtests/ExperimentsPage').then((module) => ({ default: module.ExperimentsPage })),
)

/** An address of the former Prices page: to the Assets View, or to the asset it showed. */
function PricesRedirect() {
  const location = useLocation()
  return <Navigate to={legacyPricesTarget(location.search)} replace />
}

function ChartPage({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Skeleton className="h-40 w-full rounded-2xl" />}>{children}</Suspense>
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A 4xx will not get better by asking again; a network blip or a 5xx may.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failureCount < 2,
        refetchOnWindowFocus: true,
        staleTime: 2_000,
      },
    },
  })
}

/** What every page needs around it, the login included. */
function Root() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Outlet />
      </ToastProvider>
    </AuthProvider>
  )
}

// A data router: the configuration page can then ask before unsaved changes are left behind.
const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<Root />}>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="positions" element={<PositionsPage />} />
        <Route path="orders" element={<OrdersPage />} />
        <Route
          path="performance"
          element={
            <ChartPage>
              <PerformancePage />
            </ChartPage>
          }
        />
        <Route
          path="report"
          element={
            <ChartPage>
              <ReportPage />
            </ChartPage>
          }
        />
        <Route
          path="exits"
          element={
            <ChartPage>
              <ExitsPage />
            </ChartPage>
          }
        />
        <Route path="data" element={<Navigate to="/data/assets" replace />} />
        <Route
          path="data/assets"
          element={
            <ChartPage>
              <AssetsPage />
            </ChartPage>
          }
        />
        <Route
          path="data/assets/:provider/:key"
          element={
            <ChartPage>
              <AssetPage />
            </ChartPage>
          }
        />
        <Route path="data/providers" element={<ProvidersPage />} />
        <Route path="ta" element={<Navigate to="/ta/indicators" replace />} />
        <Route
          path="ta/indicators"
          element={
            <ChartPage>
              <IndicatorsPage />
            </ChartPage>
          }
        />
        <Route
          path="ta/indicators/new"
          element={
            <ChartPage>
              <ScriptEditorPage key="new" />
            </ChartPage>
          }
        />
        <Route
          path="ta/indicators/:id"
          element={
            <ChartPage>
              <ScriptEditorPage />
            </ChartPage>
          }
        />
        <Route
          path="ta/charts"
          element={
            <ChartPage>
              <ChartsPage />
            </ChartPage>
          }
        />
        <Route
          path="ta/signals"
          element={
            <ChartPage>
              <SignalRulesPage />
            </ChartPage>
          }
        />
        <Route
          path="ta/monitor"
          element={
            <ChartPage>
              <MonitorPage />
            </ChartPage>
          }
        />
        <Route path="data/providers/:providerId" element={<ProviderPage />} />
        <Route path="prices" element={<PricesRedirect />} />
        <Route path="backtests" element={<BacktestsPage />} />
        <Route path="backtests/new" element={<NewBacktestPage />} />
        <Route
          path="backtests/compare"
          element={
            <ChartPage>
              <BacktestComparePage />
            </ChartPage>
          }
        />
        <Route
          path="backtests/experiments"
          element={
            <ChartPage>
              <ExperimentsPage />
            </ChartPage>
          }
        />
        <Route
          path="backtests/:runId"
          element={
            <ChartPage>
              <BacktestRunPage />
            </ChartPage>
          }
        />
        <Route
          path="backtests/:runId/days/:day"
          element={
            <ChartPage>
              <BacktestDayPage />
            </ChartPage>
          }
        />
        <Route path="timing" element={<TimingPage />} />
        <Route path="signals" element={<SignalsPage />} />
        <Route path="watchlist" element={<WatchlistPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="config" element={<ConfigPage />}>
          <Route index element={<ConfigOverview />} />
          <Route path="json" element={<ConfigJsonView />} />
          <Route path="file" element={<ConfigFileView />} />
          <Route path="versions" element={<ConfigVersionsView />} />
          <Route path=":group" element={<ConfigGroupPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Route>,
  ),
)

export default function App() {
  const [queryClient] = useState(createQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      {/* Light or dark, for every page, the login included. */}
      <ThemeProvider>
        {/* Animations follow the system's "reduce motion" setting. */}
        <MotionConfig reducedMotion="user">
          <RouterProvider router={router} />
        </MotionConfig>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
