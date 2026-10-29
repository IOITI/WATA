// What the pages say of the release each container runs and of applying the newest one (src/admin/release.py,
// src/release/): pure, so that what can be said wrong is tested.

import type { BadgeTone } from 'ioiti-ui'

import type {
  ReleaseApply,
  ReleaseContainer,
  ReleaseConfigImage,
  ReleaseState,
  ReleaseStepState,
  ReleaseSummary,
} from '@/lib/types'

// What records what cannot be fetched again, and what trades or decides when to: a deploy holds them back while
// the market is open (RECORDERS and TRADING_PATH of src/release/guards.py), so their confirmation asks for a slide.
export const RECORDING_ROLES = new Set(['price_recorder', 'capital_feed'])
const CAREFUL_ROLES = new Set(['trader', 'position_monitor', 'watchlist_manager', 'web_server', 'scheduler', ...RECORDING_ROLES])
// The files of a reason named before "and N more".
const SHOWN_FILES = 3
// A request the server has said nothing of after this long was not taken (watadeploy-requests.path).
export const REQUEST_GIVE_UP_MS = 75_000

export const RELEASE_STEP_LABEL: Record<ReleaseStepState, string> = {
  waiting: 'Waiting',
  recreating: 'Recreating',
  starting: 'Starting',
  ready: 'At work',
  not_ready: 'Not seen at work',
  failed: 'Did not stay up',
  rolled_back: 'Put back',
  held: 'Held back',
  skipped: 'Not applied',
}

export const RELEASE_STEP_TONE: Record<ReleaseStepState, BadgeTone> = {
  waiting: 'neutral',
  recreating: 'warning',
  starting: 'warning',
  ready: 'success',
  not_ready: 'danger',
  failed: 'danger',
  rolled_back: 'danger',
  held: 'warning',
  skipped: 'neutral',
}

