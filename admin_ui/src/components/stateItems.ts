import type { StateItem } from 'ioiti-ui'
import { KeyRound, Rocket, RotateCw, ServerCrash, TriangleAlert } from 'lucide-react'
import { createElement } from 'react'

import { releaseChipHint } from '@/features/system/release'
import { restartNeededHint } from '@/features/system/restarts'
import { SAXO_SERVICE_ID } from '@/lib/types'
import type { Health, ServiceHealth, Session } from '@/lib/types'

/**
 * What the top bar's summary of states says (ioiti-ui's StateSummary, AppShell.tsx): each state the operator must
 * not miss, while it applies, and the quiet ones once it is opened. Saxo's login first, since nothing trades without
 * it; then the services, a release, a restart, and whether the page is told of changes as they happen. The
 * sidebar's badges (menuBadges.ts) say the same states where they are dealt with.
 */

export interface StateFacts {
  health?: Health
  session: Session
  /** Whether the live events reach the page (useLiveEvents). */
  live: boolean
}

export const SAXO_AUTHORISE_HINT =
  'The Saxo token can no longer be renewed: nothing can be traded until a new authorisation is given, on the System page.'

function listed(names: readonly string[]): string {
  return names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

/** The services but Saxo's login (a row of its own), as the System page lists them. */
function servicesItem(services: readonly ServiceHealth[]): StateItem {
  const down = services.filter((service) => service.status === 'down')
  const warning = services.filter((service) => service.status === 'warning')
  if (down.length > 0) {
    return {
      id: 'services',
      tone: 'danger',
      icon: createElement(ServerCrash),
      label: down.length === 1 ? `${down[0].name}: down` : `${down.length} services down`,
      detail: down.length === 1 ? down[0].detail : `${listed(down.map((service) => service.name))}.`,
      href: '/system',
    }
  }
  if (warning.length > 0) {
    return {
      id: 'services',
      tone: 'warning',
      icon: createElement(TriangleAlert),
      label: warning.length === 1 ? `${warning[0].name}: warning` : `${warning.length} services to watch`,
      detail: warning.length === 1 ? warning[0].detail : `${listed(warning.map((service) => service.name))}.`,
      href: '/system',
    }
  }
  return { id: 'services', tone: 'ok', label: 'All services up', href: '/system' }
}

export function stateItems({ health, session, live }: StateFacts): StateItem[] {
  const items: StateItem[] = []
  const saxo = health?.services.find((service) => service.id === SAXO_SERVICE_ID)
  if (saxo?.status === 'down') {
    items.push({ id: 'saxo', tone: 'danger', icon: createElement(KeyRound), label: 'Saxo: authorise', detail: SAXO_AUTHORISE_HINT, href: '/system' })
  } else if (saxo?.status === 'ok') {
    items.push({ id: 'saxo', tone: 'ok', label: 'Saxo: authorised', href: '/system' })
  }
  if (health) items.push(servicesItem(health.services.filter((service) => service.id !== SAXO_SERVICE_ID)))

  const release = session.release
  if (release && (release.applying || release.waiting.length > 0)) {
    items.push({
      id: 'release',
      tone: 'warning',
      icon: createElement(Rocket),
      label: release.applying ? 'Deploying' : 'Release waiting',
      detail: releaseChipHint(release),
      href: '/system#release',
    })
  }
  if (session.restart_required) {
    items.push({
      id: 'restart',
      tone: 'warning',
      icon: createElement(RotateCw),
      label: 'Restart needed',
      detail: restartNeededHint(session),
      href: session.restart_services ? '/system' : '/config',
    })
  }

  items.push(
    live
      ? { id: 'live', tone: 'ok', label: 'Live updates', detail: 'Changes are pushed to this page as they happen.' }
      : { id: 'live', tone: 'neutral', label: 'Polling', detail: 'Not receiving live updates: the page refreshes on a timer.' },
  )
  return items
}
