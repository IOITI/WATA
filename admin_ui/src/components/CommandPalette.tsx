import { useQuery } from '@tanstack/react-query'
import { Hint, SpotlightSearch } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { assetItems, conversationItems, idItems, pageItems, ruleItems, runItems, scriptItems } from '@/components/commandItems'
import { useSession } from '@/features/auth/auth-context'
import { useHiddenRuns } from '@/features/backtests/pending-removals-context'
import { api } from '@/lib/api'

const PAGES = pageItems()

/**
 * ⌘K (Ctrl+K) anywhere, or the top bar's button: go to a page, a run, a rule,
 * a script, an asset, a position or an order by name or number. What the pages
 * list is read only once the palette is open, through the pages' own queries.
 */
export function CommandPalette() {
  const { backtests_enabled: backtestsEnabled } = useSession()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  // A run whose removal waits for its Undo is not offered.
  const hiddenRuns = useHiddenRuns()

  const runsQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, enabled: open && backtestsEnabled })
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled: open })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled: open })
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), enabled: open, staleTime: 60_000 })
  // The agent's conversations: refused (403) while it is off, which lists none.
  const conversationsQuery = useQuery({ queryKey: ['agent-conversations', 'all'], queryFn: () => api.agentConversations(), enabled: open, retry: false })

  const items = useMemo(
    () => [
      ...idItems(query),
      ...PAGES,
      ...runItems((runsQuery.data?.items ?? []).filter((run) => !hiddenRuns.has(run.id))),
      ...ruleItems(rulesQuery.data?.rules ?? []),
      ...scriptItems(scriptsQuery.data?.items ?? []),
      ...assetItems(assetsQuery.data?.items ?? []),
      ...conversationItems(conversationsQuery.data?.items ?? []),
    ],
    [query, runsQuery.data, hiddenRuns, rulesQuery.data, scriptsQuery.data, assetsQuery.data, conversationsQuery.data],
  )

  return (
    <>
      <Hint content="Go to… (⌘K)" side="bottom">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Go to a page, run, rule, script or asset (⌘K)"
          className="grid size-9 shrink-0 place-items-center rounded-lg text-neutral-500 transition-colors hover:bg-neutral-200/70 hover:text-neutral-900 focus-ring dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white"
        >
          <Search className="size-[18px]" />
        </button>
      </Hint>
      <SpotlightSearch
        open={open}
        onOpenChange={setOpen}
        enableGlobalShortcut
        items={items}
        onQueryChange={setQuery}
        onNavigate={(href) => navigate(href)}
        linkComponent={RouterLink}
        label="Go to"
        placeholder="A page, a run, a rule, a script, an asset, a position's number…"
      />
    </>
  )
}
