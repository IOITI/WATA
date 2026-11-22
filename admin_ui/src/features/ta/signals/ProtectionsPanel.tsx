// A signal rule's protections (src/admin/live_safety.py, GET /api/ta/rules/<id>/protections): what keeps the account
// safe while the rule trades live, each leading to where it is set, and what the account buys. On the rule's page, and
// in the go-live dialog, where its links open in a new tab so that the dialog stays. Nothing here changes a setting.

import { CheckList, KpiStrip, LinkProvider, Panel, QueryState } from 'ioiti-ui'
import type { AnchorHTMLAttributes } from 'react'

import type { Protection, TaRule } from '@/lib/types'

import { fundsFigures, protectionHref, protectionsLine, toSeeTo } from './protections'
import { useRuleProtections } from './useRuleProtections'

/** A link that opens in a new tab: the dialog it is drawn in stays where it is. */
function NewTabLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <a {...props} target="_blank" rel="noreferrer" />
}

/** The protections as a list, each leading where it is set; in a new tab when ``newTab``. */
export function ProtectionList({ checks, newTab = false }: { checks: Protection[]; newTab?: boolean }) {
  const list = <CheckList checks={checks} label="WATA’s protections" hrefOf={protectionHref} />
  return newTab ? <LinkProvider component={NewTabLink}>{list}</LinkProvider> : list
}

/** The rule page's section. */
export function ProtectionsPanel({ rule, className }: { rule: TaRule; className?: string }) {
  const query = useRuleProtections(rule.id)
  const checks = query.data?.checks ?? []
  const figures = fundsFigures(query.data?.funds)
  return (
    <Panel
      id="protections"
      className={className}
      title="Protections"
      description={
        rule.mode === 'live'
          ? 'What keeps the account safe while the rule trades live, each leading to where it is set; and what the account buys of the turbos the rule bought in its backtest or on paper.'
          : 'What would keep the account safe once the rule trades live, each leading to where it is set; and what the account buys of the turbos the rule bought in its backtest or on paper.'
      }
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={query.data !== undefined}>
        <div className="space-y-4">
          <p className="text-sm">
            {protectionsLine(checks)}.{toSeeTo(checks) ? ` ${toSeeTo(checks)}` : ''}
          </p>
          {figures.length > 0 && <KpiStrip items={figures.map((figure) => ({ label: figure.label, value: figure.value, hint: figure.hint }))} />}
          <ProtectionList checks={checks} />
          <p className="text-xs text-muted-foreground">
            WATA’s protections are the trader’s, for every rule and signal: each is set where it leads, and most apply at
            the services’ next start, as the Configuration page says. Nothing here holds the rule back from going live.
          </p>
        </div>
      </QueryState>
    </Panel>
  )
}
