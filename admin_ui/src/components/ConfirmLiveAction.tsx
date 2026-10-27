import { ConfirmAction } from 'ioiti-ui'
import type { ComponentProps } from 'react'

type ConfirmLiveActionProps = ComponentProps<typeof ConfirmAction>

/**
 * The last step before anything that acts on the live account: ioiti-ui's ConfirmAction, which
 * says first that the account is live unless told what else is at stake (`warning`). The operator
 * reads what is about to happen, then has to drag the slider to the end (with a keyboard: focus
 * it and press Enter): a deliberate gesture, not a button under the cursor.
 */
export function ConfirmLiveAction({ warning = 'This acts on the live trading account.', ...props }: ConfirmLiveActionProps) {
  return <ConfirmAction warning={warning} {...props} />
}
