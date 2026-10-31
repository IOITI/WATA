// The admin's sign-in to a LuxAlgo account (src/admin/luxalgo_auth.py): LuxAlgo's page opened in another tab,
// which comes back to the admin by itself; the address it landed on pasted when it could not.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, Button, FormControl, FormDescription, FormItem, FormLabel, Hint, Input, Panel } from 'ioiti-ui'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { LuxAlgoStatus } from '@/lib/types'

import { signInSaid } from './luxalgoModel'

const TONE = { positive: 'success', neutral: 'neutral', warning: 'warning' } as const

export function SignInPanel({ status, editable }: { status: LuxAlgoStatus; editable: boolean }) {
  const queryClient = useQueryClient()
  const [pasting, setPasting] = useState(false)
  const [address, setAddress] = useState('')
  const said = signInSaid(status)
  const settled = (next: LuxAlgoStatus) => {
    queryClient.setQueryData(['luxalgo', 'status'], next)
    // Sources read signed out were cut: read them again.
    void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'source'] })
  }
  const begin = useMutation({
    mutationFn: api.beginLuxalgoSignIn,
    onSuccess: ({ url }) => {
      window.open(url, '_blank', 'noopener,noreferrer')
      setPasting(true)
    },
  })
  const paste = useMutation({
    mutationFn: () => api.pasteLuxalgoSignIn(address.trim()),
    onSuccess: (next) => {
      setAddress('')
      setPasting(false)
      settled(next)
    },
  })
  const signOut = useMutation({ mutationFn: api.luxalgoSignOut, onSuccess: settled })
  const failed = begin.error ?? paste.error ?? signOut.error
  const locked = editable ? null : 'Configuration editing is switched off (admin.config_edit_enabled): the sign-in is a credential.'

  const control = (button: React.ReactElement) =>
    locked ? (
      <Hint content={locked} wrap>
        {button}
      </Hint>
    ) : (
      button
    )

  return (
    <Panel
      title="LuxAlgo account"
      description={said.text}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={TONE[said.tone]} dot>
            {status.signed_in ? 'Signed in' : 'Signed out'}
          </Badge>
          {status.signed_in
            ? control(
                <Button variant="ghost" size="sm" disabled={!!locked} isLoading={signOut.isPending} onClick={() => signOut.mutate()}>
                  Sign out
                </Button>,
              )
            : control(
                <Button
                  variant="outline"
                  size="sm"
                  rightIcon={<ExternalLink className="size-3.5" aria-hidden />}
                  disabled={!!locked}
                  isLoading={begin.isPending}
                  onClick={() => begin.mutate()}
                >
                  Sign in to LuxAlgo
                </Button>,
              )}
        </div>
      }
    >
      <div className="space-y-3 text-sm text-text-secondary">
        <p>
          The admin calls LuxAlgo&apos;s Library, market trackers and prop-firm tools, never its journal or its account. Signed in, the
          account&apos;s token is sealed for the admin alone and sent to LuxAlgo with those calls only.
        </p>
        {pasting && !status.signed_in && (
          <div className="max-w-xl space-y-3">
            <p>
              LuxAlgo&apos;s page opened in another tab: once you said yes there, it comes back to this admin by itself. If it went to another
              address, paste it here.
            </p>
            <FormItem>
              <FormLabel htmlFor="luxalgo-address">The address LuxAlgo sent the browser to</FormLabel>
              <FormControl>
                <Input
                  id="luxalgo-address"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="https://…/api/luxalgo/callback?code=…&state=…"
                  value={address}
                  onChange={(event) => setAddress(event.target.value)}
                />
              </FormControl>
              <FormDescription>The whole address: its state shows it comes from the sign-in begun here, ten minutes at most.</FormDescription>
            </FormItem>
            <Button size="sm" disabled={!address.trim()} isLoading={paste.isPending} onClick={() => paste.mutate()}>
              Finish the sign-in
            </Button>
          </div>
        )}
        {failed && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{failed.message}</AlertDescription>
          </Alert>
        )}
      </div>
    </Panel>
  )
}