function counted(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

function listed(names: string[]): string {
  return names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** The containers that wait for the newest release: a change, or the platform alone. */
export function waitingContainers(state: ReleaseState): ReleaseContainer[] {
  return (state.status.containers ?? []).filter((entry) => entry.kind === 'change' || entry.kind === 'optional')
}

/** Whether applying ``entry`` restarts something that trades or records. */
export function isCareful(entry: Pick<ReleaseContainer, 'role'>): boolean {
  return CAREFUL_ROLES.has(entry.role)
}

/** What changed for a container, in a few words. */
export function reasonsSaid(entry: ReleaseContainer): string {
  const reasons = entry.reasons ?? {}
  const parts: string[] = []
  if (reasons.code) {
    const shown = reasons.code.slice(0, SHOWN_FILES).join(', ')
    const more = reasons.code.length > SHOWN_FILES ? ` and ${reasons.code.length - SHOWN_FILES} more` : ''
    parts.push(`its code (${counted(reasons.code.length, 'file')}: ${shown}${more})`)
  }
  if (reasons.libraries?.length) {
    parts.push(
      `its libraries (${reasons.libraries.map((found) => `${found.name} ${found.from ?? '–'} → ${found.to ?? '–'}`).join(', ')})`,
    )
  }
  if (reasons.compose?.length) parts.push(`its compose definition (${reasons.compose.join(', ')})`)
  if (reasons.image) parts.push(reasons.image)
  if (reasons.unknown) parts.push(reasons.unknown)
  if (reasons.platform) parts.push(`only ${reasons.platform}: optional`)
  return parts.length ? parts.join('; ') : 'nothing'
}

/** The files of its code that differ, all of them: what a hint lists. */
export function changedFiles(entry: ReleaseContainer): string | null {
  return entry.reasons?.code?.length ? entry.reasons.code.join(', ') : null
}

/** The release a container runs, and whether it waits for the newest. */
export function versionSaid(entry: ReleaseContainer | undefined, newest: string | undefined): string {
  if (!entry) return '—'
  if (entry.kind === 'absent') return 'Not created'
  const version = entry.version ?? 'unknown'
  if (entry.kind === 'change') return `${version} · waits for ${newest}`
  if (entry.kind === 'optional') return `${version} · ${newest} optional`
  if (newest && version !== newest && entry.kind === 'unchanged') return `${version} · same code as ${newest}`
  return version
}

/** What holds a waiting container back now: the admin's look first, watadeploy's last one otherwise. */
export function heldSaid(state: ReleaseState, entry: ReleaseContainer): string | null {
  if (state.held[entry.container]) return state.held[entry.container]
  if (entry.held_with?.length) return `waits with ${listed(entry.held_with)}, which change because of the same files`
  return null
}

/** Why applying cannot be asked from here now; null when it can. */
export function applyUnavailable(state: ReleaseState): string | null {
  if (!state.available) return state.error ?? 'watadeploy has said nothing yet on this server'
  if (!state.actions_enabled) return 'Operator actions are switched off in the configuration (admin.actions_enabled)'
  if (state.applying) return 'An apply is under way: one at a time'
  if (waitingContainers(state).length === 0) return `Every container runs the code of ${state.status.newest}`
  return null
}

/** The page's first line: the newest release, who runs it, who waits for it. */
export function releaseSaid(state: ReleaseState): string {
  const newest = state.status.newest
  if (!newest) return 'No release known yet.'
  const containers = state.status.containers ?? []
  const running = containers.filter((entry) => entry.kind === 'unchanged' && entry.version === newest).length
  const same = containers.filter((entry) => entry.kind === 'unchanged' && entry.version && entry.version !== newest).length
  const waiting = waitingContainers(state)
  const parts = [`${newest} is the newest release installed`]
  if (running) parts.push(`${counted(running, 'container')} run${running === 1 ? 's' : ''} it`)
  if (same) parts.push(`${same} run${same === 1 ? 's' : ''} an older one with the same code`)
  parts.push(waiting.length ? `${counted(waiting.length, 'container')} wait${waiting.length === 1 ? 's' : ''} for it` : 'nothing waits for it')
  return `${parts.join('; ')}.`
}

/** Images in use that would not start on config.json as it is now. */
export function refusingImages(state: ReleaseState): ReleaseConfigImage[] {
  return (state.status.config?.images ?? []).filter((image) => image.accepted === false)
}

/** What an apply came to, in a sentence. */
export function applyOutcome(apply: ReleaseApply): string {
  const of = (...states: ReleaseStepState[]) => apply.steps.filter((step) => states.includes(step.state))
  const parts: string[] = []
  const ready = of('ready').map((step) => step.container)
  if (ready.length) parts.push(`${listed(ready)}: at work on ${apply.version}`)
  for (const step of of('failed', 'rolled_back', 'not_ready', 'held')) parts.push(`${step.container}: ${step.detail ?? RELEASE_STEP_LABEL[step.state]}`)
  const skipped = of('skipped').map((step) => step.container)
  if (skipped.length) parts.push(`not applied: ${listed(skipped)}`)
  if (apply.error) parts.push(apply.error)
  // Not capitalised: it starts with a container's name.
  return parts.length ? `${parts.join('; ')}.` : 'Nothing was applied.'
}

/** Whether an apply ended with every container it moved at work. */
export function applySucceeded(apply: ReleaseApply): boolean {
  return !apply.error && apply.steps.every((step) => step.state === 'ready' || step.state === 'held')
}

/**
 * What became of a request asked from this page: taken, refused or done, as watadeploy says it; null
 * while it says nothing yet, or 'lost' once that took longer than the server's path unit would.
 */
export function requestFollowed(
  state: ReleaseState | undefined,
  requestId: string | null,
  askedAt: number | null,
  now: number,
): { state: 'accepted' | 'refused' | 'done' | 'lost'; detail?: string } | null {
  if (!requestId) return null
  const found = state?.status.requests?.find((request) => request.id === requestId)
  if (found) return { state: found.state, detail: found.detail }
  if (state?.applying?.request === requestId) return { state: 'accepted' }
  if (askedAt !== null && now - askedAt > REQUEST_GIVE_UP_MS) {
    return {
      state: 'lost',
      detail:
        'The server has not taken the request: is watadeploy-requests.path enabled there (systemctl status watadeploy-requests.path)?',
    }
  }
  return null
}

/** What the top bar's chip says: the newest release and what waits for it. */
export function releaseChipHint(summary: ReleaseSummary): string {
  if (summary.applying) return `A deploy is applying ${summary.newest}: the System page follows it.`
  return `${summary.newest} waits for ${listed(summary.waiting)}: apply it on the System page, once nothing holds it back.`
}
