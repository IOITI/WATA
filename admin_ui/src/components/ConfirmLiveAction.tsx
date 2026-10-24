import type { ReactNode } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { DragToConfirm } from '@/components/ui/drag-to-confirm'
import { SettleModal } from '@/components/ui/settle-modal'

interface ConfirmLiveActionProps {
  open: boolean
  title: string
  /** Exactly what will happen, in the operator's terms. */
  description: ReactNode
  /** Text on the slider, e.g. "Slide to send". */
  confirmLabel: string
  /** What is at stake. Defaults to the reminder that the account is live. */
  warning?: ReactNode
  /**
   * How it is confirmed. The slide is for what puts money at risk or cannot be
   * taken back. A plain button is for the protective direction (pausing), which
   * has to be quick: `confirmLabel` is then the text of that button.
   */
  gesture?: 'slide' | 'button'
  /** Shown on the slider once it is done ("Saved", "Sent"...). */
  doneLabel?: string
  pending?: boolean
  /** The gesture waits for something the description asks first (a box ticked). */
  blocked?: boolean
  error?: string | null
  onConfirm: () => void
  onClose: () => void
}

/**
 * The last step before anything that acts on the live account. The operator
 * reads what is about to happen, then has to drag the slider to the end (with
 * a keyboard: focus it and press Enter): a deliberate gesture, not a button
 * under the cursor.
 */
export function ConfirmLiveAction({
  open,
  title,
  description,
  confirmLabel,
  warning = 'This acts on the live trading account.',
  gesture = 'slide',
  doneLabel = 'Done',
  pending = false,
  blocked = false,
  error,
  onConfirm,
  onClose,
}: ConfirmLiveActionProps) {
  return (
    <SettleModal
      open={open}
      onClose={pending ? () => {} : onClose}
      title={title}
      closeOnBackdrop={!pending}
      hideCloseButton={pending}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          {gesture === 'button' && (
            <Button size="sm" isLoading={pending} disabled={blocked} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <Alert tone="warning">
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
        <div className="text-sm leading-relaxed text-text-secondary">{description}</div>
        {error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {gesture === 'slide' && (
          <DragToConfirm
            // A fresh slider every time the dialog opens or an attempt failed.
            key={`${open}-${error ?? ''}`}
            label={confirmLabel}
            confirmedLabel={pending ? 'Working…' : doneLabel}
            variant="warning"
            autoResetDelay={0}
            haptic={false}
            disabled={pending || blocked}
            onConfirm={onConfirm}
          />
        )}
      </div>
    </SettleModal>
  )
}
