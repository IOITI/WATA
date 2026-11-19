import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from 'ioiti-ui'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/features/auth/auth-context'
import type { DiskCleanups, DiskKind, DiskOverview, DiskProjection, DiskRetentions, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { DiskPage } from './DiskPage'

const GB = 1_000_000_000
const MB = 1_000_000

const SESSION = { authenticated: true, version: '0.89.0', timezone: 'Europe/Paris', actions_enabled: true } as Session

const RETENTIONS: DiskRetentions = {
  saxo_retention_months: 0, capital_retention_months: 0, capital_day_files_days: 0, dukascopy_retain_days: 0,
  signal_cache_mb: 1024, research_keep_days: 30, ta_monitoring_days: 7,
}

const kind = (id: string, label: string, family: string, bytes: number, extra: Partial<DiskKind> = {}): DiskKind => ({
  id, label, family, model: 'trend', knob: null, link: null, note: `${label}: what it is`, bytes, share: 1, per_day: null,
  pace_from: null, week: null, week_since: null, retention: null, ...extra,
})

function overview(changes: Partial<DiskOverview> = {}): DiskOverview {
  return {
    judged: {
      level: 'ok', reason: '79.0 GB free of 96.0 GB (82.3 %)', at: '2026-11-17T07:07:00Z', total: 96 * GB, available: 79 * GB,
      free_percent: 82.3, available_per_day: -40 * MB, days_to_critical: 1855, age_hours: 0.5,
      thresholds: { warning_free_percent: 15, critical_free_percent: 5, warning_days_left: 14 },
    },
    retentions: {
      values: RETENTIONS,
      bounds: {
        saxo_retention_months: [0, 120], capital_retention_months: [0, 120], capital_day_files_days: [0, 3650],
        dukascopy_retain_days: [0, 3650], signal_cache_mb: [64, 8192], research_keep_days: [1, 365], ta_monitoring_days: [1, 30],
      },
    },
    horizons: [30, 90, 180, 365],
    max_horizon_days: 730,
    sample: {
      at: '2026-11-17T07:07:00Z', took_seconds: 4.2, errors: { docker: 'docker system df failed' },
      filesystems: [{ mount: '/', paths: ['/app/wata'], total: 96 * GB, used: 17 * GB, free: 79 * GB, available: 79 * GB,
        inodes_total: 1, inodes_free: 1, wata: true }],
      trend: { available_per_day: -40 * MB, over_days: 6, samples: 145 }, samples: 145, first_at: '2026-11-11T07:07:00Z',
    },
    kinds: [
      kind('saxo_bars', "Saxo's recorded prices", 'Prices', 749 * MB, { model: 'months', knob: 'saxo_retention_months', retention: 0,
        per_day: 35 * MB, pace_from: 'samples', week: 245 * MB, link: '/data/providers/saxo' }),
      kind('dukascopy', "Dukascopy's history", 'Prices', 424 * MB, { model: 'days', knob: 'dukascopy_retain_days', retention: 0 }),
      kind('docker_build_cache', 'Docker build cache', 'Releases and Docker', 4.4 * GB),
    ],
    families: [{ name: 'Prices', bytes: 1173 * MB }, { name: 'Releases and Docker', bytes: 4.4 * GB }],
    series: [
      { at: '2026-11-16T07:07:00Z', available: 79.04 * GB, total: 96 * GB, families: { Prices: 1.13 * GB } },
      { at: '2026-11-17T07:07:00Z', available: 79 * GB, total: 96 * GB, families: { Prices: 1.17 * GB } },
    ],
    details: { docker: { images: 4 * GB, build_cache: 4.4 * GB, reclaimable: { build_cache: 902 * MB } }, tables: [], logs: [], releases: [], dumps: [] },
    ...changes,
  }
}

function projected(asked: Partial<DiskRetentions>): DiskProjection {
  const free = Object.keys(asked).length ? 75 * GB : 66 * GB
  return {
    at: '2026-11-17T07:07:00Z', today: '2026-11-17', total: 96 * GB, available: 79 * GB,
    retentions: { ...RETENTIONS, ...asked }, current: RETENTIONS, asked,
    thresholds: { warning_free_percent: 15, critical_free_percent: 5 },
    kinds: [{ id: 'saxo_bars', now: 749 * MB, per_day: 35 * MB, pace_from: 'samples', retention: asked.saxo_retention_months ?? 0,
      steady: asked.saxo_retention_months ? [2.1 * GB, 3.2 * GB] : null }],
    horizons: [30, 90, 180, 365].map((days) => ({ days, date: '', available: free, free_percent: null,
      kinds: { saxo_bars: Object.keys(asked).length ? 3 * GB : 13.5 * GB } })),
    series: Array.from({ length: 366 }, (_, days) => ({
      days, date: new Date(Date.UTC(2026, 10, 17 + days)).toISOString().slice(0, 10), available: 79 * GB - days * 30 * MB,
      free_percent: 0, families: {},
    })),
    days_to_warning: Object.keys(asked).length ? null : 1500, days_to_critical: null, look_days: 1825,
  }
}

let state: DiskOverview
let asked: unknown[] = []
let cleaned: string[] = []
let cleanups: DiskCleanups

const offers = (waiting = false): DiskCleanups => ({
  actions_enabled: true,
  items: [
    { id: 'server', bytes: 929 * MB, up_to: true, available: !waiting, why_not: null, build_cache: 900 * MB,
      releases: [{ version: '0.85.0', bytes: 15 * MB }, { version: '0.61.1', bytes: 14 * MB }],
      kept: [{ version: '0.89.0', why: ['newest', 'pinned'], containers: ['admin1'] },
        { version: '0.59.1', why: ['created'], containers: ['traefik', 'postgres'] }],
      waiting: waiting ? { id: 'a'.repeat(32), state: 'asked', at: '2026-11-17T07:20:00Z' } : null,
      last: { id: 'c1', by: 'watadeploy prune', removed: ['0.82.0'], build_cache_bytes: 300 * MB, images_bytes: 0,
        finished_at: '2026-11-16T22:00:00Z' } },
    { id: 'signal_cache', bytes: 1_100 * MB, files: 120, limit: 1024 * 1024 * 1024, available: false,
      why_not: 'A backtest is under way: its Pine answers are read as it goes' },
  ],
})

beforeEach(() => {
  state = overview()
  asked = []
  cleaned = []
  cleanups = offers()
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      if (path === '/api/system/disk') return json(state)
      if (path === '/api/system/disk/cleanups') return json(cleanups)
      if (path.startsWith('/api/system/disk/cleanups/')) {
        cleaned.push(path.split('/').pop() as string)
        cleanups = offers(true)
        return json({ action: 'server', asked: { id: 'a'.repeat(32), action: 'cleanup' } })
      }
      if (path === '/api/system/disk/projection') {
        const body = JSON.parse(String(init.body)) as { retentions: Partial<DiskRetentions> }
        asked.push(body)
        return json(projected(body.retentions))
      }
      return json({ detail: `Not served: ${path}` }, 404)
    }),
  )
})
afterEach(() => vi.unstubAllGlobals())

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/system/disk', element: <WithTrail><DiskPage /></WithTrail> }],
    { initialEntries: ['/system/disk'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
          <RouterProvider router={router} />
        </AuthContext.Provider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

describe('the Disk page', () => {
  it('is under Operations, and says the disk, each kind and what was not measured', async () => {
    renderPage()

    const trail = (await screen.findAllByRole('navigation', { name: 'Breadcrumb' }))[0]
    expect(within(trail).getByText('Operations')).toBeInTheDocument()
    expect(within(trail).getByText('Disk')).toBeInTheDocument()
    expect((await screen.findByText('Free on /')).parentElement?.parentElement).toHaveTextContent('79.0 GB')
    expect(screen.getByRole('meter', { name: 'Used of the disk' })).toHaveAttribute('aria-valuenow', '18')
    expect(screen.getByText('Fine')).toBeInTheDocument()
    // The kinds' table comes first.
    const table = screen.getAllByRole('table')[0]
    const saxo = within(table).getByRole('row', { name: /Saxo's recorded prices/ })
    expect(within(saxo).getByText('749 MB')).toBeInTheDocument()
    expect(within(saxo).getByText('+35.0 MB')).toBeInTheDocument()
    expect(within(saxo).getByText('+245 MB')).toBeInTheDocument()
    expect(within(saxo).getByRole('link', { name: 'Every month' })).toHaveAttribute('href', '/data/providers/saxo')
    expect(within(table).getByRole('row', { name: /Docker build cache/ })).toHaveTextContent('Never removed')
    expect(screen.getByText(/Not measured this time: docker \(docker system df failed\)/)).toBeInTheDocument()
    expect(screen.getByText(/Docker could give back 902 MB of its build cache/)).toBeInTheDocument()
  })

  it('projects the retentions tried beside those set now, and asks only what changed', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText(/In 1 year: 66.0 GB free as set now\./)).toBeInTheDocument()
    expect(asked).toEqual([{ retentions: {} }])

    const field = screen.getByRole('spinbutton', { name: "Saxo's prices" })
    await user.clear(field)
    await user.type(field, '2')
    await user.click(screen.getByRole('button', { name: 'Project' }))

    await waitFor(() => expect(asked).toContainEqual({ retentions: { saxo_retention_months: 2 } }))
    expect(await screen.findByText(/66.0 GB free as set now, 75.0 GB with the retentions tried\./)).toBeInTheDocument()
    expect(screen.getByText(/Settles at 2.1 GB to 3.2 GB/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Back to the retentions set' }))
    expect(await screen.findByText(/In 1 year: 66.0 GB free as set now\./)).toBeInTheDocument()
  })

  it('offers the cleanups with what each would give back, and asks the server for its own after a slide', async () => {
    const user = userEvent.setup()
    renderPage()

    const panel = (await screen.findByText('Give space back')).closest('section') ?? document.body
    expect(await within(panel as HTMLElement).findByText(/Up to 929 MB/)).toBeInTheDocument()
    expect(within(panel as HTMLElement).getByText(/release folders 0.85.0, 0.61.1 and their images/)).toBeInTheDocument()
    expect(within(panel as HTMLElement).getByText(/Last cleanup .*by watadeploy prune: releases 0.82.0 removed, 300 MB given back/)).toBeInTheDocument()
    expect(within(panel as HTMLElement).getByText(
      'Kept: 0.89.0 (in use); 0.59.1 (holds the compose files traefik and postgres were made from).')).toBeInTheDocument()
    // Not while a backtest reads it.
    expect(within(panel as HTMLElement).getByRole('button', { name: 'Empty it' })).toBeDisabled()

    await user.click(within(panel as HTMLElement).getByRole('button', { name: 'Clean up' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/This acts on the server/)).toBeInTheDocument()
    within(dialog).getByRole('slider', { name: 'Slide to clean up' }).focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(cleaned).toEqual(['server']))
    expect(await screen.findByText(/Asked: watadeploy cleans up within a minute/)).toBeInTheDocument()
    expect(await screen.findByText(/waiting for watadeploy/)).toBeInTheDocument()
  })

  it('says a disk that is low, and how to get a first sample', async () => {
    state = overview({ judged: { ...overview().judged, level: 'warning', reason: '12.0 GB free of 96.0 GB (12.5 %): under 15 %' } })
    renderPage()
    expect(await screen.findByText('12.0 GB free of 96.0 GB (12.5 %): under 15 %.')).toBeInTheDocument()
    expect(screen.getAllByText('Low').length).toBeGreaterThan(0)
  })

  it('without a sample, says how one is taken', async () => {
    state = overview({ judged: { level: 'none', reason: 'No sample of the disk yet' }, sample: null, kinds: [], families: [], series: [] })
    renderPage()
    expect(await screen.findByText(/watadisk now/)).toBeInTheDocument()
    expect(asked).toEqual([])
  })
})
