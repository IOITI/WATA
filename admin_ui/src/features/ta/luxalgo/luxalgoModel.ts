// LuxAlgo's Library on the Technical Analysis pages (src/admin/routers/luxalgo.py): where its pages are, what
// its write-ups become on them, and every sentence they say of a source, a sign-in and a script's origin.

import type {
  AgentVerdictTone,
  LuxAlgoCompat,
  LuxAlgoKnown,
  LuxAlgoKnownShort,
  LuxAlgoSource,
  LuxAlgoStatus,
  ScriptOrigin,
} from '@/lib/types'

export const LUXALGO_PATH = '/ta/luxalgo'
const SITE = 'https://www.luxalgo.com'
// LuxAlgo's image host, the one the admin's policy lets pictures come from (src/admin/app.py, LUXALGO_IMAGES).
const IMAGE_HOST = 'luxalgo-production.s3.amazonaws.com'
const IMAGE_PATH = /^\/library\/[A-Za-z0-9/_.-]{1,200}\.(?:png|jpe?g|webp|gif)$/i

export const indicatorPath = (slug: string) => `${LUXALGO_PATH}/indicators/${encodeURIComponent(slug)}`
export const conceptPath = (slug: string) => `${LUXALGO_PATH}/concepts/${encodeURIComponent(slug)}`
/** The Library's indicators of a concept, on the Library's page. */
export const conceptIndicatorsPath = (slug: string) => `${LUXALGO_PATH}?concept=${encodeURIComponent(slug)}`

const IMAGE = /!\[[^\]]*\]\([^)]*\)/g
// A write-up's own title, which the page's heading says already.
const TITLE = /^\s*# [^\n]*\n+/
const LINK = /\]\(([^)\s]+)\)/g
const LIBRARY = /^(?:https?:\/\/(?:www\.)?luxalgo\.com)?\/library\/(indicator|concept)\/([a-z0-9][a-z0-9-]*)\/?$/

/**
 * A write-up of LuxAlgo's as the pages show it: without its own title (the page's heading), a link into its
 * Library leading to the admin's page of that indicator or concept, another of its relative links to LuxAlgo's
 * site, and its images left out (the pages load nothing from another origin).
 */
export function shownMarkdown(markdown: string | null | undefined): string {
  return (markdown ?? '').replace(TITLE, '').replace(IMAGE, '').replace(LINK, (whole, target: string) => {
    const library = LIBRARY.exec(target)
    if (library) return `](${library[1] === 'indicator' ? indicatorPath(library[2]) : conceptPath(library[2])})`
    return target.startsWith('/') && !target.startsWith('//') ? `](${SITE}${target})` : whole
  })
}

/** Why a source cannot be taken as it is shown, or null when it is whole. */
export function sourceSaid(source: LuxAlgoSource): string | null {
  if (!source.available || !source.source) return "LuxAlgo does not publish this indicator's source."
  if (source.complete) return null
  const shown = source.shown_lines ? `${source.shown_lines} of its ${source.total_lines ?? 'many'} lines` : 'its first lines'
  return `LuxAlgo gives ${shown} to an admin not signed in: sign in to LuxAlgo on the Library's page to read it whole and take it.`
}

/** How much of its source LuxAlgo gives, in a few words: the page's glance at it. */
export function sourceLinesSaid(source: LuxAlgoSource | undefined): string {
  if (!source) return 'Being read'
  if (!source.available || !source.source) return 'None published'
  const lines = source.total_lines ?? source.source.split('\n').length
  if (source.complete) return `Whole, ${lines.toLocaleString('en-GB')} lines`
  return `${source.shown_lines ?? 'Its first'} of ${lines.toLocaleString('en-GB')} lines, signed out`
}

/** A source can be taken as one of the operator's scripts: whole, never a part. */
export function canTake(source: LuxAlgoSource | undefined): boolean {
  return !!source?.available && !!source.complete && !!source.source
}

