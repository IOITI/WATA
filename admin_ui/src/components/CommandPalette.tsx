import { useQuery } from '@tanstack/react-query'
import { Hint, Kbd, SpotlightSearch } from 'ioiti-ui'
import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { assetItems, conversationItems, memoryItems, pageItems, paletteItems, ruleItems, runItems, scriptItems } from '@/components/commandItems'
import { useSession } from '@/features/auth/auth-context'
import { useHiddenRuns } from '@/features/backtests/pending-removals-context'
import { api } from '@/lib/api'
import { REMEMBERED } from '@/lib/remembered'

const PAGES = pageItems()

/**
 * ⌘K (Ctrl+K) anywhere, or the top bar's button: go to a page, a run, a rule,
 * a script, an asset, a position or an order by name or number, or open the
 * list of one kind and search it alone. What the pages list is read only once
 * the palette is open, through the pages' own queries. The entries chosen last
 * are offered first, kept in this browser until the operator signs out. An
 * entry is followed through the application's router (ioiti-ui's RouterRoot).
 */
export function CommandPalette() {
  const { backtests_enabled: backtestsEnabled } = useSession()
  const [open, setOpen] = useState(false)
  // What is typed at the top: a list opened is searched by the palette alone.
  const [query, setQuery] = useState('')
  // A run whose removal waits for its Undo is not offered.
  const hiddenRuns = useHiddenRuns()

  const runsQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, enabled: open && backtestsEnabled })
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled: open })
  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled: open })
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), enabled: open, staleTime: 60_000 })
  // The agent's conversations and memory: refused (403) while it is off, which lists none and is no failure.
  const conversationsQuery = useQuery({ queryKey: ['agent-conversations', 'all'], queryFn: () => api.agentConversations(), enabled: open, retry: false })
  const memoryQuery = useQuery({ queryKey: ['agent-memory'], queryFn: api.agentMemory, enabled: open, retry: false })

  const lists = useMemo(() => ({
    runs: !backtestsEnabled ? null : runsQuery.data ? runItems(runsQuery.data.items.filter((run) => !hiddenRuns.has(run.id))) : undefined,
    rules: rulesQuery.data ? ruleItems(rulesQuery.data.rules) : undefined,
    scripts: scriptsQuery.data ? scriptItems(scriptsQuery.data.items) : undefined,
    assets: assetsQuery.data ? assetItems(assetsQuery.data.items) : undefined,
    conversations: conversationsQuery.isError ? null : conversationsQuery.data ? conversationItems(conversationsQuery.data.items) : undefined,
    memory: memoryQuery.isError ? null : memoryQuery.data ? memoryItems(memoryQuery.data.entries) : undefined,
  }), [backtestsEnabled, runsQuery.data, hiddenRuns, rulesQuery.data, scriptsQuery.data, assetsQuery.data, conversationsQuery.data,
    conversationsQuery.isError, memoryQuery.data, memoryQuery.isError])
  const items = useMemo(() => paletteItems({ query, pages: PAGES, lists }), [query, lists])

  // Said as the palette searches: a name typed before its list is read is not "No results".
  const read = [runsQuery, rulesQuery, scriptsQuery, assetsQuery]
  const loading = open && [...read, conversationsQuery, memoryQuery].some((entry) => entry.isFetching && entry.data === undefined)
  const failed = read.filter((entry) => entry.isError)

  return (
    <>
      <Hint content={<span className="inline-flex items-center gap-2">Go to… <Kbd keys="mod+k" /></span>} side="bottom">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Go to a page, run, rule, script or asset"
          className="grid size-9 shrink-0 place-items-center rounded-lg text-neutral-500 transition-colors hover:bg-neutral-200/70 hover:text-neutral-900 focus-ring dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white"
        >
          <Search className="size-[18px]" />
        </button>
      </Hint>
      <SpotlightSearch
        open={open}
        onOpenChange={setOpen}
        enableGlobalShortcut
        remember={REMEMBERED.goTo}
        items={items}
        onQueryChange={(text, scope) => setQuery(scope ? '' : text)}
        loading={loading}
        error={failed.length > 0 ? 'Some of what the pages list could not be read.' : undefined}
        onRetry={failed.length > 0 ? () => failed.forEach((entry) => void entry.refetch()) : undefined}
        label="Go to"
        placeholder="A page, a run, a rule, a script, an asset, a position's number…"
      />
    </>
  )
}
