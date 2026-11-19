import { useQuery } from '@tanstack/react-query'
import { ShortcutsDialog, StateSummary, TextLink, UserMenu } from 'ioiti-ui'
import type { Menu } from 'ioiti-ui'
import { RouterAppShell, RouterLink } from 'ioiti-ui/react-router'
import { Keyboard } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { CommandPalette } from '@/components/CommandPalette'
import { menuBadges } from '@/components/menuBadges'
import { MENU_ICONS } from '@/components/menuIcons'
import { ADMIN_MENU, isWidePage } from '@/components/navigation'
import type { MenuSection } from '@/components/navigation'
import { stateItems } from '@/components/stateItems'
import { useAuth, useSession } from '@/features/auth/auth-context'
import { EventLogProvider } from '@/features/live/EventLogProvider'
import { PendingRemovalsProvider } from '@/features/backtests/PendingRemovalsProvider'
import { LivePill } from '@/features/dashboard/LivePill'
import { EventsBell } from '@/features/live/EventsBell'
import { FollowProvider } from '@/features/live/FollowProvider'
import { recordLiveValues } from '@/features/positions/liveSamples'
import { useLiveEvents } from '@/features/live/useLiveEvents'
import { api } from '@/lib/api'
import type { Session } from '@/lib/types'

/** Where the browser keeps the reader's choice of the folded sidebar (⌘B), beside the theme's `wata.theme`. */
const SIDEBAR_COLLAPSED_KEY = 'wata.sidebar.collapsed'

/**
 * ioiti-ui's shell (the sidebar, the top bar with the trail of where the page is, the page) with what
 * is WATA's in it: the states the operator must not miss, summed up in one control, the live pill, the
 * palette, the bell, the operator's menu (the look, the keys, signing out), and the sidebar's badges.
 */
export function AppShell() {
  return (
    <EventLogProvider>
      <FollowProvider>
        <PendingRemovalsProvider>
          <Shell />
        </PendingRemovalsProvider>
      </FollowProvider>
    </EventLogProvider>
  )
}

/**
 * Who is signed in: the admin has one operator. Under the name, what the configuration keeps them from doing, when
 * it keeps them from something: the usual (actions and settings) says nothing, and leaves the trail its room.
 */
function operatorDetail(session: Session): string | undefined {
  if (!session.actions_enabled) return 'Read only'
  return session.config_edit_enabled ? undefined : 'Settings locked'
}

function Shell() {
  const session = useSession()
  const { logout } = useAuth()
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  // Shared with the dashboard, which refreshes it more often; a pause or a
  // resume is also announced by a live event, which refreshes it at once. While
  // a position is open its live value is read as often as the dashboard reads it.
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: (query) => (query.state.data?.open_positions?.length ? 5_000 : 60_000),
  })
  // The AI agent's state, for its entry's badge (a question waiting, its pause, what it does); its live events read
  // it again at once (liveEvents.ts).
  const agentQuery = useQuery({ queryKey: ['agent'], queryFn: api.agent, staleTime: 10_000, refetchInterval: 60_000 })
  // Every page's reading of the live values, kept for the lines of the open positions.
  useEffect(() => {
    if (overviewQuery.data) recordLiveValues(overviewQuery.data.open_positions ?? [])
  }, [overviewQuery.data])
  const live = useLiveEvents()
  const [shortcutsOpen, setShortcutsOpen] = useState(false)

  // The menu itself is in navigation.ts, which the trail of the top bar reads too; its icons in menuIcons.tsx, its
  // badges in menuBadges.ts. Each entry a link: it can be opened in another tab as well as followed.
  const badges = useMemo(
    () => menuBadges({ health: healthQuery.data, overview: overviewQuery.data, session, agent: agentQuery.data }),
    [healthQuery.data, overviewQuery.data, session, agentQuery.data],
  )
  const menu: Menu<MenuSection> = useMemo(
    () => ({ ...ADMIN_MENU, entries: ADMIN_MENU.entries.map((entry) => ({ ...entry, icon: MENU_ICONS[entry.id], ...badges[entry.id] })) }),
    [badges],
  )
  const states = useMemo(() => stateItems({ health: healthQuery.data, session, live }), [healthQuery.data, session, live])

  return (
    <>
      <RouterAppShell
        menu={menu}
        appDetail={`v${session.version}`}
        linkComponent={RouterLink}
        isWide={isWidePage}
        collapsedStorageKey={SIDEBAR_COLLAPSED_KEY}
        // Drawn below, so that the operator's menu opens it as ? does.
        shortcutsDialog={false}
        topBarChips={
          <StateSummary
            label="Status"
            items={states}
            footer={<TextLink to="/system" variant="quiet">Every service, on the System page</TextLink>}
          />
        }
        topBarEnd={
          <>
            <LivePill />
            <CommandPalette />
            <EventsBell />
            <UserMenu
              name="Operator"
              detail={operatorDetail(session)}
              items={[{ label: 'Keyboard shortcuts', icon: <Keyboard />, shortcut: '?', onSelect: () => setShortcutsOpen(true) }]}
              // The theme, the accent and the material: each browser's own (wata.theme…), nothing of them in config.json.
              appearance
              onSignOut={() => void logout()}
            />
          </>
        }
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </>
  )
}
