// A configuration's nightly re-run switched on or off (src/admin/backtest_nightly.py): its first day, the days it
// covers tonight, what replaces what, and whether its result is said on Telegram. Not audited: nothing here trades.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Hint, Input, Dialog } from 'ioiti-ui'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { BacktestConfiguration } from '@/lib/types'

import { windowSentence } from './configurations'

/** The first day of a window of ``most`` days ending on ``to``, and never before ``from``. */
function windowStart(from: string, to: string, most: number): string {
  const end = new Date(`${to}T12:00:00Z`)
  end.setUTCDate(end.getUTCDate() - (most - 1))
  const earliest = end.toISOString().slice(0, 10)
  return from > earliest ? from : earliest
}

export function NightlyDialog({
  item,
  open,
  telegram,
  onClose,
}: {
  item: BacktestConfiguration
  open: boolean
  /** Whether a Telegram bot is set; null when it cannot be told. */
  telegram: boolean | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const nightly = item.nightly
  const [on, setOn] = useState(nightly?.on ?? false)
  const [first, setFirst] = useState(nightly?.from ?? item.latest?.from ?? '')
  const [tell, setTell] = useState(nightly?.telegram ?? false)
  const save = useMutation({
    mutationFn: () => api.changeBacktestConfiguration(item.id, { nightly: { on, from: first || undefined, telegram: tell } }),
    onSuccess: (page) => {
      queryClient.setQueryData(['backtests', 'configurations', item.id], page)
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      onClose()
    },
  })
  const most = nightly?.most_days ?? 365
  const to = nightly?.next.to ?? item.latest?.to ?? first
  const tellHint = telegram === false ? 'No Telegram bot is set: set it on the Credentials page first' : null
  const tellBox = (
    <Checkbox
      label="Tell me on Telegram once each nightly run ends"
      checked={tell}
      disabled={telegram === false}
      onChange={(event) => setTell(event.target.checked)}
    />
  )

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Make it again each evening"
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" pending={save.isPending} disabled={!first} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 text-sm">
        <Checkbox
          label="Make it again after each trading day"
          checked={on}
          onChange={(event) => setOn(event.target.checked)}
        />
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">From</span>
          <Input type="date" value={first} max={to} onChange={(event) => setFirst(event.target.value)} aria-label="Its first day" />
        </label>
        {first && to && (
          <p className="text-text-secondary">
            Its next run: {windowSentence(windowStart(first, to, most), to, most)}.
          </p>
        )}
        <p className="leading-relaxed text-text-secondary">
          Once the market is closed, its latest run is asked again with the same settings, script and inputs, its days
          stretched to the day just over; the signals WATA received, or a rule’s, are read again for the new days. Once
          it is done, the nightly run before it is removed, unless it is a favourite, a reference, stressed or checked,
          or carries the AI agent’s verdict. Runs you asked for yourself are never removed. What each evening’s run
          found stays in its history.
        </p>
        {most < 365 && (
          <p className="text-warning">
            Held to {most} days by “Days of a backtest at most” (admin.backtests_max_period_days): raise it to 365 on the
            Configuration page for a year of days.
          </p>
        )}
        {tellHint ? (
          <Hint content={tellHint} wrap>
            {tellBox}
          </Hint>
        ) : (
          tellBox
        )}
        {save.error && (
          <p role="alert" className="text-negative">
            {save.error.message}
          </p>
        )}
      </div>
    </Dialog>
  )
}
