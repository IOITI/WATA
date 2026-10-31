// An indicator of LuxAlgo's Library: its write-up, its source as LuxAlgo gives it (its first lines alone signed out)
// with the licence its header says, and its taking as one of the operator's scripts, whole, its origin kept.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AIResponse,
  AIResponseContent,
  Alert,
  AlertDescription,
  Badge,
  Button,
  CodeBlock,
  FormControl,
  FormDescription,
  FormItem,
  FormLabel,
  Hint,
  Input,
  PageHeader,
  Panel,
  QueryState,
  SettleModal,
  useToast,
} from 'ioiti-ui'
import { ExternalLink, FileDown } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict } from '@/features/agent/AgentVerdict'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { api, isNotFound } from '@/lib/api'
import type { LuxAlgoIndicator, LuxAlgoSource } from '@/lib/types'

import { InWataPanel } from './InWataPanel'
import { familyName, useLuxAlgoFamilies, useLuxAlgoStatus } from './useLuxAlgo'
import { canTake, conceptPath, licenceTerms, LUXALGO_PATH, shownMarkdown, sourceSaid } from './luxalgoModel'

const PARENTS = [{ label: 'LuxAlgo Library', href: LUXALGO_PATH }]
const OUT = 'inline-flex items-center gap-1 text-sm text-foreground underline underline-offset-4'

function TakeDialog({ indicator, source, onClose }: { indicator: LuxAlgoIndicator; source: LuxAlgoSource; onClose: () => void }) {
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [name, setName] = useState(indicator.name)
  const terms = licenceTerms(source.licence)
  const take = useMutation({
    mutationFn: () => api.takeLuxalgoIndicator(indicator.slug, name.trim()),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['ta', 'scripts'] })
      void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'indicator', indicator.slug] })
      toast({ title: 'Taken as your script', description: `${made.name}, version ${made.version}` })
      void navigate(`/ta/indicators/${made.id}`)
    },
  })
  return (
    <SettleModal
      open
      onClose={onClose}
      title={`Take ${indicator.name} as your script`}
      description="Its whole source becomes one of your scripts, its header kept: test it, map its alerts and change it as any other."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!name.trim()} isLoading={take.isPending} onClick={() => take.mutate()}>
            Take it
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FormItem>
          <FormLabel htmlFor="luxalgo-name">Name of your script</FormLabel>
          <FormControl>
            <Input id="luxalgo-name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
          </FormControl>
          <FormDescription>A name of yours not taken yet.</FormDescription>
        </FormItem>
        <Alert tone="neutral">
          <AlertDescription>
            LuxAlgo publishes it under {source.licence ?? 'its own licence'}
            {terms ? `: ${terms}` : ''}. Its terms are yours to check before trading it; its page on LuxAlgo&apos;s site says them.
          </AlertDescription>
        </Alert>
        {take.isError && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{take.error.message}</AlertDescription>
          </Alert>
        )}
      </div>
    </SettleModal>
  )
}

