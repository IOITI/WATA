// A concept of LuxAlgo's Library: its write-up, and the way to the Library's indicators of it.

import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, ButtonLink, PageHeader, Panel, QueryState } from 'ioiti-ui'
import { ExternalLink } from 'lucide-react'
import { useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { api, isNotFound } from '@/lib/api'

import { LuxAlgoWriteUp } from './LuxAlgoWriteUp'
import { conceptIndicatorsPath, LUXALGO_PATH } from './luxalgoModel'
import { familyName, useLuxAlgoFamilies, useLuxAlgoStatus } from './useLuxAlgo'

export function LuxAlgoConceptPage() {
  const { slug = '' } = useParams()
  const status = useLuxAlgoStatus()
  const enabled = !!status.data?.enabled
  const families = useLuxAlgoFamilies(enabled)
  const concept = useQuery({ queryKey: ['luxalgo', 'concept', slug], queryFn: () => api.luxalgoConcept(slug), enabled })

  if (isNotFound(concept.error)) {
    return (
      <NotFoundState
        title="No such concept"
        description="LuxAlgo's Library has no concept at this address."
        suggestions={[{ label: 'LuxAlgo Library', href: LUXALGO_PATH, description: "LuxAlgo's indicators and concepts" }]}
      />
    )
  }
  const found = concept.data
  return (
    <div className="space-y-6">
      <PageHeader
        title={found?.name ?? 'Concept'}
        description={
          found?.family
            ? `A concept of ${familyName(families.data?.families, found.family)}${found.cluster ? ` (${found.cluster})` : ''}.`
            : undefined
        }
        actions={found && (
          <ButtonLink to={conceptIndicatorsPath(found.slug)} variant="outline" size="sm">
            Its indicators
          </ButtonLink>
        )}
      />
      <QueryState isPending={status.isPending || (enabled && concept.isPending)} error={status.error ?? concept.error} hasData={!!found || !enabled}>
        {!enabled && (
          <Alert tone="neutral">
            <AlertDescription>
              LuxAlgo&apos;s tools are switched off (<code>admin.luxalgo_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
        {found && (
          <div className="space-y-6">
            {found.url && (
              <a href={found.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-sm underline underline-offset-4">
                On LuxAlgo&apos;s site <ExternalLink className="size-3.5" aria-hidden />
              </a>
            )}
            {found.content_markdown && (
              <Panel title="What it is">
                <LuxAlgoWriteUp markdown={found.content_markdown} />
              </Panel>
            )}
          </div>
        )}
      </QueryState>
    </div>
  )
}
