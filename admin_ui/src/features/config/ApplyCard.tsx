import { Alert, AlertDescription, AlertTitle, Button } from 'ioiti-ui'
import { Check, Copy, X } from 'lucide-react'
import { useState } from 'react'

import { RestartAction } from '@/features/system/RestartViews'
import type { ConfigCatalog, ConfigSaved } from '@/lib/types'

import { restartCommand } from './catalog'
import { ImpactSaid } from './ReviewChanges'

/**
 * What a save did and where it applies, until dismissed: the file is saved,
 * and each service named reads it at its next start, which can be asked here
 * (through the control service) or on the server.
 */
export function ApplyCard({ catalog, saved, onDismiss }: { catalog: ConfigCatalog; saved: ConfigSaved; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)
  const services = saved.impact?.services ?? []
  const command = restartCommand(catalog, services)
  const changed = saved.changes?.length ?? 0

  return (
    <Alert tone={services.length ? 'warning' : 'success'}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-2">
          <AlertTitle>
            Saved: {changed} change{changed === 1 ? '' : 's'}
          </AlertTitle>
          <AlertDescription>
            <div className="space-y-2">
              <p>The previous version is kept as {saved.backup}.</p>
              {saved.impact && <ImpactSaid catalog={catalog} impact={saved.impact} />}
              {services.length > 0 && <RestartAction ids={services} />}
              {command && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted-foreground">Or on the server:</span>
                  <code className="break-all rounded-md border border-border bg-surface-raised/60 px-2 py-1 text-xs">{command}</code>
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                    onClick={() => {
                      void navigator.clipboard?.writeText(command).then(() => setCopied(true))
                    }}
                  >
                    {copied ? 'Copied' : 'Copy'}
                  </Button>
                </div>
              )}
            </div>
          </AlertDescription>
        </div>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={onDismiss}
          className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-surface-hover focus-ring"
        >
          <X className="size-3.5" />
        </button>
      </div>
    </Alert>
  )
}
