import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '@/components/ToastProvider'
import { formatDateTime } from '@/lib/format'
import type { ConfigBackup, ConfigDocument } from '@/lib/types'

import { BackupsPanel } from './BackupsPanel'

const ZONE = 'Europe/Paris'
const NEWEST = 'config.json.bak-20261022-221800'

const BACKUPS: ConfigBackup[] = [
  { name: NEWEST, created_at: '2026-10-22T20:18:00Z', size: 4812 },
  { name: 'config.json.bak-20261021-090000', created_at: '2026-10-21T07:00:00Z', size: 4700 },
]

const CONFIG_DOC: ConfigDocument = {
  config: { trade: { config: { max_positions: 2 }, rules: { stop: 1 } }, logging: { level: 'INFO' } },
  revision: 'r1',
  editable_paths: ['trade.rules', 'trade.config', 'reporting', 'logging.level'],
  protected_paths: [],
  unknown: [],
  editable: true,
  restart_required: false,
  modified_at: '2026-10-23T12:05:00Z',
}

interface Sent {
  method: string
  path: string
  body: unknown
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

/** The server's backups: the newest held one parameter at 3 where the file in use has 2. */
function serve(backups: ConfigBackup[] = BACKUPS) {
  const sent: Sent[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (method === 'GET' && path === '/api/config/backups') return json({ items: backups })
      if (method === 'GET' && path === `/api/config/backups/${NEWEST}`) {
        return json({ name: NEWEST, config: { ...CONFIG_DOC.config, trade: { config: { max_positions: 3 }, rules: { stop: 1 } } } })
      }
      if (method === 'POST' && path === `/api/config/backups/${NEWEST}/restore`) {
        return json({ revision: 'r2', backup: 'config.json.bak-20261023-140500', restored: NEWEST, restart_required: true })
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }),
  )
  return sent
}

function renderPanel(configDoc: ConfigDocument = CONFIG_DOC) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BackupsPanel configDoc={configDoc} timeZone={ZONE} />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

async function versions() {
  return within(await screen.findByRole('list', { name: 'Versions' })).getAllByRole('listitem')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('previous versions of the configuration', () => {
  it('lists the file in use, then each version it replaced, newest first', async () => {
    serve()
    renderPanel()

    const [inUse, newest, older] = await versions()
    expect(inUse).toHaveTextContent(`Written ${formatDateTime(CONFIG_DOC.modified_at, ZONE)}`)
    expect(within(inUse).getByText('In use')).toBeInTheDocument()
    // The file in use is neither opened nor restored.
    expect(within(inUse).queryByRole('button')).not.toBeInTheDocument()
    expect(newest).toHaveTextContent(`Replaced ${formatDateTime(BACKUPS[0].created_at, ZONE)}`)
    expect(newest).toHaveTextContent('4,812 bytes')
    expect(newest).toHaveTextContent(NEWEST)
    expect(older).toHaveTextContent(`Replaced ${formatDateTime(BACKUPS[1].created_at, ZONE)}`)
    // Nothing is offered for restoring before it is opened and read.
    expect(screen.queryByRole('button', { name: /^Restore/ })).not.toBeInTheDocument()
  })

  it('opens a version on what restoring it would change, and restores it only once the slide is done', async () => {
    const sent = serve()
    const user = userEvent.setup()
    renderPanel()
    const replaced = formatDateTime(BACKUPS[0].created_at, ZONE)

    const newest = (await versions())[1]
    const toggle = within(newest).getByRole('button')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(
      await within(newest).findByText('Restoring this version would make 1 change (current value → restored value):'),
    ).toBeInTheDocument()
    expect(within(newest).getByText('trade.config.max_positions')).toBeInTheDocument()

    await user.click(within(newest).getByRole('button', { name: `Restore: Replaced ${replaced}` }))
    const dialog = await screen.findByRole('dialog', { name: 'Restore this version' })
    expect(dialog).toHaveTextContent(`Restoring the version replaced ${replaced} makes 1 change.`)
    expect(sent.filter((request) => request.method === 'POST')).toEqual([])

    const slider = within(dialog).getByRole('slider')
    slider.focus()
    fireEvent.keyDown(slider, { key: 'Enter' })

    await waitFor(() =>
      expect(sent.filter((request) => request.method === 'POST')).toEqual([
        { method: 'POST', path: `/api/config/backups/${NEWEST}/restore`, body: { revision: 'r1' } },
      ]),
    )
    expect(await screen.findByText('Trading parameters restored')).toBeInTheDocument()
    expect(screen.getByText('The version it replaced is kept as config.json.bak-20261023-140500.')).toBeInTheDocument()
  })

  it('shows what a version would change while editing is switched off, without offering to restore it', async () => {
    serve()
    const user = userEvent.setup()
    renderPanel({ ...CONFIG_DOC, editable: false })

    const newest = (await versions())[1]
    const toggle = within(newest).getByRole('button')
    await user.click(toggle)

    expect(await within(newest).findByText(/Editing is switched off in the configuration/)).toBeInTheDocument()
    expect(within(newest).getByText('trade.config.max_positions')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Restore/ })).not.toBeInTheDocument()

    // Opened again, it closes.
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(within(newest).queryByText('trade.config.max_positions')).not.toBeInTheDocument())
  })

  it('says so when no version is kept yet', async () => {
    serve([])
    renderPanel()

    expect(
      await screen.findByText('Nothing yet: the first version is kept the first time the configuration is saved from here.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Versions' })).not.toBeInTheDocument()
  })
})
