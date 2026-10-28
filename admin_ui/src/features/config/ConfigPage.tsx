import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Button, Loader, PageHeader, QueryState, SaveBar, useToast } from 'ioiti-ui'
import { RouterLeaveGuard } from 'ioiti-ui/react-router'
import { useCallback, useMemo, useState } from 'react'
import { Outlet } from 'react-router'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { isSignIn } from '@/features/auth/returnPath'
import { api, ApiError } from '@/lib/api'
import type { ConfigCatalog, ConfigDocument, ConfigHistoryEntry, ConfigSaved, JsonObject, JsonValue } from '@/lib/types'

import { changedParams } from './catalog'
import { diffConfigs, setAt } from './configModel'
import type { ConfigPath } from './configModel'
import { ConfigEditorContext } from './editor-context'
import type { ConfigEditorValue } from './editor-context'
import { DraftProvider } from './fields'
import { ImpactSaid, ReviewChanges } from './ReviewChanges'

interface EditorProps {
  catalog: ConfigCatalog
  doc: ConfigDocument
  history: ConfigHistoryEntry[] | undefined
  lastSaved: ConfigSaved | null
  onSaved: (saved: ConfigSaved) => void
  dismissSaved: () => void
}

/**
 * The draft of config.json, which every view of the page edits (the groups of
 * parameters, the JSON), the bar under it, and the review before a save: what
 * the server finds the change reaches, said before it is written.
 */
