import { Badge } from 'ioiti-ui'
import { AlertTriangle } from 'lucide-react'
import { Link } from 'react-router'

import type { CatalogChange, ConfigCatalog, ConfigImpact, ConfigPreview } from '@/lib/types'

import { hrefOf, lookup, shownValue } from './catalog'
import { serviceNames } from './editor-context'

function changeSaid(change: CatalogChange): string {
  if (change.added) return `set to ${shownValue(change.after)}`
  if (change.removed) return `taken out (was ${shownValue(change.before)})`
  return `${shownValue(change.before)} → ${shownValue(change.after)}`
}

/** Where a change applies: the services that read it at their start, and what else reads it. */
export function ImpactSaid({ catalog, impact }: { catalog: ConfigCatalog; impact: ConfigImpact }) {
  const names = serviceNames(catalog)
  const consumers = new Map(catalog.consumers.map((consumer) => [consumer.id, consumer]))
  return (
    <div className="space-y-1.5 text-xs">
      {impact.services.length > 0 ? (
        <p>
          <span className="font-medium">Applies at the next start of:</span>{' '}
          {impact.services.map((service) => names.get(service) ?? service).join(', ')}.
        </p>
      ) : (
        <p className="text-muted-foreground">No service reads what changed: nothing needs to start again.</p>
      )}
      {impact.consumers.length > 0 && (
        <p className="text-text-secondary">
          Also read by:{' '}
          {impact.consumers
            .map((id) => consumers.get(id))
            .filter((consumer) => consumer !== undefined)
            .map((consumer) => (consumer.live ? consumer.name : `${consumer.name} (once the admin starts again)`))
            .join('; ')}
          .
        </p>
      )}
    </div>
  )
}

/**
 * What saving would change, as the server checked it: each parameter with its
 * old and new value, grouped as the page groups them, the trading ones said;
 * where it applies; and what works against what.
 */
export function ReviewChanges({ catalog, preview }: { catalog: ConfigCatalog; preview: ConfigPreview }) {
  const groups = new Map(catalog.groups.map((group) => [group.id, group.title]))
  const byGroup = new Map<string, CatalogChange[]>()
  const unknown: CatalogChange[] = []
  for (const change of preview.changes) {
    const param = lookup(catalog, change.path)
    if (!param) {
      unknown.push(change)
      continue
    }
    byGroup.set(param.group, [...(byGroup.get(param.group) ?? []), change])
  }

  return (
    <div className="space-y-3">
      {preview.warnings.length > 0 && (
        <ul className="space-y-1.5 rounded-lg border border-warning/40 bg-warning/5 p-3 text-xs">
          {preview.warnings.map((warning) => (
            <li key={`${warning.path}-${warning.message}`} className="flex gap-1.5">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <span>{warning.message}</span>
            </li>
          ))}
        </ul>
      )}
      <ImpactSaid catalog={catalog} impact={preview.impact} />
      <div className="max-h-72 space-y-3 overflow-y-auto rounded-lg border border-border bg-surface-raised/40 p-3 text-xs">
        {[...byGroup.entries()].map(([group, changes]) => (
          <section key={group}>
            <p className="font-medium text-foreground">{groups.get(group) ?? group}</p>
            <ul className="mt-1 space-y-1.5">
              {changes.map((change) => {
                const param = lookup(catalog, change.path)
                return (
                  <li key={change.path}>
                    <span className="flex flex-wrap items-center gap-1.5">
                      {param ? (
                        <Link to={hrefOf(param)} className="text-foreground underline-offset-2 hover:underline">
                          {param.label}
                        </Link>
                      ) : (
                        change.path
                      )}
                      {param?.risk === 'trading' && (
                        <Badge variant="outline" tone="warning">
                          Trading
                        </Badge>
                      )}
                    </span>
                    <span className="block break-all font-mono text-muted-foreground">{changeSaid(change)}</span>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
        {unknown.length > 0 && (
          <section>
            <p className="font-medium text-foreground">Keys WATA does not know (nothing reads them)</p>
            <ul className="mt-1 space-y-1">
              {unknown.map((change) => (
                <li key={change.path} className="break-all font-mono text-muted-foreground">
                  {change.path}: {changeSaid(change)}
                </li>
              ))}
            </ul>
          </section>
        )}
        {preview.changes.length === 0 && <p className="text-muted-foreground">Nothing changes.</p>}
      </div>
    </div>
  )
}