/** What the sign-in panel says. */
export function signInSaid(status: LuxAlgoStatus): { tone: 'positive' | 'neutral' | 'warning'; text: string } {
  if (status.refused) return { tone: 'warning', text: status.refused }
  if (status.signed_in) {
    return {
      tone: 'positive',
      text: `Signed in as ${status.account ?? 'a LuxAlgo account'}: LuxAlgo gives whole sources. The admin renews the sign-in itself.`,
    }
  }
  return {
    tone: 'neutral',
    text: "Not signed in: LuxAlgo gives an indicator's first lines alone. A free LuxAlgo account is enough to read them whole.",
  }
}

/** What the page says once LuxAlgo sent the browser back (`?luxalgo=signed-in`, or refused with why). */
export function returnSaid(params: URLSearchParams): { tone: 'success' | 'warning'; text: string } | null {
  const outcome = params.get('luxalgo')
  if (outcome === 'signed-in') return { tone: 'success', text: 'Signed in to LuxAlgo.' }
  if (outcome !== 'refused') return null
  const why = params.get('why')
  if (why === 'off') return { tone: 'warning', text: "LuxAlgo's tools are switched off (admin.luxalgo_enabled): nothing was kept." }
  return { tone: 'warning', text: `The sign-in to LuxAlgo did not finish: ${why || 'no reason given'}.` }
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
}

// What the licences LuxAlgo's indicators come under ask, in a line.
const TERMS: [RegExp, string][] = [
  [/BY-NC-SA/i, 'non-commercial use only, credit to its author, and what is made from it shared under the same licence'],
  [/Mozilla Public License/i, 'its files changed shared under the same licence, its notices kept'],
]

/** What a licence asks, in a line, when WATA knows it. */
export function licenceTerms(licence: string | null | undefined): string | null {
  return TERMS.find(([pattern]) => pattern.test(licence ?? ''))?.[1] ?? null
}

/** Where a script came from, in a sentence. */
export function originSaid(origin: ScriptOrigin): string {
  const owner = origin.owner ? `, © ${origin.owner}` : ''
  if (origin.derived) return `Its header says it is LuxAlgo's${owner}: under ${origin.licence}.`
  return `From LuxAlgo's Library: ${origin.name ?? origin.slug ?? 'an indicator'}, under ${origin.licence}${owner}.`
}

// A licence that forbids commercial use (Creative Commons' NC), and what a header WATA could not read says
// (src/admin/script_origins.py: _NON_COMMERCIAL, UNREAD_LICENCE).
const NON_COMMERCIAL = /\bNC\b|NonCommercial|Non-Commercial/i
export const UNREAD_LICENCE = "see LuxAlgo's NOTICE"

/**
 * The licence the operator says they checked before the rule goes live, or null when its script asks for none: a
 * script of LuxAlgo's under a non-commercial licence, or one WATA could not read (needs_licence_check).
 */
export function licenceToCheck(origin: ScriptOrigin | null | undefined): string | null {
  if (!origin || origin.provider !== 'luxalgo') return null
  const licence = origin.licence || UNREAD_LICENCE
  return licence === UNREAD_LICENCE || NON_COMMERCIAL.test(licence) ? licence : null
}

/** What the confirmation of going live says of a rule whose script is LuxAlgo's. */
export function goLiveSaid(origin: ScriptOrigin): string {
  const from = origin.derived ? 'is derived from one of LuxAlgo\'s indicators' : `comes from LuxAlgo's Library (${origin.name ?? origin.slug})`
  return `Its script ${from}, under ${origin.licence}: its terms are yours to check before trading it.`
}

// ── what WATA's check found of an indicator ──

export const COMPAT_LABEL: Record<LuxAlgoCompat['state'], string> = {
  runs: 'Runs on WATA',
  no_alerts: 'No alerts',
  fails: 'Does not compile',
  cut: 'Needs a sign-in',
}

