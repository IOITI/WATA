// Technical Analysis › Indicators: the Pine scripts written in WATA (indicators,
// strategies and the libraries they import), kept by the admin as .pine files; each
// with its star, its family and its picture (src/admin/script_details.py), the list
// narrowed to the favourites or a family by its address, as a table or as cards.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  AnimatedFileUpload,
  AnimatedTabs,
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  Hint,
  Input,
  PageHeader,
  QueryState,
  Select,
  SettleModal,
} from 'ioiti-ui'
import type { ColumnDef, UploadFileItem, UploadResult } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { ChartSpline, Copy, Download, FileUp, FlaskConical, Plus, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'

import { FavouriteStar } from '@/components/FavouriteStar'
import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { judgedRuns } from '@/features/backtests/verdict'
import { madeByClaude } from '@/features/system/claude'
import { api, ApiError, taScriptSourceUrl } from '@/lib/api'
import type { RuleCheckSummary, RuleVerdict, TaRuleMode, TaScriptKind, TaScriptSummary } from '@/lib/types'

import { chartsPath } from './chartLinks'
import { familiesInUse, indicatorsHref, narrowed, readIndicatorsAddress, scriptsShown } from './indicatorsModel'
import type { IndicatorsAddress, IndicatorsView } from './indicatorsModel'
import { LuxAlgoBadge } from './luxalgo/LuxAlgoBadge'
import { badgeOf, summarySaid } from './ruleCheck/ruleCheckModel'
import { useRuleChecks } from './ruleCheck/useRuleCheck'
import { importScript, librariesFirst, MAX_SCRIPT_BYTES } from './importScripts'
import { modeCounts, modesSaid, rulesRunning, runsOfScript, runsSaid, scriptTabHref } from './script/scriptPageModel'
import { PictureButton, PictureChooser, ScriptPicture } from './ScriptPicture'
import { importersOf, KIND_LABEL, librariesOf } from './templates'
import { useFavouriteScripts } from './useScriptDetails'

const KIND_TONE: Record<TaScriptKind, 'neutral' | 'success' | 'warning'> = {
  indicator: 'success',
  strategy: 'warning',
  library: 'neutral',
}

// The runs the list of backtests gives, the newest first (MAX_LISTED of src/admin/backtests.py).
const MAX_LISTED_RUNS = 200

// A column of short values as narrow as they are: the room left goes to the scripts and their libraries.
// Left to itself, the browser gives it all to the scripts, whose descriptions are the longest text.
const FIT = '1%'
const LIBRARIES_SHARE = '28%'
// The column "As a rule" sorted from the readiest.
const LEVEL_ORDER_OF_VERDICT: Record<RuleVerdict, number> = { ready: 0, to_change: 1, may_repaint: 2, needs_alerts: 3, repaints: 4, blocked: 5 }

/** Scripts one under the other, each a link to its page; a name WATA does not hold stays text. */
function ScriptNames({ entries }: { entries: { name: string; script?: TaScriptSummary }[] }) {
  return (
    <ul className="flex flex-col gap-0.5">
      {entries.map((entry) => (
        <li key={entry.script?.id ?? entry.name}>
          {entry.script ? (
            <Link to={`/ta/indicators/${entry.script.id}`} className="underline-offset-4 hover:underline">
              {entry.name}
            </Link>
          ) : (
            entry.name
          )}
        </li>
      ))}
    </ul>
  )
}

// An icon of a row's actions that leads to a page.
const ICON_LINK = 'inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-hover hover:text-foreground'

/** What the list says of each script beside it: the rules that run it, its runs and how many pass every check. */
interface ScriptUses {
  rules: Map<string, Record<TaRuleMode, number>>
  runs: Map<string, { runs: number; passing: number }>
  /** Each script's newest version as a rule (its rule check); null while its report is made. */
  checks: Record<string, RuleCheckSummary | null> | null
}

/** Its star, its name leading to its page, who made it, where it came from and its family. */
function ScriptTitle({ script, onFavourite }: { script: TaScriptSummary; onFavourite: (script: TaScriptSummary) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <FavouriteStar on={Boolean(script.favourite)} name={script.name} onToggle={() => onFavourite(script)} className="-ml-1" />
      <Link to={`/ta/indicators/${script.id}`} className="font-medium underline-offset-4 hover:underline">
        {script.name}
      </Link>
      {madeByClaude(script) && <ClaudeBadge />}
      {script.origin && <LuxAlgoBadge origin={script.origin} />}
      {script.family && <Badge variant="outline">{script.family}</Badge>}
    </div>
  )
}

function scriptColumns(
  timezone: string,
  importers: Map<string, TaScriptSummary[]>,
  libraries: Map<string, TaScriptSummary>,
  backtests: boolean,
  uses: ScriptUses,
  act: (action: 'duplicate' | 'delete', script: TaScriptSummary) => void,
  onFavourite: (script: TaScriptSummary) => void,
): ColumnDef<TaScriptSummary>[] {
  return [
    {
      id: 'name',
      header: 'Script',
      // Searched by its name, description, library, family, "Claude", "LuxAlgo" and "favourite".
      accessorFn: (row) =>
        `${row.name} ${row.description} ${row.library_name ?? ''} ${row.family ?? ''}${madeByClaude(row) ? ' Claude' : ''}${row.origin ? ' LuxAlgo' : ''}${row.favourite ? ' favourite' : ''}`,
      sortable: true,
      cell: ({ row }) => (
        // Its floor in the table only: a card on a phone is narrower.
        <div className="flex max-w-[30rem] items-start gap-3 whitespace-normal [td_&]:min-w-64">
          <PictureButton script={row} className="w-24 shrink-0" />
          <div className="min-w-0">
            <ScriptTitle script={row} onFavourite={onFavourite} />
            {row.description && <p className="mt-0.5 text-xs text-muted-foreground">{row.description}</p>}
          </div>
        </div>
      ),
    },
    {
      id: 'kind',
      header: 'Kind',
      width: FIT,
      accessorFn: (row) => KIND_LABEL[row.kind],
      sortable: true,
      filterable: true,
      filterOptions: (['indicator', 'strategy', 'library'] as const).map((kind) => ({ label: KIND_LABEL[kind], value: KIND_LABEL[kind] })),
      cell: ({ row }) => <Badge tone={KIND_TONE[row.kind]}>{KIND_LABEL[row.kind]}</Badge>,
    },
    {
      id: 'pine',
      header: 'Pine',
      width: FIT,
      accessorFn: (row) => row.pine_version,
      sortable: true,
      cell: ({ row }) => `v${row.pine_version}`,
    },
    {
      id: 'version',
      header: 'Version',
      width: FIT,
      accessorFn: (row) => row.updated_at,
      sortable: true,
      cell: ({ row }) => (
        <div>
          <span className="font-mono text-xs">v{row.version}</span>
          <p className="text-xs text-muted-foreground">{formatDateTime(row.updated_at, timezone, false)}</p>
        </div>
      ),
    },
    {
      id: 'uses',
      // The rules that run it, then its runs and how many pass every check: one column, the table's room being short.
      header: 'In use',
      width: FIT,
      accessorFn: (row) => {
        const counts = uses.rules.get(row.id)
        const rules = counts ? counts.live * 100 + counts.shadow * 10 + counts.off : 0
        return rules * 10_000 + (uses.runs.get(row.id)?.runs ?? 0)
      },
      sortable: true,
      cell: ({ row }) => {
        const counts = uses.rules.get(row.id)
        const rules = counts ? modesSaid(counts) : 'None'
        const runs = uses.runs.get(row.id)
        return (
          // Each line whole in the table (its floor there only: a card on a phone is narrower).
          <div className="flex max-w-44 flex-col gap-0.5 whitespace-normal text-xs [td_&]:min-w-36">
            {rules === 'None' ? (
              <span className="text-muted-foreground">No rule</span>
            ) : (
              <Link to={`/ta/indicators/${row.id}${scriptTabHref('rules')}`} className="underline-offset-4 hover:underline">
                {rules}
              </Link>
            )}
            {backtests &&
              row.kind !== 'library' &&
              (!runs || runs.runs === 0 ? (
                <span className="text-muted-foreground">No run</span>
              ) : (
                <Link to={`/ta/indicators/${row.id}${scriptTabHref('backtests')}`} className="underline-offset-4 hover:underline">
                  {runs.runs} run{runs.runs === 1 ? '' : 's'}, {runs.passing === 0 ? 'none passing' : `${runs.passing} passing`}
                </Link>
              ))}
          </div>
        )
      },
    },
    {
      id: 'rule',
      // Whether its newest version can become a signal rule as it is: the script's "As a rule" tab says more.
      header: 'As a rule',
      width: FIT,
      accessorFn: (row) => {
        const found = uses.checks?.[row.id]
        return found ? LEVEL_ORDER_OF_VERDICT[found.verdict] : 9
      },
      sortable: true,
      cell: ({ row }) => {
        if (row.kind === 'library') return <span className="text-xs text-muted-foreground">Through its scripts</span>
        const found = uses.checks?.[row.id]
        const badge = badgeOf(found)
        if (!found || !badge) return <span className="text-xs text-muted-foreground">{uses.checks ? 'Checking…' : '—'}</span>
        return (
          <Hint content={summarySaid(found)}>
            <Link to={`/ta/indicators/${row.id}${scriptTabHref('rule')}`} aria-label={`${row.name} as a rule: ${badge.label}`}>
              <Badge tone={badge.tone} dot>
                {badge.label}
              </Badge>
            </Link>
          </Hint>
        )
      },
    },
    {
      id: 'libraries',
      header: 'Libraries',
      width: LIBRARIES_SHARE,
      accessorFn: (row) => row.imports.map((entry) => entry.name).join(' '),
      // One name a line, wrapped within the column: on one line, the scripts using a library made it the
      // widest of the table, and pushed the actions out of sight.
      cell: ({ row }) => {
        const users = row.kind === 'library' ? (importers.get(row.library_name ?? '') ?? []) : []
        return (
          <div className="flex max-w-72 flex-col gap-1 whitespace-normal text-xs [td_&]:min-w-40">
            {row.kind === 'library' ? (
              <>
                {/* Broken after its parenthesis when it must, and inside the name only in a card too narrow for it. */}
                <code className="font-mono text-muted-foreground wrap-break-word">
                  library(<wbr />"{row.library_name}")
                </code>
                {users.length > 0 ? (
                  <div>
                    <span className="text-muted-foreground">Used by</span>
                    <ScriptNames entries={users.map((script) => ({ name: script.name, script }))} />
                  </div>
                ) : (
                  <span className="text-muted-foreground">Not imported yet</span>
                )}
              </>
            ) : row.imports.length === 0 ? (
              <span className="text-muted-foreground">None</span>
            ) : (
              <ScriptNames entries={row.imports.map((entry) => ({ name: entry.name, script: libraries.get(entry.name) }))} />
            )}
          </div>
        )
      },
    },
    {
      id: 'actions',
      header: 'Actions',
      align: 'right',
      width: FIT,
      cell: ({ row }) => (
        // Lifted by half their height above a line of text, so that they sit on the row's first line.
        <div className="-my-2 flex justify-end gap-1">
          {row.kind !== 'library' && (
            <Link to={chartsPath({ script: row.id })} className={ICON_LINK} aria-label={`Open ${row.name} in Charts`} title="Open in Charts">
              <ChartSpline className="size-4" />
            </Link>
          )}
          {row.kind !== 'library' && backtests && (
            <Link
              to={`/backtests/new?script=${row.id}&version=${row.version}`}
              className={ICON_LINK}
              aria-label={`Backtest ${row.name}`}
              title={`Backtest version ${row.version}`}
            >
              <FlaskConical className="size-4" />
            </Link>
          )}
          <a
            href={taScriptSourceUrl(row.id)}
            className={ICON_LINK}
            aria-label={`Download ${row.name} as a .pine file`}
            title="Download .pine"
          >
            <Download className="size-4" />
          </a>
          {row.kind !== 'library' && (
            <Button variant="ghost" size="icon" className="size-8" aria-label={`Duplicate ${row.name}`} title="Duplicate" onClick={() => act('duplicate', row)}>
              <Copy className="size-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Delete ${row.name}`} title="Delete" onClick={() => act('delete', row)}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ),
    },
  ]
}

/** The list narrowed to the favourites and a family: in the address, so that a list narrowed can be opened elsewhere, kept or sent. */
function ScriptFilters({ address, families }: { address: IndicatorsAddress; families: string[] }) {
  const navigate = useNavigate()
  const go = (changes: Partial<IndicatorsAddress>) => navigate(indicatorsHref({ ...address, ...changes }), { replace: true })
  // The family the address names, as the list spells it (the list is narrowed whatever its case).
  const named = address.family?.toLocaleLowerCase()
  const family = address.family ? (families.find((entry) => entry.toLocaleLowerCase() === named) ?? address.family) : null
  const offered = family && !families.includes(family) ? [...families, family] : families
  return (
    <div role="group" aria-label="Narrow the list" className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-text-secondary">Favourites</span>
        <Select
          value={address.favourite ?? ''}
          onChange={(event) => go({ favourite: event.target.value === 'yes' ? 'yes' : null })}
          options={[{ value: '', label: 'Every script' }, { value: 'yes', label: 'Favourites only' }]}
        />
      </label>
      {offered.length > 0 && (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-text-secondary">Family</span>
          <Select
            value={family ?? ''}
            onChange={(event) => go({ family: event.target.value || null })}
            options={[{ value: '', label: 'Any' }, ...offered.map((family) => ({ value: family, label: family }))]}
          />
        </label>
      )}
      {narrowed(address) && (
        <ButtonLink to={indicatorsHref({ ...address, favourite: null, family: null })} variant="ghost" size="sm">
          Every script
        </ButtonLink>
      )}
    </div>
  )
}

const VIEW_LABEL: Record<IndicatorsView, string> = { table: 'Table', cards: 'Cards' }

/** The scripts as cards, as LuxAlgo's Library shows its indicators: its picture leading to it, a button to change it. */
function ScriptCards({ scripts, uses, backtests, onFavourite }: {
  scripts: TaScriptSummary[]
  uses: ScriptUses
  backtests: boolean
  onFavourite: (script: TaScriptSummary) => void
}) {
  const [search, setSearch] = useState('')
  const shown = useMemo(() => {
    const words = search.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
    return scripts.filter((script) => {
      const text = `${script.name} ${script.description} ${script.family ?? ''} ${KIND_LABEL[script.kind]}`.toLocaleLowerCase()
      return words.every((word) => text.includes(word))
    })
  }, [scripts, search])
  return (
    <div className="flex flex-col gap-3">
      <Input
        type="search"
        aria-label="Search the scripts"
        placeholder="Search…"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        className="max-w-xs"
      />
      {shown.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">No script here: change what narrows the list.</p>
      ) : (
        <ul aria-label="Scripts" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((script) => {
            const counts = uses.rules.get(script.id)
            const runs = uses.runs.get(script.id)
            return (
              <li key={script.id} className="glass-card flex flex-col overflow-hidden rounded-xl border border-border bg-surface">
                <div className="relative border-b border-border">
                  {/* The name below is the card's link for the keyboard and screen readers: the picture's is for the pointer. */}
                  <Link to={`/ta/indicators/${script.id}`} tabIndex={-1} aria-hidden className="block">
                    <ScriptPicture script={script} />
                  </Link>
                  <PictureChooser script={script} className="absolute right-2 top-2" />
                </div>
                <div className="flex flex-col gap-1.5 p-4">
                  <ScriptTitle script={script} onFavourite={onFavourite} />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge tone={KIND_TONE[script.kind]}>{KIND_LABEL[script.kind]}</Badge>
                    <span className="font-mono">v{script.version}</span>
                    {counts && modesSaid(counts) !== 'None' && <span>{modesSaid(counts)}</span>}
                    {backtests && script.kind !== 'library' && runs && runs.runs > 0 && (
                      <span>
                        {runs.runs} run{runs.runs === 1 ? '' : 's'}
                      </span>
                    )}
                  </div>
                  {script.description && <p className="line-clamp-3 text-sm text-text-secondary">{script.description}</p>}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function IndicatorsPage() {
  const { timezone, backtests_enabled: backtests } = useSession()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const address = useMemo(() => readIndicatorsAddress(params), [params])
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [deleting, setDeleting] = useState<TaScriptSummary | null>(null)
  const [copying, setCopying] = useState<TaScriptSummary | null>(null)
  const [copyName, setCopyName] = useState('')
  const [importError, setImportError] = useState<string | null>(null)

  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts })
  const scripts = scriptsQuery.data?.items
  const shown = useMemo(() => scriptsShown(scripts ?? [], address), [scripts, address])
  const families = useMemo(() => familiesInUse(scripts ?? []), [scripts])
  const { mutate: favour } = useFavouriteScripts()
  const toggleFavourite = useMemo(() => (script: TaScriptSummary) => favour({ ids: [script.id], favourite: !script.favourite }), [favour])
  const importers = useMemo(() => importersOf(scripts ?? []), [scripts])
  const libraries = useMemo(() => librariesOf(scripts ?? []), [scripts])
  // The rules that run each script, and its runs among the newest the list of backtests gives, with their verdicts.
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  const runsQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, enabled: backtests })
  const checksQuery = useRuleChecks()
  const uses = useMemo((): ScriptUses => {
    const rules = rulesQuery.data?.rules ?? []
    const runs = runsQuery.data?.items ?? []
    const judged = judgedRuns(runs)
    return {
      rules: new Map((scripts ?? []).map((script) => [script.id, modeCounts(rulesRunning(script.id, rules))])),
      runs: new Map((scripts ?? []).map((script) => [script.id, runsSaid(runsOfScript(runs, script.id), judged)])),
      checks: checksQuery.data?.scripts ?? null,
    }
  }, [scripts, rulesQuery.data, runsQuery.data, checksQuery.data])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
    void queryClient.invalidateQueries({ queryKey: ['ta-library'] })
  }

  const deleteMutation = useMutation({
    mutationFn: (script: TaScriptSummary) => api.deleteTaScript(script.id),
    onSuccess: () => {
      refresh()
      setDeleting(null)
    },
  })
  const copyMutation = useMutation({
    mutationFn: ({ script, name }: { script: TaScriptSummary; name: string }) => api.duplicateTaScript(script.id, name),
    onSuccess: (copy) => {
      refresh()
      setCopying(null)
      navigate(`/ta/indicators/${copy.id}`)
    },
  })
  const importMutation = useMutation({
    mutationFn: importScript,
    onSuccess: (created) => {
      refresh()
      navigate(`/ta/indicators/${created.id}`)
    },
    onError: (error) => setImportError(error instanceof Error ? error.message : String(error)),
  })
  // Files dropped: one opens in the editor, as the button's does; several stay listed, each with its outcome.
  const dropped = useRef(0)
  const importDropped = async (item: UploadFileItem): Promise<UploadResult> => {
    if (!item.rawFile) throw new Error('The file could not be read')
    const created = await importScript(item.rawFile)
    refresh()
    if (dropped.current === 1) navigate(`/ta/indicators/${created.id}`)
    return { href: `/ta/indicators/${created.id}`, label: 'Open' }
  }

  const columns = useMemo(
    () =>
      scriptColumns(timezone, importers, libraries, backtests, uses, (action, script) => {
        if (action === 'delete') {
          deleteMutation.reset()
          setDeleting(script)
        } else {
          copyMutation.reset()
          setCopyName(`${script.name} copy`)
          setCopying(script)
        }
      }, toggleFavourite),
    // The mutations' reset is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [timezone, importers, libraries, backtests, uses, toggleFavourite],
  )

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setImportError(null)
    importMutation.mutate(file)
  }

  return (
    <>
      <PageHeader
        title="Indicators"
        description="Pine Script indicators, strategies and libraries, run by PineTS on WATA's own prices. Nothing here trades."
        actions={
          <>
            <input ref={fileInput} type="file" accept=".pine,.txt,text/plain" className="hidden" onChange={onFile} />
            <Button
              variant="outline"
              size="sm"
              leftIcon={<FileUp className="size-3.5" />}
              isLoading={importMutation.isPending}
              onClick={() => fileInput.current?.click()}
            >
              Import .pine
            </Button>
            <ButtonLink to="/ta/indicators/new?kind=library" variant="outline" size="sm">
              New library
            </ButtonLink>
            <ButtonLink to="/ta/indicators/new?kind=strategy" variant="outline" size="sm">
              New strategy
            </ButtonLink>
            <ButtonLink to="/ta/indicators/new?kind=indicator" size="sm" leftIcon={<Plus className="size-3.5" />}>
              New indicator
            </ButtonLink>
          </>
        }
      />

      {importError && (
        <p role="alert" className="text-sm text-negative">
          The file could not be imported: {importError}
        </p>
      )}

      <AnimatedFileUpload
        variant="compact"
        multiple
        accept=".pine,.txt"
        maxSize={MAX_SCRIPT_BYTES}
        dropTitle="Drop .pine files here"
        dropSubtitle="or choose them: several at once, libraries first"
        arrange={librariesFirst}
        sequential
        workingLabel="Importing…"
        doneLabel="Imported"
        linkComponent={RouterLink}
        onFilesSelected={(files) => {
          dropped.current = files.length
        }}
        uploadHandler={importDropped}
      />

      <QueryState isPending={scriptsQuery.isPending} error={scriptsQuery.error} hasData={!!scripts}>
        {scripts && (
          // A row as tall as its longest cell: the others start at its top, not halfway down.
          <div className="flex flex-col gap-3 [&_td]:align-top">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <ScriptFilters address={address} families={families} />
              <AnimatedTabs
                label="How the list is shown"
                tabs={(['table', 'cards'] as const).map((view) => ({ id: view, label: VIEW_LABEL[view], href: indicatorsHref({ ...address, view }) }))}
                activeTab={address.view}
                onChange={(view) => navigate(indicatorsHref({ ...address, view: view as IndicatorsView }), { replace: true })}
                linkComponent={RouterLink}
                renderContent={false}
              />
            </div>
            {address.view === 'cards' ? (
              <ScriptCards scripts={shown} uses={uses} backtests={backtests} onFavourite={toggleFavourite} />
            ) : (
              <AdvancedDataTable<TaScriptSummary>
                title="Scripts"
                searchPlaceholder="Search…"
                emptyTitle={narrowed(address) ? 'No script here' : 'No script yet'}
                emptyDescription={
                  narrowed(address)
                    ? 'Change what narrows the list, or show every script.'
                    : 'Write one with “New indicator”, or import a .pine file from TradingView.'
                }
                data={shown}
                columns={columns}
                getRowId={(row) => row.id}
                defaultPageSize={20}
                selectable={false}
              />
            )}
            <p className="text-xs text-muted-foreground">
              Every save keeps the version before it. A script's backtests are counted among the newest {MAX_LISTED_RUNS} runs.
              A script imports a library of WATA with{' '}
              <code className="font-mono">import user/Name/1 as alias</code>, Name being what the library declares in{' '}
              <code className="font-mono">library("Name")</code>.
            </p>
          </div>
        )}
      </QueryState>

      <SettleModal
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? ''}?`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDeleting(null)}>
              Keep it
            </Button>
            <Button
              variant="destructive"
              size="sm"
              isLoading={deleteMutation.isPending}
              onClick={() => deleting && deleteMutation.mutate(deleting)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          It leaves the list with all its versions. They are moved to the trash folder on the server, where they can be
          found by hand.
        </p>
        {deleteMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {deleteMutation.error instanceof ApiError && deleteMutation.error.status === 409
              ? `It cannot be deleted: ${deleteMutation.error.message}.`
              : deleteMutation.error.message}
          </p>
        )}
      </SettleModal>

      <SettleModal
        open={copying !== null}
        onClose={() => setCopying(null)}
        title={`Duplicate ${copying?.name ?? ''}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCopying(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              isLoading={copyMutation.isPending}
              disabled={!copyName.trim()}
              onClick={() => copying && copyMutation.mutate({ script: copying, name: copyName })}
            >
              Duplicate
            </Button>
          </>
        }
      >
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="text-xs font-medium text-text-secondary">Name of the copy</span>
          <Input value={copyName} onChange={(event) => setCopyName(event.target.value)} autoFocus />
        </label>
        {copyMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {copyMutation.error.message}
          </p>
        )}
      </SettleModal>
    </>
  )
}
