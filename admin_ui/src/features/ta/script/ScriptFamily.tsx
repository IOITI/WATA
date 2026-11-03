// A script's family, in its page's header: what the Indicators list is narrowed by. Chosen among those in use and
// LuxAlgo's Library's (while its tools are on), or a name of the operator's own; a script taken from LuxAlgo comes
// with its family.

import { useQuery } from '@tanstack/react-query'
import { Button, Hint, Input, SettleModal } from 'ioiti-ui'
import { Tag } from 'lucide-react'
import { useState } from 'react'

import { useLuxAlgoFamilies, useLuxAlgoStatus } from '@/features/ta/luxalgo/useLuxAlgo'
import { api } from '@/lib/api'
import type { TaScriptSummary } from '@/lib/types'

import { familiesOffered, MAX_FAMILY } from '../indicatorsModel'
import { useScriptFamily } from '../useScriptDetails'

export function ScriptFamily({ script }: { script: Pick<TaScriptSummary, 'id' | 'name' | 'family'> }) {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const luxalgo = !!useLuxAlgoStatus().data?.enabled
  const families = useLuxAlgoFamilies(open && luxalgo)
  const scripts = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled: open })
  const offered = familiesOffered(scripts.data?.items ?? [], families.data?.families ?? [])
  const change = useScriptFamily()
  const chosen = typed.trim()

  const set = (family: string | null) =>
    change.mutate({ id: script.id, family }, { onSuccess: () => setOpen(false) })

  return (
    <>
      <Hint content="What the Indicators list is narrowed by">
        <button
          type="button"
          onClick={() => {
            setTyped(script.family ?? '')
            change.reset()
            setOpen(true)
          }}
          className="inline-flex items-center gap-1 rounded-md px-1 text-xs underline-offset-4 hover:underline focus-ring"
        >
          <Tag className="size-3" aria-hidden />
          {script.family ? `Family: ${script.family}` : 'No family yet'}
        </button>
      </Hint>
      <SettleModal
        open={open}
        onClose={() => setOpen(false)}
        title={`The family of ${script.name}`}
        size="sm"
        footer={
          <>
            {script.family && (
              <Button variant="ghost" size="sm" isLoading={change.isPending && change.variables?.family === null} onClick={() => set(null)}>
                Remove it
              </Button>
            )}
            <Button size="sm" isLoading={change.isPending && change.variables?.family !== null}
              disabled={!chosen || chosen === script.family} onClick={() => set(chosen)}>
              Keep it
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">Its family</span>
            <Input value={typed} maxLength={MAX_FAMILY} placeholder="Trend, Momentum, Breakouts…" autoFocus
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && chosen && chosen !== script.family) set(chosen)
              }} />
          </label>
          {offered.length > 0 && (
            <div role="group" aria-label="Families offered" className="flex flex-wrap gap-1.5">
              {offered.map((family) => (
                <button
                  key={family}
                  type="button"
                  aria-pressed={family.toLocaleLowerCase() === chosen.toLocaleLowerCase()}
                  onClick={() => setTyped(family)}
                  className="rounded-full border border-border px-2.5 py-1 text-xs hover:bg-surface-hover focus-ring aria-pressed:border-brand aria-pressed:text-brand"
                >
                  {family}
                </button>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            {luxalgo ? 'Those in use first, then the families of LuxAlgo’s Library.' : 'Those in use, or a name of your own.'}
          </p>
        </div>
      </SettleModal>
    </>
  )
}