export const COMPAT_TONE: Record<LuxAlgoCompat['state'], 'success' | 'warning' | 'danger' | 'neutral'> = {
  runs: 'success',
  no_alerts: 'warning',
  fails: 'danger',
  cut: 'neutral',
}

function alertsSaid(compat: LuxAlgoCompat): string {
  const titles = compat.alertconditions ?? []
  const parts = []
  if (titles.length > 0) parts.push(`declares ${titles.length} alert${titles.length === 1 ? '' : 's'} (${titles.join(', ')})`)
  if (compat.alert_calls) parts.push(`calls alert() ${compat.alert_calls === 1 ? 'once' : `${compat.alert_calls} times`}`)
  return parts.join(' and ')
}

/** What WATA's check found, in a sentence. */
export function compatSaid(compat: LuxAlgoCompat): string {
  const strategy = compat.strategy ? " It is a strategy(): its orders are not signals, only its alerts are." : ''
  if (compat.state === 'cut') return "LuxAlgo gave a part of it: sign in to LuxAlgo to have it checked whole."
  if (compat.state === 'fails') return `It does not compile on WATA: ${compat.error ?? 'PineTS refused it'}.`
  if (compat.state === 'no_alerts') {
    return `It compiles on WATA but declares no alert: add alertcondition() lines in a version of yours to make signals.${strategy}`
  }
  return `It compiles on WATA and ${alertsSaid(compat)}: a signal rule maps them to actions.${strategy}`
}

/** The badges of a card of the Library, for an indicator WATA knows: taken, tested, checked or not. */
export function cardBadges(known: LuxAlgoKnownShort | undefined): { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[] {
  if (!known) return []
  const badges: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[] = []
  if (known.scripts.length > 0) badges.push({ label: 'Taken', tone: 'neutral' })
  if (known.verdict) badges.push({ label: `Tested: ${TESTED[known.verdict.tone]}`, tone: VERDICT_TONE[known.verdict.tone] })
  badges.push(known.compat
    ? { label: COMPAT_LABEL[known.compat.state], tone: COMPAT_TONE[known.compat.state] }
    : { label: 'Not checked', tone: 'neutral' })
  return badges
}

const TESTED: Record<AgentVerdictTone, string> = { positive: 'it holds', mixed: 'in part', negative: 'no', neutral: 'nothing to judge' }
const VERDICT_TONE: Record<AgentVerdictTone, 'success' | 'warning' | 'danger' | 'neutral'> = {
  positive: 'success', mixed: 'warning', negative: 'danger', neutral: 'neutral',
}

/** Whether WATA knows anything of an indicator yet. */
export function knowsNothing(known: Pick<LuxAlgoKnown, 'scripts' | 'runs' | 'rules' | 'verdict' | 'compat'> | undefined): boolean {
  return !known || (known.scripts.length === 0 && known.runs.length === 0 && known.rules.length === 0 && !known.verdict && !known.compat)
}

/** The checked indicators a filter keeps: every one, or those of one state. */
export function checkedOf(items: LuxAlgoCompat[], state: LuxAlgoCompat['state'] | null): LuxAlgoCompat[] {
  return state ? items.filter((item) => item.state === state) : items
}

// ── LuxAlgo's pictures ──

/** LuxAlgo's preview picture of an indicator, when its answer names one on LuxAlgo's image host; null otherwise. */
export function previewImage(url: string | null | undefined): string | null {
  if (!url) return null
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' || parsed.host !== IMAGE_HOST || parsed.search || parsed.hash) return null
  return IMAGE_PATH.test(parsed.pathname) ? parsed.href : null
}

/** The tint of a picture's stand-in: a hue of its own for each family, light enough for both themes. */
export function familyTint(family: string | null | undefined): string {
  let hue = 210
  for (const char of family ?? '') hue = (hue * 31 + char.charCodeAt(0)) % 360
  return `linear-gradient(135deg, hsl(${hue} 70% 55% / 0.22), hsl(${(hue + 40) % 360} 70% 55% / 0.06))`
}
