// Technical Analysis › Indicators: the Pine scripts written in WATA (indicators,
// strategies and the libraries they import), kept by the admin as .pine files.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChartSpline, Copy, Download, FileUp, Plus, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { PageHeader } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { api, ApiError, taScriptSourceUrl } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { TaScriptKind, TaScriptSummary } from '@/lib/types'

import { chartsPath } from './chartLinks'
import { declaredTitle, importersOf, KIND_LABEL } from './templates'

const KIND_TONE: Record<TaScriptKind, 'neutral' | 'success' | 'warning'> = {
  indicator: 'success',
  strategy: 'warning',
  library: 'neutral',
}

function scriptColumns(
  timezone: string,
  importers: Map<string, string[]>,
  act: (action: 'duplicate' | 'delete', script: TaScriptSummary) => void,
): ColumnDef<TaScriptSummary>[] {
  return [
    {
      id: 'name',
      header: 'Script',
      accessorFn: (row) => `${row.name} ${row.description} ${row.library_name ?? ''}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-96 whitespace-normal">
          <Link to={`/ta/indicators/${row.id}`} className="font-medium underline-offset-4 hover:underline">
            {row.name}
          </Link>
          {row.description && <p className="text-xs text-muted-foreground">{row.description}</p>}
        </div>
      ),
    },
    {
      id: 'kind',
      header: 'Kind',
      accessorFn: (row) => KIND_LABEL[row.kind],
      sortable: true,
      filterable: true,
      filterOptions: (['indicator', 'strategy', 'library'] as const).map((kind) => ({ label: KIND_LABEL[kind], value: KIND_LABEL[kind] })),
      cell: ({ row }) => <Badge tone={KIND_TONE[row.kind]}>{KIND_LABEL[row.kind]}</Badge>,
    },
    {
      id: 'pine',
      header: 'Pine',
      accessorFn: (row) => row.pine_version,
      sortable: true,
      cell: ({ row }) => `v${row.pine_version}`,
    },
    {
      id: 'version',
      header: 'Version',
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
      accessorFn: (row) => row.imports.map((entry) => entry.name).join(' '),
      cell: ({ row }) => {
        if (row.kind === 'library') {
          const users = importers.get(row.library_name ?? '') ?? []
          return (
            <span className="text-xs text-muted-foreground">
              library("{row.library_name}"){users.length > 0 ? `, used by ${users.join(', ')}` : ', not imported yet'}
            </span>
          )
        }
        return row.imports.length === 0 ? (
          <span className="text-xs text-muted-foreground">none</span>
        ) : (
          <span className="text-xs">{row.imports.map((entry) => entry.name).join(', ')}</span>
        )
      },
    },
    {
      id: 'actions',
      header: 'Actions',
      align: 'right',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          {row.kind !== 'library' && (
            <Link
              to={chartsPath({ script: row.id })}
              className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-hover hover:text-foreground"
              aria-label={`Open ${row.name} in Charts`}
              title="Open in Charts"
            >
              <ChartSpline className="size-4" />
            </Link>
          )}
          <a
            href={taScriptSourceUrl(row.id)}
            className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-hover hover:text-foreground"
            aria-label={`Download ${row.name} as a .pine file`}
            title="Download .pine"
          >
            <Download className="size-4" />
          </a>
          {row.kind !== 'library' && (
            <Button variant="ghost" size="icon" aria-label={`Duplicate ${row.name}`} title="Duplicate" onClick={() => act('duplicate', row)}>
              <Copy className="size-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" aria-label={`Delete ${row.name}`} title="Delete" onClick={() => act('delete', row)}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ),
    },
  ]
}

export function IndicatorsPage() {
  const { timezone } = useSession()
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
    mutationFn: async (file: File) => {
      const source = await file.text()
      const name = declaredTitle(source) ?? file.name.replace(/\.(pine|txt)$/i, '')
      return api.createTaScript({ name, source })
    },
    onSuccess: (created) => {
      refresh()
      navigate(`/ta/indicators/${created.id}`)
    },
    onError: (error) => setImportError(error instanceof Error ? error.message : String(error)),
  })

  const columns = useMemo(
    () =>
      scriptColumns(timezone, importers, (action, script) => {
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
    [timezone, importers],
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
            <Button variant="outline" size="sm" onClick={() => navigate('/ta/indicators/new?kind=library')}>
              New library
            </Button>
            <Button variant="outline" size="sm" onClick={() => navigate('/ta/indicators/new?kind=strategy')}>
              New strategy
            </Button>
            <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => navigate('/ta/indicators/new?kind=indicator')}>
              New indicator
            </Button>
          </>
        }
      />

      {importError && (
        <p role="alert" className="text-sm text-negative">
          The file could not be imported: {importError}
        </p>
      )}

      <QueryState isPending={scriptsQuery.isPending} error={scriptsQuery.error} hasData={!!scripts}>
        {scripts && (
          <div className="flex flex-col gap-2">
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
