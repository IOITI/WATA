import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  FormDescription,
  FormItem,
  FormLabel,
  Input,
  Panel,
  QueryState,
  SettleModal,
  Textarea,
} from 'ioiti-ui'
import { Check, Copy, KeyRound, PlugZap, RotateCw, Send, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { BrandMark } from '@/components/BrandMark'
import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { useSession } from '@/features/auth/auth-context'
import { RestartAction } from '@/features/system/RestartViews'
import { ApiError, api } from '@/lib/api'
import type { Credential } from '@/lib/types'

import {
  CREDENTIAL_BRANDS,
  HELPERS,
  KEPT_ELSEWHERE,
  WHERE_LABEL,
  checksSaid,
  fromSaxoAppObject,
  inSentence,
  rotationSaid,
  saxoAppEnvironment,
  whereSaid,
  type SaxoEnvironment,
} from './credentials'
import { useConfigEditor } from './editor-context'

function errorText(error: Error | null): string | null {
  if (error instanceof ApiError && error.status === 403) return 'Switched off in the configuration (admin.config_edit_enabled or admin.actions_enabled).'
  return error?.message ?? null
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="ghost"
      size="sm"
      leftIcon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      onClick={() => void navigator.clipboard?.writeText(text).then(() => setCopied(true))}
    >
      {copied ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** Saxo's app object pasted as the portal copies it: its key and secret fill the fields, what does not fit is said. */
function SaxoAppPaste({ environment, onRead }: {
  environment: SaxoEnvironment
  onRead: (values: Record<string, string>) => void
}) {
  const saxo = useQuery({ queryKey: ['saxo'], queryFn: api.saxo })
  const [text, setText] = useState('')
  const read = text.trim() ? fromSaxoAppObject(text, environment, saxo.data?.redirect_url ?? null) : null

  return (
    <FormItem>
      <FormLabel htmlFor="credential-app-object">App object (optional)</FormLabel>
      <Textarea
        id="credential-app-object"
        rows={3}
        spellCheck={false}
        autoComplete="off"
        className="font-mono text-xs"
        placeholder='{ "AppName": "…", "AppKey": "…", "AppSecret": "…", … }'
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          const found = event.target.value.trim() ? fromSaxoAppObject(event.target.value, environment) : null
          if (found && 'values' in found) onRead(found.values)
        }}
      />
      {read && 'error' in read && <FormDescription className="text-negative">{read.error}</FormDescription>}
      {read && 'values' in read && (
        <div className="space-y-2">
          <FormDescription>Its App Key and App Secret are in the fields below. Its addresses are not kept.</FormDescription>
          {read.warnings.length > 0 && (
            <Alert tone="warning">
              <AlertDescription>
                <ul className="list-disc space-y-1 pl-4">
                  {read.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </FormItem>
  )
}

/** Its parts entered, checked by the server, sealed for the services that read it. Never shown back. */
function SetDialog({ credential, open, onClose, onSaved }: {
  credential: Credential
  open: boolean
  onClose: () => void
  onSaved: (services: string[]) => void
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<Record<string, string>>({})
  const mutation = useMutation({
    mutationFn: () => api.setCredential(credential.name, values),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['credentials'] })
      setValues({})
      onSaved(saved.services)
    },
  })
  const close = () => {
    setValues({})
    mutation.reset()
    onClose()
  }
  const complete = credential.parts.every((part) => (values[part.key] ?? '').trim() !== '')
  const saxoEnvironment = saxoAppEnvironment(credential.name)

  return (
    <SettleModal
      open={open}
      onClose={mutation.isPending ? () => {} : close}
      title={`${credential.where === 'database' ? 'Replace' : 'Set'} the ${inSentence(credential)}`}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={close} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button size="sm" isLoading={mutation.isPending} disabled={!complete} onClick={() => mutation.mutate()}>
            Seal and save
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (complete) mutation.mutate()
        }}
      >
        <ol className="list-decimal space-y-1 pl-5 text-xs text-text-secondary">
          {(HELPERS[credential.name] ?? []).map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {saxoEnvironment && (
          <SaxoAppPaste environment={saxoEnvironment} onRead={(read) => setValues((current) => ({ ...current, ...read }))} />
        )}
        {credential.parts.map((part) => (
          <FormItem key={part.key}>
            <FormLabel htmlFor={`credential-${part.key}`}>{part.label}</FormLabel>
            <Input
              id={`credential-${part.key}`}
              type={part.secret ? 'password' : 'text'}
              showPasswordToggle={part.secret}
              autoComplete="off"
              spellCheck={false}
              value={values[part.key] ?? ''}
              onChange={(event) => setValues((current) => ({ ...current, [part.key]: event.target.value }))}
            />
            <FormDescription>{part.expected}</FormDescription>
          </FormItem>
        ))}
        <p className="text-xs text-muted-foreground">
          Sealed with the keys of {credential.readers.length === 1 ? 'the one service' : `the ${credential.readers.length} services`}{' '}
          that read it
          {credential.readers.includes('admin')
            ? ', the admin among them for its own use: this page never shows it back.'
            : ': nobody else, the admin included, can read it back.'}
        </p>
        {mutation.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{errorText(mutation.error)}</AlertDescription>
          </Alert>
        )}
      </form>
    </SettleModal>
  )
}

/** Whether the services log in with this app now, and where its login is. */
function SaxoAppInUse({ environment }: { environment: SaxoEnvironment }) {
  const saxo = useQuery({ queryKey: ['saxo'], queryFn: api.saxo })
  const inUse = saxo.data?.environment
  if (!inUse) return null
  return (
    <p className="text-xs text-text-secondary">
      {inUse === environment
        ? `In use: the services trade on the ${environment} account. `
        : `Not in use: the services trade on the ${inUse} account (saxo_auth.env, set on the server). `}
      Its login:{' '}
      <Link className="underline underline-offset-2" to="/system">
        System page, Saxo connection
      </Link>
      .
    </p>
  )
}

function CredentialCard({ credential, names, timeZone, keysMissing }: {
  credential: Credential
  names: Record<string, string>
  timeZone: string
  keysMissing: boolean
}) {
  const queryClient = useQueryClient()
  const { config_edit_enabled: canEdit, actions_enabled: canAct } = useSession()
  const [editing, setEditing] = useState(false)
  const [rotating, setRotating] = useState(false)
  const [removing, setRemoving] = useState(false)
  // The services a change reaches: offered to restart.
  const [changed, setChanged] = useState<string[] | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['credentials'] })
  const rotation = useMutation({
    mutationFn: () => api.rotateCredential(credential.name),
    onSuccess: async (saved) => {
      await refresh()
      setRotating(false)
      setChanged(saved.services)
    },
  })
  const removal = useMutation({
    mutationFn: () => api.removeCredential(credential.name),
    onSuccess: async (removed) => {
      await refresh()
      setRemoving(false)
      setChanged(removed.services)
    },
  })
  const show = useMutation({ mutationFn: api.showWebhook })
  const tryBot = useMutation({ mutationFn: api.testTelegram, onSuccess: () => refresh() })
  const tryKey = useMutation({ mutationFn: api.testAnthropicKey, onSuccess: () => refresh() })
  const where = WHERE_LABEL[credential.where]
  const checks = checksSaid(credential.checks, names, timeZone)
  const blocked = keysMissing || !canEdit

  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          {CREDENTIAL_BRANDS[credential.name] ? (
            <BrandMark brand={CREDENTIAL_BRANDS[credential.name]} />
          ) : (
            <KeyRound aria-hidden className="size-4 text-muted-foreground" />
          )}
          {credential.label}
        </span>
      }
      description={whereSaid(credential, timeZone)}
      action={
        <Badge tone={where.tone} dot>
          {where.label}
        </Badge>
      }
      bodyClassName="space-y-3 pt-3"
    >
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Read by</dt>
          <dd className="mt-1 flex flex-wrap gap-1">
            {credential.readers.map((reader) => (
              <Badge key={reader} variant="outline">
                {names[reader] ?? reader}
              </Badge>
            ))}
          </dd>
        </div>
        {Object.entries(credential.public).map(([key, value]) => (
          <div key={key}>
            <dt className="text-muted-foreground">{credential.parts.find((part) => part.key === key)?.label ?? 'Server'}</dt>
            <dd className="mt-1 break-all font-mono">{value}</dd>
          </div>
        ))}
      </dl>
      {saxoAppEnvironment(credential.name) && <SaxoAppInUse environment={saxoAppEnvironment(credential.name)!} />}
      {checks.length > 0 && (
        <ul className="space-y-0.5 text-xs text-text-secondary">
          {checks.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {credential.generated ? (
          <Button size="sm" variant="outline" leftIcon={<RotateCw className="size-3.5" />} disabled={blocked} onClick={() => setRotating(true)}>
            {credential.where === 'database' ? 'Rotate' : 'Make it here'}
          </Button>
        ) : (
          <Button size="sm" variant="outline" leftIcon={<KeyRound className="size-3.5" />} disabled={blocked} onClick={() => setEditing(true)}>
            {credential.where === 'database' ? 'Replace' : 'Set'}
          </Button>
        )}
        {credential.name === 'webhook.token' && (
          <Button size="sm" variant="ghost" disabled={!canAct} isLoading={show.isPending} onClick={() => show.mutate()}>
            Show the address
          </Button>
        )}
        {credential.name === 'telegram.bot' && credential.where !== 'missing' && (
          <Button size="sm" variant="ghost" leftIcon={<Send className="size-3.5" />} disabled={!canAct} isLoading={tryBot.isPending} onClick={() => tryBot.mutate()}>
            Send a test message
          </Button>
        )}
        {credential.name === 'anthropic.api' && credential.where === 'database' && (
          <Button size="sm" variant="ghost" leftIcon={<PlugZap className="size-3.5" />} isLoading={tryKey.isPending} onClick={() => tryKey.mutate()}>
            Test the connection
          </Button>
        )}
        {credential.where === 'database' && !credential.generated && (
          <Button size="sm" variant="ghost" leftIcon={<Trash2 className="size-3.5" />} disabled={blocked} onClick={() => setRemoving(true)}>
            Remove
          </Button>
        )}
      </div>
      {show.data && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded-md border border-border bg-surface-raised/60 px-2 py-1 text-xs">
              {show.data.url ?? show.data.path}
            </code>
            <CopyButton text={show.data.url ?? show.data.path} />
          </div>
          {!show.data.url && <p className="text-xs text-muted-foreground">On your API domain (API_DOMAIN of .env).</p>}
        </div>
      )}
      {tryBot.data && (
        <Alert tone={tryBot.data.ok ? 'success' : 'danger'}>
          <AlertDescription>
            {tryBot.data.ok ? 'Telegram took the test message: look for it in the chat.' : `Telegram refused it: ${tryBot.data.detail}`}
          </AlertDescription>
        </Alert>
      )}
      {tryKey.data && (
        <Alert tone={tryKey.data.ok ? 'success' : 'danger'}>
          <AlertDescription>
            {tryKey.data.ok ? `The Claude API answered. ${tryKey.data.detail}.` : `The Claude API refused it: ${tryKey.data.detail}`}
          </AlertDescription>
        </Alert>
      )}
      {(show.error || tryBot.error || tryKey.error) && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{errorText(show.error ?? tryBot.error ?? tryKey.error)}</AlertDescription>
        </Alert>
      )}
      {changed && changed.length === 0 && (
        <Alert tone="success">
          <AlertDescription>Saved. It applies at once: nothing has to start again.</AlertDescription>
        </Alert>
      )}
      {changed && changed.length > 0 && (
        <Alert tone="warning">
          <AlertDescription>
            <div className="space-y-2">
              <p>Saved. It applies when these start again: {changed.map((id) => names[id] ?? id).join(', ')}.</p>
              <RestartAction ids={changed} />
            </div>
          </AlertDescription>
        </Alert>
      )}
      <details className="text-xs">
        <summary className="cursor-pointer text-text-secondary">How to get it</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-text-secondary">
          {(HELPERS[credential.name] ?? []).map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </details>

      <SetDialog credential={credential} open={editing} onClose={() => setEditing(false)} onSaved={(services) => {
        setEditing(false)
        setChanged(services)
      }} />
      <ConfirmLiveAction
        open={rotating}
        title={`${credential.where === 'missing' ? 'Make' : 'Rotate'} the ${inSentence(credential)}`}
        confirmLabel={credential.where === 'missing' ? 'Slide to make it' : 'Slide to rotate'}
        doneLabel="Rotated"
        pending={rotation.isPending}
        error={errorText(rotation.error)}
        warning={
          credential.name !== 'webhook.token'
            ? undefined
            : credential.where === 'missing'
              ? 'Until TradingView’s alerts send it, the web server refuses them.'
              : 'TradingView’s alerts stop reaching WATA once the one before is no longer accepted.'
        }
        description={<p>{rotationSaid(credential)}</p>}
        onConfirm={() => rotation.mutate()}
        onClose={() => {
          rotation.reset()
          setRotating(false)
        }}
      />
      <ConfirmLiveAction
        open={removing}
        title={`Remove the ${inSentence(credential)}`}
        gesture="button"
        confirmLabel="Remove"
        pending={removal.isPending}
        error={errorText(removal.error)}
        warning="The services that read it do without it from their next start."
        description={<p>Its row is removed from PostgreSQL. Until it is set again, its old place is read, where there is one: {credential.old_place}.</p>}
        onConfirm={() => removal.mutate()}
        onClose={() => {
          removal.reset()
          setRemoving(false)
        }}
      />
    </Panel>
  )
}

