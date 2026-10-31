// A rule's deletion, asked first: the Signal rules page's and a rule's own page's (useRuleActions' `deleting`).

import { Alert, AlertDescription, Button, SettleModal } from 'ioiti-ui'

import type { TaRule } from '@/lib/types'

import { conflictText } from './ruleModel'

export function DeleteRuleDialog({ rule, pending, error, onCancel, onDelete }: {
  /** The rule asked to be deleted; none: the dialog is closed. */
  rule: TaRule | null
  pending: boolean
  error: Error | null
  onCancel: () => void
  onDelete: (rule: TaRule) => void
}) {
  return (
    <SettleModal
      open={rule !== null}
      onClose={onCancel}
      title={`Delete ${rule?.name ?? ''}`}
      size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button variant="destructive" size="sm" isLoading={pending} onClick={() => rule && onDelete(rule)}>
            Delete
          </Button>
        </>
      }
    >
      <p className="text-sm text-text-secondary">
        The engine stops running it. Its signals stay in the journal; the script stays on the Indicators page.
      </p>
      {error && (
        <Alert tone="danger" role="alert" className="mt-3">
          <AlertDescription>{conflictText(error)}</AlertDescription>
        </Alert>
      )}
    </SettleModal>
  )
}
