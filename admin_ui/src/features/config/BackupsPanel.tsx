import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { ConfigDocument } from '@/lib/types'
import { cn } from '@/lib/utils'

import { ChangeList } from './ChangeList'
import { restoreChanges } from './configModel'

interface BackupsPanelProps {
  configDoc: ConfigDocument
  timeZone: string
}

/**
 * The versions of config.json kept by each save: look at what one would change,
 * and put its trading parameters back. Like a save, a restore leaves the rest
 * of the file (credentials, infrastructure, the admin's own settings) alone.
 */
export function BackupsPanel({ configDoc, timeZone }: BackupsPanelProps) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const backupsQuery = useQuery({ queryKey: ['config-backups'], queryFn: api.configBackups })
  const backupQuery = useQuery({
    queryKey: ['config-backup', selected],
    queryFn: () => api.configBackup(selected ?? ''),
    enabled: selected !== null,
  })
  const backups = backupsQuery.data?.items
  const backup = backupQuery.data

  // From the file as it is now to the backup: what restoring would change.
  const changes = useMemo(
    () =>
      backup
        ? restoreChanges(configDoc.config, backup.config, configDoc.editable_paths, configDoc.protected_paths)
        : [],
    [configDoc.config, configDoc.editable_paths, configDoc.protected_paths, backup],
  )

  const restoreMutation = useMutation({
    mutationFn: () => api.restoreConfigBackup(selected ?? '', configDoc.revision),
    onSuccess: async (restored) => {
      setConfirming(false)
      setSelected(null)
      toast({
        title: 'Trading parameters restored',
        description: `The version it replaced is kept as ${restored.backup}.`,
        variant: 'success',
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['config'] }),
        queryClient.invalidateQueries({ queryKey: ['config-backups'] }),
        queryClient.invalidateQueries({ queryKey: ['session'] }),
      ])
    },
  })
  const conflict = restoreMutation.error instanceof ApiError && restoreMutation.error.status === 409

  return (
    <Panel
      title="Previous versions"
      description="Each save keeps the file it replaced. The twenty most recent are kept. Restoring one puts its trading parameters back, nothing else."
    >
      <QueryState isPending={backupsQuery.isPending} error={backupsQuery.error} hasData={!!backups}>
        {backups && backups.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nothing yet: the first version is kept the first time the configuration is saved from here.
          </p>
        )}
        {backups && backups.length > 0 && (
          <div className="grid gap-5 lg:grid-cols-[18rem_1fr]">
            <ul className="max-h-[28rem] space-y-1 overflow-y-auto">
              {backups.map((entry) => (
                <li key={entry.name}>
                  <button
                    type="button"
                    aria-pressed={entry.name === selected}
                    onClick={() => {
                      restoreMutation.reset()
                      setSelected(entry.name)
                    }}
                    className={cn(
                      'w-full rounded-lg border px-3 py-2 text-left text-xs transition-colors focus-ring',
                      entry.name === selected
                        ? 'border-border-hover bg-surface-hover text-foreground'
                        : 'border-transparent text-muted-foreground hover:bg-surface-hover hover:text-foreground',
                    )}
                  >
                    <span className="block font-medium">Replaced {formatDateTime(entry.created_at, timeZone)}</span>
                    <span className="block text-xs">{formatNumber(entry.size)} bytes</span>
                  </button>
                </li>
              ))}
            </ul>

            <div className="min-w-0 space-y-3">
              {selected === null && (
                <p className="text-sm text-muted-foreground">Pick a version to see what restoring it would change.</p>
              )}
              {selected !== null && (
                <QueryState isPending={backupQuery.isPending} error={backupQuery.error} hasData={!!backup}>
                  {backup && changes.length === 0 && (
                    <p className="text-sm text-muted-foreground">
                      The trading parameters of this version are the same as the current ones: restoring it would
                      change nothing.
                    </p>
                  )}
                  {backup && changes.length > 0 && (
                    <>
                      <p className="text-sm text-text-secondary">
                        Restoring this version would make {changes.length} change{changes.length === 1 ? '' : 's'}{' '}
                        (current value → restored value):
                      </p>
                      <ChangeList changes={changes} />
                    </>
                  )}
                  {backup && configDoc.editable && (
                    <Button
                      size="sm"
                      onClick={() => {
                        restoreMutation.reset()
                        setConfirming(true)
                      }}
                    >
                      Restore this version
                    </Button>
                  )}
                  {backup && !configDoc.editable && (
                    <Alert tone="neutral">
                      <AlertDescription>
                        Editing is switched off in the configuration (<code>admin.config_edit_enabled</code>).
                      </AlertDescription>
                    </Alert>
                  )}
                </QueryState>
              )}
            </div>
          </div>
        )}
      </QueryState>

      <ConfirmLiveAction
        open={confirming}
        title="Restore this version"
        confirmLabel="Slide to restore"
        doneLabel="Restored"
        warning="Each service applies the file the next time it starts, including when it restarts by itself after a crash."
        pending={restoreMutation.isPending}
        error={
          conflict
            ? 'The file changed on the server since this page loaded it. Close this dialog and reload the page.'
            : restoreMutation.error?.message
        }
        onConfirm={() => restoreMutation.mutate()}
        onClose={() => setConfirming(false)}
        description={
          <>
            Only the trading parameters of this version are put back. Credentials, infrastructure settings and the
            admin&apos;s own settings stay as they are on the server now, whatever this version held for them. The
            current file is kept as a new backup, and the result is checked with the services&apos; validator first.
          </>
        }
      />
    </Panel>
  )
}
