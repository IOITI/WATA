import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'framer-motion'
import { lazy, Suspense, useState } from 'react'
import type { ReactNode } from 'react'
import { createBrowserRouter, createRoutesFromElements, Outlet, Route, RouterProvider } from 'react-router'

import { AppShell } from '@/components/AppShell'
import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { Skeleton } from '@/components/ui/skeleton'
import { AuditPage } from '@/features/audit/AuditPage'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { BacktestFormPage } from '@/features/backtests/BacktestFormPage'
import { BacktestsPage } from '@/features/backtests/BacktestsPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { ConfigPage } from '@/features/config/ConfigPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
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
const BacktestRunPage = lazy(() =>
  import('@/features/backtests/BacktestRunPage').then((module) => ({ default: module.BacktestRunPage })),
)
const ComparePage = lazy(() =>
  import('@/features/backtests/ComparePage').then((module) => ({ default: module.ComparePage })),
)
const BacktestDayPage = lazy(() =>
  import('@/features/backtests/BacktestDayPage').then((module) => ({ default: module.BacktestDayPage })),
)
// Its own charting library, for candles: loaded with the page as well.
const PricesPage = lazy(() =>
  import('@/features/prices/PricesPage').then((module) => ({ default: module.PricesPage })),
)

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
        <Route
          path="prices"
          element={
            <ChartPage>
              <PricesPage />
            </ChartPage>
          }
        />
        <Route path="backtests" element={<BacktestsPage />} />
        <Route path="backtests/new" element={<BacktestFormPage />} />
        <Route
          path="backtests/compare"
          element={
            <ChartPage>
              <ComparePage />
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
        <Route path="config" element={<ConfigPage />} />
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
