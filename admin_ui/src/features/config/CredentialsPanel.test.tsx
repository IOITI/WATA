import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/features/auth/auth-context'
import type { Credential, Credentials, Session } from '@/lib/types'
import { CATALOG, configFile } from '@/test/configCatalog'
import { WithTrail } from '@/test/WithTrail'

import { ConfigPage } from './ConfigPage'
import { ConfigGroupPage } from './ConfigViews'
import { checksSaid, fromSaxoAppObject, rotationSaid, saxoAppEnvironment, whereSaid } from './credentials'

const SESSION: Session = {
  authenticated: true,
  version: '0.43.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const NAMES = { telegram: 'Telegram', trader: 'Trader', admin: 'Admin', web_server: 'Web server' }

function credential(name: string, overrides: Partial<Credential> = {}): Credential {
  return {
    name,
    label: name,
    readers: ['trader'],
    parts: [{ key: 'token', label: 'Token', secret: true, expected: 'letters' }],
    generated: false,
    old_place: 'config.json (telegram.bot_token, telegram.chat_id)',
    where: 'database',
    public: {},
    fingerprint: 'fghi',
    set_at: '2026-10-24T08:00:00+00:00',
    set_by: '10.77.0.2',
    checks: [],
    grace_seconds: null,
    ...overrides,
  }
}

function credentials(): Credentials {
  return {
    keys: null,
    items: [
      credential('telegram.bot', {
        label: 'Telegram bot',
        readers: ['telegram', 'trader', 'admin'],
        parts: [
          { key: 'token', label: 'Bot token', secret: true, expected: 'as BotFather gives it' },
          { key: 'chat_id', label: 'Chat id', secret: false, expected: 'a number' },
        ],
        where: 'old place',
        public: {},
        fingerprint: null,
        set_at: null,
        set_by: null,
      }),
      credential('webhook.token', {
        label: 'TradingView webhook token',
        readers: ['web_server', 'admin'],
        generated: true,
        grace_seconds: 3600,
        checks: [],
      }),
      credential('saxo.live.app', {
        label: 'Saxo live app',
        readers: ['trader', 'position_monitor', 'watchlist_manager', 'price_recorder'],
        parts: [
          { key: 'app_key', label: 'App Key', secret: false, expected: 'letters and digits' },
          { key: 'app_secret', label: 'App Secret', secret: true, expected: 'letters and digits' },
        ],
        where: 'old place',
        old_place: 'config.json (authentication.saxo.app_config_object)',
        public: {},
        fingerprint: null,
        set_at: null,
        set_by: null,
      }),
    ],
  }
}

const APP_OBJECT = {
  AppName: 'WATA',
  AppKey: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6',
  AuthorizationEndpoint: 'https://live.logonvalidation.net/authorize',
  TokenEndpoint: 'https://live.logonvalidation.net/token',
  GrantType: 'Code',
  OpenApiBaseUrl: 'https://gateway.saxobank.com/openapi/',
  RedirectUrls: ['https://admin.example.com/api/saxo/callback'],
  AppSecret: 's3cr3ts3cr3ts3cr3ts3cr3ts3cr3t00',
}

let state: Credentials
let sent: { method: string; path: string; body: unknown }[] = []

function serve() {
  sent = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const catalog = {
    ...CATALOG,
    services: [...CATALOG.services, { id: 'telegram', name: 'Telegram', container: 'telegram1', profile: null }],
    groups: [...CATALOG.groups, { id: 'credentials', title: 'Credentials', summary: 'Sealed.' }],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/config/catalog') return json(catalog)
      if (path === '/api/config' && method === 'GET') {
        return json({ config: configFile(), revision: 'r1', editable_paths: [], protected_paths: [], unknown: [], editable: true,
          restart_required: false, modified_at: '2026-10-24T08:00:00Z' })
      }
      if (path === '/api/credentials') return json(state)
      if (path === '/api/credentials/telegram.bot' && method === 'PUT') {
        return json({ name: 'telegram.bot', fingerprint: 'fghi', services: ['telegram', 'trader', 'admin'] })
      }
      if (path === '/api/credentials/webhook.token/rotate') {
        return json({ name: 'webhook.token', fingerprint: 'abcd', services: ['web_server', 'admin'], previous_accepted_seconds: 3600 })
      }
      if (path === '/api/credentials/webhook.token/show') {
        return json({ token: 'tok', url: 'https://api.example.com/webhook?token=tok', path: '/webhook?token=tok' })
      }
      if (path === '/api/credentials/telegram.bot/test') return json({ ok: true, detail: 'Sent' })
      if (path === '/api/credentials/saxo.live.app' && method === 'PUT') {
        return json({ name: 'saxo.live.app', fingerprint: 'c5d6', services: ['trader', 'position_monitor', 'watchlist_manager', 'price_recorder'] })
      }
      if (path === '/api/system/saxo') {
        return json({ environment: 'live', redirect_url: 'https://admin.example.com/api/saxo/callback', callback: true })
      }
      if (path === '/api/system/services') {
        return json({ available: false, error: 'No control service runs beside this admin', starts_known: false,
          actions_enabled: true, services: [], plan: null })
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderCredentials() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/config', element: <WithTrail><ConfigPage /></WithTrail>, children: [{ path: ':group', element: <ConfigGroupPage /> }] }],
    { initialEntries: ['/config/credentials'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <RouterProvider router={router} />
          </AuthContext.Provider>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state = credentials()
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('what is said of a credential', () => {
  it('says where the services read it now', () => {
    const [telegram, webhook] = credentials().items

    expect(whereSaid(webhook, 'Europe/Paris')).toMatch(/^Set .* from 10\.77\.0\.2, ending …fghi\.$/)
    expect(whereSaid(telegram, 'Europe/Paris')).toBe(
      'Read at its old place until it is set here: config.json (telegram.bot_token, telegram.chat_id).',
    )
    expect(whereSaid(credential('heartbeat.url', { where: 'unknown', old_place: '.env (WATA_HEARTBEAT_URL)' }), 'UTC')).toMatch(
      /maybe in \.env \(WATA_HEARTBEAT_URL\), which the admin cannot see/,
    )
  })

  it('says what the services that used it saw, newest first', () => {
    expect(
      checksSaid(
        [
          { role: 'telegram', checked_at: '2026-10-24T07:00:00+00:00', ok: true, detail: 'Messages taken' },
          { role: 'admin', checked_at: '2026-10-24T08:00:00+00:00', ok: false, detail: 'Unauthorized' },
        ],
        NAMES,
        'UTC',
      ),
    ).toEqual(['Admin, 24 Oct, 08:00:00: failed (Unauthorized)', 'Telegram, 24 Oct, 07:00:00: worked (Messages taken)'])
  })

  it('says what a rotation does before it is asked, and that there is no token before a first one', () => {
    expect(rotationSaid(credentials().items[1])).toMatch(/accepted for 60 minutes: change the address of every TradingView alert/)
    expect(rotationSaid(credential('internal.token', { generated: true, grace_seconds: 900 }))).toMatch(/15 minutes: restart the web server/)
    expect(rotationSaid(credential('webhook.token', { generated: true, where: 'missing', grace_seconds: 3600 }))).toMatch(
      /^A token is made, with none before it/,
    )
  })
})

describe('a Saxo app', () => {
  it('is known by its name', () => {
    expect(saxoAppEnvironment('saxo.live.app')).toBe('live')
    expect(saxoAppEnvironment('saxo.simulation.app')).toBe('simulation')
    expect(saxoAppEnvironment('saxo.live.tokens')).toBeNull()
    expect(saxoAppEnvironment('telegram.bot')).toBeNull()
  })

  it('is read from the app object the portal copies, its addresses only checked', () => {
    expect(fromSaxoAppObject(JSON.stringify(APP_OBJECT), 'live', 'https://admin.example.com/api/saxo/callback')).toEqual({
      values: { app_key: APP_OBJECT.AppKey, app_secret: APP_OBJECT.AppSecret },
      warnings: [],
    })
  })

  it('says what does not fit the account it is set for', () => {
    const simulation = {
      ...APP_OBJECT,
      AuthorizationEndpoint: 'https://sim.logonvalidation.net/authorize',
      TokenEndpoint: 'https://sim.logonvalidation.net/token',
      GrantType: 'PKCE',
      RedirectUrls: ['http://localhost/app'],
    }
    const read = fromSaxoAppObject(JSON.stringify(simulation), 'live', 'https://admin.example.com/api/saxo/callback')

    expect('warnings' in read && read.warnings).toEqual([
      'Its AuthorizationEndpoint is at sim.logonvalidation.net: the simulation account’s app, not the live account’s.',
      'Its TokenEndpoint is at sim.logonvalidation.net: the simulation account’s app, not the live account’s.',
      'Its grant type is PKCE: WATA logs in with Code.',
      'Its redirect addresses leave out this server’s, https://admin.example.com/api/saxo/callback: add it in the portal, or Saxo refuses the login.',
    ])
  })

  it('refuses what is no app object, or one without its secret', () => {
    expect(fromSaxoAppObject('a1b2c3', 'live')).toEqual({ error: expect.stringMatching(/^Not an app object/) })
    expect(fromSaxoAppObject('[1, 2]', 'live')).toEqual({ error: expect.stringMatching(/^Not an app object/) })
    const { AppSecret: _secret, ...withoutSecret } = APP_OBJECT
    expect(fromSaxoAppObject(JSON.stringify(withoutSecret), 'live')).toEqual({ error: expect.stringMatching(/no AppKey and AppSecret/) })
  })
})

describe('the Credentials group', () => {
  it('shows each credential, who reads it, and never a value', async () => {
    renderCredentials()

    const telegram = (await screen.findByRole('heading', { name: 'Telegram bot' })).closest('section')!
    expect(within(telegram).getByText('In its old place')).toBeInTheDocument()
    expect(within(telegram).getByText('Read by')).toBeInTheDocument()
    expect(within(telegram).getByText('Trader')).toBeInTheDocument()
    expect(screen.getByText('Kept elsewhere')).toBeInTheDocument()
  })

  it('seals a bot entered and offers to restart the services that read it', async () => {
    const user = userEvent.setup()
    renderCredentials()

    const telegram = (await screen.findByRole('heading', { name: 'Telegram bot' })).closest('section')!
    await user.click(within(telegram).getByRole('button', { name: 'Set' }))
    const dialog = await screen.findByRole('dialog', { name: 'Set the Telegram bot' })
    expect(dialog).toHaveTextContent('@BotFather')
    await user.type(within(dialog).getByLabelText('Bot token'), '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    await user.type(within(dialog).getByLabelText('Chat id'), '4242')
    expect(within(dialog).getByLabelText('Bot token')).toHaveAttribute('type', 'password')
    await user.click(within(dialog).getByRole('button', { name: 'Seal and save' }))

    await waitFor(() => expect(sent.some((call) => call.method === 'PUT')).toBe(true))
    expect(sent.find((call) => call.method === 'PUT')!.body).toEqual({
      parts: { token: '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi', chat_id: '4242' },
    })
    expect(await within(telegram).findByText(/It applies when these start again: Telegram, Trader, Admin/)).toBeInTheDocument()
  })

  it('rotates the webhook token after saying how long the one before is accepted, and shows its address', async () => {
    const user = userEvent.setup()
    renderCredentials()

    const webhook = (await screen.findByRole('heading', { name: 'TradingView webhook token' })).closest('section')!
    await user.click(within(webhook).getByRole('button', { name: 'Rotate' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('accepted for 60 minutes')
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(sent.some((call) => call.path === '/api/credentials/webhook.token/rotate')).toBe(true))

    await user.click(within(webhook).getByRole('button', { name: 'Show the address' }))
    expect(await within(webhook).findByText('https://api.example.com/webhook?token=tok')).toBeInTheDocument()
  })

  it('tries the bot as stored', async () => {
    const user = userEvent.setup()
    renderCredentials()

    const telegram = (await screen.findByRole('heading', { name: 'Telegram bot' })).closest('section')!
    await user.click(within(telegram).getByRole('button', { name: 'Send a test message' }))

    expect(await within(telegram).findByText(/Telegram took the test message/)).toBeInTheDocument()
  })

  it('fills a Saxo app from its app object, seals its key and secret, and says whether it is in use', async () => {
    const user = userEvent.setup()
    renderCredentials()

    const saxo = (await screen.findByRole('heading', { name: 'Saxo live app' })).closest('section')!
    expect(await within(saxo).findByText(/In use: the services trade on the live account/)).toBeInTheDocument()
    await user.click(within(saxo).getByRole('button', { name: 'Set' }))
    const dialog = await screen.findByRole('dialog', { name: 'Set the Saxo live app' })
    expect(dialog).toHaveTextContent('developer.saxo')
    await user.click(within(dialog).getByLabelText('App object (optional)'))
    await user.paste(JSON.stringify(APP_OBJECT))

    expect(within(dialog).getByLabelText('App Key')).toHaveValue(APP_OBJECT.AppKey)
    expect(within(dialog).getByLabelText('App Secret')).toHaveValue(APP_OBJECT.AppSecret)
    expect(within(dialog).getByText(/Its addresses are not kept/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Seal and save' }))

    await waitFor(() => expect(sent.some((call) => call.path === '/api/credentials/saxo.live.app')).toBe(true))
    expect(sent.find((call) => call.path === '/api/credentials/saxo.live.app')!.body).toEqual({
      parts: { app_key: APP_OBJECT.AppKey, app_secret: APP_OBJECT.AppSecret },
    })
  })

  it('sets nothing without the keys, and says why', async () => {
    state = { ...credentials(), keys: 'No key here: the install script makes them' }
    renderCredentials()

    expect(await screen.findByText(/No key here: the install script makes them/)).toBeInTheDocument()
    const telegram = screen.getByRole('heading', { name: 'Telegram bot' }).closest('section')!
    expect(within(telegram).getByRole('button', { name: 'Set' })).toBeDisabled()
  })
})