function ConfigEditor({ catalog, doc, history, lastSaved, onSaved, dismissSaved }: EditorProps) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<JsonObject>(doc.config)
  // Bumped to remount the fields (which hold what is being typed) when the draft is replaced.
  const [formVersion, setFormVersion] = useState(0)
  const [reviewing, setReviewing] = useState(false)
  // Fields holding something that cannot be used: the draft does not have what they show.
  const [invalidFields, setInvalidFields] = useState<ReadonlySet<string>>(() => new Set())
  const reportInvalid = useCallback((fieldId: string, invalid: boolean) => {
    setInvalidFields((current) => {
      if (current.has(fieldId) === invalid) return current
      const next = new Set(current)
      if (invalid) next.add(fieldId)
      else next.delete(fieldId)
      return next
    })
  }, [])

  const preview = useMutation({ mutationFn: (config: JsonObject) => api.validateConfig(config) })
  const save = useMutation({
    mutationFn: () => api.saveConfig(draft, doc.revision),
    onSuccess: async (saved) => {
      setReviewing(false)
      onSaved(saved)
      toast({ title: 'Configuration saved', description: `Previous version kept as ${saved.backup}.`, variant: 'success' })
      await Promise.all(
        ['config', 'config-backups', 'config-history', 'session'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
      )
    },
  })

  const unsaved = useMemo(() => changedParams(catalog.params, doc.config, draft), [catalog.params, doc.config, draft])
  const leaves = useMemo(() => diffConfigs(doc.config, draft), [doc.config, draft])
  const disabled = !doc.editable
  const invalidCount = invalidFields.size
  const dirty = leaves.length > 0 || invalidCount > 0
  const changeCount = unsaved.size || leaves.length

  const resetPreview = preview.reset
  const replaceDraft = useCallback(
    (next: JsonObject) => {
      resetPreview()
      setDraft(next)
      setFormVersion((version) => version + 1)
    },
    [resetPreview],
  )
  const update = useCallback(
    (path: ConfigPath, value: JsonValue) => {
      resetPreview()
      setDraft((current) => setAt(current, path, value) as JsonObject)
    },
    [resetPreview],
  )

  const editor: ConfigEditorValue = {
    catalog,
    doc,
    draft,
    replaceDraft,
    unsaved,
    disabled,
    formVersion,
    history,
    lastSaved,
    dismissSaved,
  }
  const conflict = save.error instanceof ApiError && save.error.status === 409

  return (
    <ConfigEditorContext.Provider value={editor}>
      <DraftProvider value={{ draft, update, disabled, reportInvalid }}>
        <Outlet />
      </DraftProvider>

      {!reviewing && preview.isSuccess && (
        <Alert tone={preview.data.warnings.length ? 'warning' : 'success'}>
          <AlertTitle>The configuration is valid</AlertTitle>
          <AlertDescription>
            <div className="space-y-1.5">
              {preview.data.warnings.map((warning) => (
                <p key={warning.message}>{warning.message}</p>
              ))}
              <ImpactSaid catalog={catalog} impact={preview.data.impact} />
            </div>
          </AlertDescription>
        </Alert>
      )}
      {!reviewing && preview.isError && (
        <Alert tone="danger" role="alert">
          <AlertTitle>This configuration is refused</AlertTitle>
          <AlertDescription>{preview.error.message}</AlertDescription>
        </Alert>
      )}

      <SaveBar
        open={dirty && !disabled}
        message={changeCount === 0 ? 'Nothing to save yet' : `${changeCount} unsaved change${changeCount === 1 ? '' : 's'}`}
        error={
          invalidCount > 0 &&
          `${invalidCount === 1 ? '1 field holds' : `${invalidCount} fields hold`} a value that cannot be used: correct ${
            invalidCount === 1 ? 'it' : 'them'
          } before checking or saving.`
        }
        saveLabel="Review and save"
        saveDisabled={leaves.length === 0 || invalidCount > 0}
        onSave={() => {
          save.reset()
          setReviewing(true)
          preview.mutate(draft)
        }}
        onDiscard={() => replaceDraft(doc.config)}
      >
        <Button
          variant="outline"
          size="sm"
          disabled={invalidCount > 0}
          isLoading={preview.isPending && !reviewing}
          onClick={() => preview.mutate(draft)}
        >
          Check
        </Button>
      </SaveBar>

      <RouterLeaveGuard
        when={dirty && !disabled}
        within="/config"
        allowed={isSignIn}
        message={`${
          changeCount === 0
            ? 'What was typed in this form will be lost.'
            : `${changeCount} unsaved change${changeCount === 1 ? '' : 's'} to the configuration will be lost.`
        } Nothing of it has been saved.`}
        stayLabel="Stay on this page"
        leaveLabel="Leave and discard"
      />

      <ConfirmLiveAction
        open={reviewing}
        title="Save the configuration"
        confirmLabel="Slide to save"
        doneLabel="Saved"
        warning="Each service named applies it at its next start, a restart after a crash included."
        pending={save.isPending}
        blocked={!preview.isSuccess}
        error={
          conflict
            ? 'The file changed on the server since this page loaded it. Close this dialog and reload the page: your changes will have to be made again.'
            : (save.error?.message ?? (preview.isError ? preview.error.message : null))
        }
        onConfirm={() => save.mutate()}
        onClose={() => setReviewing(false)}
        description={
          preview.isPending ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader size={16} /> Checking the change as a save would…
            </p>
          ) : preview.isSuccess ? (
            <div className="space-y-3">
              <p className="text-sm">
                The current file is kept as a backup beside it, and the new one was checked with the services&apos; own
                validators.
              </p>
              <ReviewChanges catalog={catalog} preview={preview.data} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">This configuration cannot be saved as it is.</p>
          )
        }
      />
    </ConfigEditorContext.Provider>
  )
}

/** The Configuration page: every view of config.json (src/features/config/ConfigViews.tsx) over one draft. */
export function ConfigPage() {
  // Kept here, not in the editor: a save gives the editor a new revision, which starts it afresh.
  const [lastSaved, setLastSaved] = useState<ConfigSaved | null>(null)
  const catalogQuery = useQuery({
    queryKey: ['config-catalog'],
    queryFn: api.configCatalog,
    // Fixed for a version of WATA.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: api.config,
    // The editor holds a draft of this file: it must not change under it.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const historyQuery = useQuery({ queryKey: ['config-history'], queryFn: api.configHistory, refetchOnWindowFocus: false })
  const catalog = catalogQuery.data
  const doc = configQuery.data

  return (
    <>
      {!(catalog && doc) && <PageHeader title="Configuration" description="Every parameter of config.json." />}
      <QueryState
        isPending={catalogQuery.isPending || configQuery.isPending}
        error={catalogQuery.error ?? configQuery.error}
        hasData={!!catalog && !!doc}
      >
        {catalog && doc && (
          <ConfigEditor
            key={doc.revision}
            catalog={catalog}
            doc={doc}
            history={historyQuery.data?.items}
            lastSaved={lastSaved}
            onSaved={setLastSaved}
            dismissSaved={() => setLastSaved(null)}
          />
        )}
      </QueryState>
    </>
  )
}