export function LuxAlgoIndicatorPage() {
  const { slug = '' } = useParams()
  const status = useLuxAlgoStatus()
  const enabled = !!status.data?.enabled
  const families = useLuxAlgoFamilies(enabled)
  const indicator = useQuery({ queryKey: ['luxalgo', 'indicator', slug], queryFn: () => api.luxalgoIndicator(slug), enabled })
  const queryClient = useQueryClient()
  const source = useQuery({
    // Read again once signed in: signed out, it was cut.
    queryKey: ['luxalgo', 'source', slug, !!status.data?.signed_in],
    queryFn: async () => {
      const found = await api.luxalgoSource(slug)
      // A source read whole is queued for its check: the checks are read again, and followed while it waits.
      if (found.complete) void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'compat'] })
      return found
    },
    enabled: enabled && !!indicator.data,
  })
  const [taking, setTaking] = useState(false)

  if (isNotFound(indicator.error)) {
    return (
      <NotFoundState
        title="No such indicator"
        description="LuxAlgo's Library has no indicator at this address."
        suggestions={[{ label: 'LuxAlgo Library', href: LUXALGO_PATH, description: "LuxAlgo's indicators and concepts" }]}
      />
    )
  }
  const found = indicator.data
  const cut = source.data ? sourceSaid(source.data) : null
  const takeable = canTake(source.data)
  const takeButton = (
    <Button leftIcon={<FileDown className="size-4" aria-hidden />} disabled={!takeable} onClick={() => setTaking(true)}>
      Take as my script
    </Button>
  )
  return (
    <div className="space-y-6">
      <PageHeader
        title={found?.name ?? 'Indicator'}
        parents={PARENTS}
        description={found ? `${familyName(families.data?.families, found.family)}${found.date_displayed ? `, ${found.date_displayed}` : ''}, by ${found.author ?? 'LuxAlgo'}.` : undefined}
        actions={found && (
          <>
            <AskAgentLink subject={{ kind: 'luxalgo', id: slug }} label="Ask the AI agent about it" />
            {takeable ? takeButton : <Hint content={cut ?? 'Its source is being read'} wrap>{takeButton}</Hint>}
          </>
        )}
      />
      {found && <AgentVerdict about={`luxalgo:${slug}`} />}
      <QueryState isPending={status.isPending || (enabled && indicator.isPending)} error={status.error ?? indicator.error} hasData={!!found || !enabled}>
        {!enabled && (
          <Alert tone="neutral">
            <AlertDescription>
              LuxAlgo&apos;s tools are switched off (<code>admin.luxalgo_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
        {found && (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              {source.data?.chart_url && (
                <a href={source.data.chart_url} target="_blank" rel="noopener noreferrer" className={OUT}>
                  Open on Vela <ExternalLink className="size-3.5" aria-hidden />
                </a>
              )}
              {found.url && (
                <a href={found.url} target="_blank" rel="noopener noreferrer" className={OUT}>
                  On LuxAlgo&apos;s site <ExternalLink className="size-3.5" aria-hidden />
                </a>
              )}
              {found.concepts?.map((concept) => (
                <Link key={concept.slug} to={conceptPath(concept.slug)}>
                  <Badge variant="outline">{concept.name}</Badge>
                </Link>
              ))}
            </div>
            {found.known && <InWataPanel slug={slug} known={found.known} />}
            {found.body_markdown && (
              <Panel title="What it does">
                <AIResponse content={shownMarkdown(found.body_markdown)} className="p-0 shadow-none ring-0 sm:p-0">
                  <AIResponseContent />
                </AIResponse>
              </Panel>
            )}
            <Panel
              title="Its Pine source"
              description={source.data?.licence ? `Under ${source.data.licence}${source.data.owner ? `, © ${source.data.owner}` : ''}, as its header says.` : undefined}
            >
              <QueryState isPending={source.isPending} error={source.error} hasData={!!source.data}>
                {cut && (
                  <Alert tone="neutral" className="mb-3">
                    <AlertDescription>
                      {cut}{' '}
                      {source.data?.available && !status.data?.signed_in && (
                        <Link to={LUXALGO_PATH} className="underline underline-offset-4">
                          Sign in on the LuxAlgo Library page
                        </Link>
                      )}
                    </AlertDescription>
                  </Alert>
                )}
                {source.data?.source && (
                  <CodeBlock
                    code={source.data.source}
                    language="text"
                    lineNumbers
                    maxHeight="32rem"
                    copyable
                    label={source.data.complete ? `${found.name}, whole` : `${found.name}, its first lines`}
                  />
                )}
              </QueryState>
            </Panel>
          </>
        )}
      </QueryState>
      {taking && found && source.data && <TakeDialog indicator={found} source={source.data} onClose={() => setTaking(false)} />}
      {found && <AgentSheet subject={{ kind: 'luxalgo', id: slug, label: `LuxAlgo's ${found.name}` }} />}
    </div>
  )
}