/** The Credentials group of the Configuration page: what is sealed in PostgreSQL, and what is kept elsewhere. */
/** The credentials' cards; ``only``: those of one group's page (the AI agent's key), without what is kept elsewhere. */
export function CredentialsPanel({ only }: { only?: string[] } = {}) {
  const { catalog } = useConfigEditor()
  const { timezone } = useSession()
  const query = useQuery({ queryKey: ['credentials'], queryFn: api.credentials })
  const names = Object.fromEntries(catalog.services.map((service) => [service.id, service.name]))
  const items = (query.data?.items ?? []).filter((credential) => !only || only.includes(credential.name))

  return (
    <>
      <QueryState isPending={query.isPending} error={query.error} hasData={!!query.data}>
        {query.data && (
          <div className="space-y-3">
            {query.data.keys && (
              <Alert tone="neutral">
                <AlertDescription>{query.data.keys}. What is here can be read, nothing can be set.</AlertDescription>
              </Alert>
            )}
            {items.map((credential) => (
              <CredentialCard
                key={credential.name}
                credential={credential}
                names={names}
                timeZone={timezone}
                keysMissing={query.data.keys !== null}
              />
            ))}
          </div>
        )}
      </QueryState>
      {!only && (
        <Panel title="Kept elsewhere" description="Credentials not set from these cards: where each is kept, and how it is changed.">
          <ul className="space-y-1.5 text-xs">
            {KEPT_ELSEWHERE.map((entry) => (
              <li key={entry.what}>
                {entry.brands?.map((brand) => (
                  <BrandMark key={brand} brand={brand} className="mr-1 size-3.5 align-[-2px]" />
                ))}
                <span className="font-medium text-foreground">{entry.what}</span>
                <span className="text-text-secondary">: {entry.where}.</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </>
  )
}
