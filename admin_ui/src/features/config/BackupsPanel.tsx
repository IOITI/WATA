import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { RecoveryLedger } from '@/components/ui/recovery-ledger'
import type { LedgerEntry } from '@/components/ui/recovery-ledger'
import { Skeleton } from '@/components/ui/skeleton'
import { api, ApiError } from '@/lib/api'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { ConfigDocument } from '@/lib/types'

import { ChangeList } from './ChangeList'
import { restoreChanges } from './configModel'

/** The ledger's first entry: the file the services read, which is never restored. */
const IN_USE = 'in-use'

interface BackupsPanelProps {
  configDoc: ConfigDocument
  timeZone: string
}

/**
 * The versions of config.json kept by each save, as a timeline under the file
 * in use: open one to see what it would change, and put its trading parameters
 * back. Like a save, a restore leaves the rest of the file (credentials,
 * infrastructure, the admin's own settings) alone.
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
  const chosen = backups?.find((entry) => entry.name === selected)

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
  const changeCount = `${changes.length} change${changes.length === 1 ? '' : 's'}`

  // What the version opened shows under its entry.
  const details = (
    <div className="space-y-3">
      <QueryState
        isPending={backupQuery.isPending}
        error={backupQuery.error}
        hasData={!!backup}
        skeleton={<Skeleton className="h-16 w-full" />}
      >
        {backup && changes.length === 0 && (
          <p className="text-sm text-muted-foreground">
            The trading parameters of this version are the same as the current ones: restoring it would change
            nothing.
          </p>
        )}
        {backup && changes.length > 0 && (
          <>
            <p className="text-sm text-text-secondary">
              Restoring this version would make {changeCount} (current value → restored value):
            </p>
            <ChangeList changes={changes} />
          </>
        )}
        {backup && !configDoc.editable && (
          <Alert tone="neutral">
            <AlertDescription>
              Editing is switched off in the configuration (<code>admin.config_edit_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
      </QueryState>
    </div>
  )

  const entries: LedgerEntry[] = [
    {
      id: IN_USE,
      action: `Written ${formatDateTime(configDoc.modified_at, timeZone)}`,
      description: 'The file each service reads when it starts.',
    },
    ...(backups ?? []).map((entry) => ({
      id: entry.name,
      action: `Replaced ${formatDateTime(entry.created_at, timeZone)}`,
      description: `${formatNumber(entry.size)} bytes`,
      reference: entry.name,
      details: { content: entry.name === selected ? details : null },
      // Once it is open and read, so that what it would change is in front of the operator.
      restorable: configDoc.editable && entry.name === selected && backup !== undefined,
    })),
  ]

  return (
    <Panel
      title="Previous versions"
      description="Each save keeps the file it replaced. The twenty most recent are kept. Open one to see what restoring it would change: only its trading parameters come back, nothing else."
    >
      <QueryState isPending={backupsQuery.isPending} error={backupsQuery.error} hasData={!!backups}>
        {backups && backups.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nothing yet: the first version is kept the first time the configuration is saved from here.
          </p>
        )}
        {backups && backups.length > 0 && (
          <RecoveryLedger
            entries={entries}
            currentEntryId={IN_USE}
            currentLabel="In use"
            title="Versions"
            countLabel={`${backups.length} kept`}
            expandedId={selected}
            onExpandedChange={(name) => {
              restoreMutation.reset()
              setSelected(name)
            }}
            restoreLabel="Restore"
            onRestore={() => {
              restoreMutation.reset()
              setConfirming(true)
            }}
          />
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
            {chosen && (
              <>
                Restoring the version replaced {formatDateTime(chosen.created_at, timeZone)}{' '}
                {changes.length === 0 ? 'changes nothing' : `makes ${changeCount}`}.{' '}
              </>
            )}
            Only the trading parameters of this version are put back. Credentials, infrastructure settings and the
            admin&apos;s own settings stay as they are on the server now, whatever this version held for them. The
            current file is kept as a new backup, and the result is checked with the services&apos; validator first.
          </>
        }
      />
    </Panel>
  )
}
