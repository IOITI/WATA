// Technical Analysis › Indicators: the Pine scripts written in WATA (indicators,
// strategies and the libraries they import), kept by the admin as .pine files.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  AnimatedFileUpload,
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  Input,
  PageHeader,
  QueryState,
  SettleModal,
} from 'ioiti-ui'
import type { ColumnDef, UploadFileItem, UploadResult } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { ChartSpline, Copy, Download, FileUp, FlaskConical, Plus, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { api, ApiError, taScriptSourceUrl } from '@/lib/api'
import type { TaScriptKind, TaScriptSummary } from '@/lib/types'

import { chartsPath } from './chartLinks'
import { LuxAlgoBadge } from './luxalgo/LuxAlgoBadge'
import { importScript, librariesFirst, MAX_SCRIPT_BYTES } from './importScripts'
import { importersOf, KIND_LABEL, librariesOf } from './templates'

const KIND_TONE: Record<TaScriptKind, 'neutral' | 'success' | 'warning'> = {
  indicator: 'success',
  strategy: 'warning',
  library: 'neutral',
}

// A column of short values as narrow as they are: the room left goes to the scripts and their libraries.
// Left to itself, the browser gives it all to the scripts, whose descriptions are the longest text.
const FIT = '1%'
const LIBRARIES_SHARE = '28%'

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

function scriptColumns(
  timezone: string,
  importers: Map<string, TaScriptSummary[]>,
  libraries: Map<string, TaScriptSummary>,
  backtests: boolean,
  act: (action: 'duplicate' | 'delete', script: TaScriptSummary) => void,
): ColumnDef<TaScriptSummary>[] {
  return [
    {
      id: 'name',
      header: 'Script',
      accessorFn: (row) =>
        `${row.name} ${row.description} ${row.library_name ?? ''}${madeByClaude(row) ? ' Claude' : ''}${row.origin ? ' LuxAlgo' : ''}`,
      sortable: true,
      cell: ({ row }) => (
        // Its floor in the table only: a card on a phone is narrower.
        <div className="max-w-96 whitespace-normal [td_&]:min-w-48">
          <Link to={`/ta/indicators/${row.id}`} className="font-medium underline-offset-4 hover:underline">
            {row.name}
          </Link>
          {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
          {row.origin && <LuxAlgoBadge origin={row.origin} className="ml-2" />}
          {row.description && <p className="text-xs text-muted-foreground">{row.description}</p>}
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
      id: 'libraries',
      header: 'Libraries',
      width: LIBRARIES_SHARE,
      accessorFn: (row) => row.imports.map((entry) => entry.name).join(' '),
      // One name a line, wrapped within the column: on one line, the scripts using a library made it the
      // widest of the table, and pushed the actions out of sight.
      cell: ({ row }) => {
        const users = row.kind === 'library' ? (importers.get(row.library_name ?? '') ?? []) : []
        return (
          <div className="flex max-w-72 flex-col gap-1 whitespace-normal text-xs [td_&]:min-w-48">
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

export function IndicatorsPage() {
  const { timezone, backtests_enabled: backtests } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [deleting, setDeleting] = useState<TaScriptSummary | null>(null)
  const [copying, setCopying] = useState<TaScriptSummary | null>(null)
  const [copyName, setCopyName] = useState('')
  const [importError, setImportError] = useState<string | null>(null)

  const scriptsQuery = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts })
  const scripts = scriptsQuery.data?.items
  const importers = useMemo(() => importersOf(scripts ?? []), [scripts])
  const libraries = useMemo(() => librariesOf(scripts ?? []), [scripts])

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
      scriptColumns(timezone, importers, libraries, backtests, (action, script) => {
        if (action === 'delete') {
          deleteMutation.reset()
          setDeleting(script)
        } else {
          copyMutation.reset()
          setCopyName(`${script.name} copy`)
          setCopying(script)
        }
      }),
    // The mutations' reset is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [timezone, importers, libraries, backtests],
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
          <div className="flex flex-col gap-2 [&_td]:align-top">
            <AdvancedDataTable<TaScriptSummary>
              title="Scripts"
              searchPlaceholder="Search…"
              emptyTitle="No script yet"
              emptyDescription="Write one with “New indicator”, or import a .pine file from TradingView."
              data={scripts}
              columns={columns}
              getRowId={(row) => row.id}
              defaultPageSize={20}
              selectable={false}
            />
            <p className="text-xs text-muted-foreground">
              Every save keeps the version before it. A script imports a library of WATA with{' '}
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
