// What the Credentials group says of each credential kept sealed in PostgreSQL (src/vault/,
// src/admin/vault.py): where it is, what its services last saw, and how each is obtained. Pure.

import { formatDateTime } from 'ioiti-ui'
import type { BadgeTone } from 'ioiti-ui'

import type { Brand } from '@/components/BrandMark'
import type { Credential, CredentialCheck } from '@/lib/types'

export const WHERE_LABEL: Record<Credential['where'], { label: string; tone: BadgeTone }> = {
  database: { label: 'Sealed in PostgreSQL', tone: 'success' },
  'old place': { label: 'In its old place', tone: 'warning' },
  missing: { label: 'Not set', tone: 'danger' },
  unknown: { label: 'Not in PostgreSQL', tone: 'warning' },
}

/** A sentence on where the services read it now. */
export function whereSaid(credential: Credential, timeZone: string): string {
  switch (credential.where) {
    case 'database': {
      const by = credential.set_by ? ` from ${credential.set_by}` : ''
      const end = credential.fingerprint ? `, ending …${credential.fingerprint}` : ''
      return `Set ${formatDateTime(credential.set_at, timeZone)}${by}${end}.`
    }
    case 'old place':
      return `Read at its old place until it is set here: ${credential.old_place}.`
    case 'missing':
      return 'Nowhere: the services that need it do without, and say so.'
    case 'unknown':
      return `Not here: maybe in ${credential.old_place}, which the admin cannot see. Set it here to be sure.`
  }
}

/** What each service that used it last saw, newest first. */
export function checksSaid(checks: CredentialCheck[], names: Record<string, string>, timeZone: string): string[] {
  return [...checks]
    .sort((a, b) => (b.checked_at ?? '').localeCompare(a.checked_at ?? ''))
    .map((check) => {
      const who = names[check.role] ?? check.role
      const when = check.checked_at ? formatDateTime(check.checked_at, timeZone) : 'once'
      return `${who}, ${when}: ${check.ok ? 'worked' : 'failed'}${check.detail ? ` (${check.detail})` : ''}`
    })
}

/** A credential's name inside a sentence: proper names keep their capitals. */
export function inSentence(credential: Pick<Credential, 'name' | 'label'>): string {
  const common: Record<string, string> = { 'internal.token': 'internal token', 'heartbeat.url': 'outside watcher' }
  return common[credential.name] ?? credential.label
}

/** What rotating a token does, before it is asked. */
export function rotationSaid(credential: Credential): string {
  const minutes = credential.grace_seconds ? Math.round(credential.grace_seconds / 60) : 0
  if (credential.where === 'missing') {
    return credential.name === 'webhook.token'
      ? 'A token is made, with none before it: show the address once it is made, put it in each TradingView alert, then restart the web server.'
      : 'A token is made, with none before it: restart the web server, then the services that send it.'
  }
  if (credential.name === 'webhook.token') {
    return `A new token is made. The one before is still accepted for ${minutes} minutes: change the address of every TradingView alert meanwhile, then restart the web server.`
  }
  return `A new token is made. The one before is still accepted for ${minutes} minutes: restart the web server, then the services that send it, meanwhile.`
}

/** The mark of the service a credential opens, beside its name. */
export const CREDENTIAL_BRANDS: Record<string, Brand> = {
  'telegram.bot': 'telegram',
  'webhook.token': 'tradingview',
  'capital.login': 'capital',
  'saxo.live.app': 'saxo',
  'saxo.simulation.app': 'saxo',
}

/** How each credential is obtained, step by step. */
export const HELPERS: Record<string, string[]> = {
  'telegram.bot': [
    'In Telegram, write to @BotFather: /newbot, a name, then a username ending in "bot". It answers with the bot’s token.',
    'Start a chat with the bot (or add it to a group) and send it a message.',
    'Its chat id: open api.telegram.org/bot<token>/getUpdates in a browser, chat.id of the message (negative for a group).',
    'Set both here, try the bot, then restart the services that send through it.',
  ],
  'capital.login': [
    'On Capital.com: Settings, API integrations. Two-factor authentication must be on first.',
    'Generate a key: a name, and a password of its own for the key (not the account’s).',
    'The key is shown once: set it here with the account’s email and the key’s password.',
    'Demo or live is the feed’s own setting (Capital.com feed group). Then restart the feed.',
  ],
  'heartbeat.url': [
    'An outside watcher (healthchecks.io, Better Stack...) gives a ping address and alerts when the pings stop.',
    'Give it a period of two minutes or more: the position monitor pings every minute while it hears from Saxo.',
    'Set the address here, then restart the position monitor.',
  ],
  'webhook.token': [
    'TradingView’s alerts send their signals to the web server’s address with this token: https://<API domain>/webhook?token=...',
    'Show the address to copy it into an alert. Rotate the token if the address was seen by someone else.',
  ],
  'internal.token': [
    'What WATA’s services send the web server: the trader’s polling, the scheduler’s close, the TA engine, the admin.',
    'Rotate it if it was seen; nobody types it.',
  ],
  'saxo.live.app': saxoAppSteps('live'),
  'saxo.simulation.app': saxoAppSteps('simulation'),
}

