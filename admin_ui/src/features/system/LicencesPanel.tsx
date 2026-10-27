import { Panel } from 'ioiti-ui'

const SOURCE = 'https://github.com/IOITI/WATA'

/**
 * What the admin is built from, and where its source is. PineTS and Vela-pinets are
 * under the AGPL-3.0: whoever uses this admin over the network is offered its whole
 * source, which is the public repository. WATA's own files stay under the MIT licence.
 */
export function LicencesPanel() {
  const link = (href: string, label: string) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-foreground underline underline-offset-2">
      {label}
    </a>
  )
  return (
    <Panel title="Licences and source" description="This admin, and what it is built with.">
      <div className="flex flex-col gap-2 text-sm leading-relaxed text-text-secondary">
        <p>
          The source of the admin you are using is {link(SOURCE, 'the WATA repository')} on GitHub. WATA&rsquo;s own files
          are under the MIT licence.
        </p>
        <p>
          The Technical Analysis pages run Pine Script with {link('https://github.com/LuxAlgo/PineTS', 'PineTS')} and{' '}
          {link('https://github.com/LuxAlgo/Vela-pinets', 'Vela-pinets')} by LuxAlgo, under the GNU Affero General Public
          License v3.0, and the TA engine runs PineTS on the server: the admin and the engine built with them are offered
          with their whole source, above. Their charts are{' '}
          {link('https://github.com/LuxAlgo/Vela', 'Vela™')} by LuxAlgo, under the Apache License 2.0. Pine Script® is a
          trademark of TradingView, Inc.; neither LuxAlgo nor WATA is affiliated with it.
        </p>
        <p>
          The other charts are TradingView Lightweight Charts™ (Apache License 2.0). The libraries, their licences and
          Vela&rsquo;s notice are in {link('/third-party-notices.txt', 'the third-party notices')}, which ship with this admin.
        </p>
      </div>
    </Panel>
  )
}