function saxoAppSteps(environment: SaxoEnvironment): string[] {
  return [
    `In Saxo’s developer portal (developer.saxo), Application management: the app of the ${environment} account, its grant type Code.`,
    'Among its redirect addresses, this server’s: the one the System page shows under Saxo connection (SAXO_REDIRECT_URL, the admin’s /api/saxo/callback).',
    'Copy its app object and paste it below, or type its App Key and one of its App Secrets. Saxo’s addresses are not asked: WATA has its own for each environment.',
    environment === 'live'
      ? 'Restart the four services that log in to Saxo. Another app needs a new login (System page); the same app with another secret does not.'
      : 'The services trade on the account saxo_auth.env names, set on the server: this app waits for the day it names simulation.',
  ]
}

export type SaxoEnvironment = 'live' | 'simulation'

/** The environment of a Saxo app's credential (saxo.live.app), or null for any other. */
export function saxoAppEnvironment(name: string): SaxoEnvironment | null {
  const found = /^saxo\.(live|simulation)\.app$/.exec(name)
  return found ? (found[1] as SaxoEnvironment) : null
}

const SAXO_LOGIN_HOSTS: Record<SaxoEnvironment, string> = {
  live: 'live.logonvalidation.net',
  simulation: 'sim.logonvalidation.net',
}

function hostOf(value: unknown): string | null {
  try {
    return typeof value === 'string' ? new URL(value).host : null
  } catch {
    return null
  }
}

/**
 * Saxo's app object as its developer portal copies it: the key and secret it
 * gives, and what in it does not fit the account it is set for. Its addresses
 * are only read, never kept: WATA has Saxo's own (src/saxo_authen/endpoints.py).
 */
export function fromSaxoAppObject(
  text: string,
  environment: SaxoEnvironment,
  redirect: string | null = null,
): { values: Record<string, string>; warnings: string[] } | { error: string } {
  let app: unknown
  try {
    app = JSON.parse(text)
  } catch {
    return { error: 'Not an app object: paste it whole, as the portal copies it, braces included.' }
  }
  if (!app || typeof app !== 'object' || Array.isArray(app)) {
    return { error: 'Not an app object: paste it whole, as the portal copies it, braces included.' }
  }
  const object = app as Record<string, unknown>
  const key = typeof object.AppKey === 'string' ? object.AppKey.trim() : ''
  const secret = typeof object.AppSecret === 'string' ? object.AppSecret.trim() : ''
  if (!key || !secret) return { error: 'It has no AppKey and AppSecret: copy the app object with its secret.' }
  const warnings: string[] = []
  const other: SaxoEnvironment = environment === 'live' ? 'simulation' : 'live'
  for (const field of ['AuthorizationEndpoint', 'TokenEndpoint']) {
    const host = hostOf(object[field])
    if (host && host !== SAXO_LOGIN_HOSTS[environment]) {
      const whose = host === SAXO_LOGIN_HOSTS[other] ? `the ${other} account’s` : 'another'
      warnings.push(`Its ${field} is at ${host}: ${whose} app, not the ${environment} account’s.`)
    }
  }
  if (typeof object.GrantType === 'string' && object.GrantType !== 'Code') {
    warnings.push(`Its grant type is ${object.GrantType}: WATA logs in with Code.`)
  }
  if (redirect && Array.isArray(object.RedirectUrls) && !object.RedirectUrls.includes(redirect)) {
    warnings.push(`Its redirect addresses leave out this server’s, ${redirect}: add it in the portal, or Saxo refuses the login.`)
  }
  return { values: { app_key: key, app_secret: secret }, warnings }
}

/** The credentials that live elsewhere, and how each is changed. */
export const KEPT_ELSEWHERE: { what: string; where: string; brands?: Brand[] }[] = [
  {
    what: 'Saxo’s login',
    brands: ['saxo'],
    where: 'In PostgreSQL, sealed for the four services that renew it in turn: its state, and a new login, on the System page (Saxo connection)',
  },
  { what: 'Claude’s tokens', brands: ['claude'], where: 'Made and revoked on the System page, Claude panel (PostgreSQL keeps their sha256)' },
  { what: 'Your sign-in', where: 'Your password’s hash and one-time code key in var/lib/admin (wataadminpasswd, wataadmintotp on the server)' },
  {
    what: 'The database’s and RabbitMQ’s passwords',
    brands: ['postgresql', 'rabbitmq'],
    where: 'config.json and .env: PostgreSQL cannot keep what is needed to reach it',
  },
  {
    what: 'The keys, the control service’s token, the certificates',
    where: '/app/wata/keys, var/lib/service_control, Traefik: on the server alone. Keep a copy of /app/wata/keys',
  },
]
