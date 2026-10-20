# WATA's research lab

Backtests and analyses on this Mac, over a copy of what the server recorded, with WATA's own
code. Nothing here acts on the server: the server writes each finished day as files, this Mac
pulls them, and everything else happens here.

Claude can also do this research on the server itself, on its live data, through the admin's
MCP endpoint (README, "Research by Claude"): the same loop, with the server's own experiment
journal and lock box. The lab stays the place for heavy analysis and long histories.

## Setting it up, once

1. **On the server**: deploy a version with the research export, then switch it on from the
   admin's Backtests page ("Write them", under the list of runs). From then on, each finished day
   is written under `/app/wata/var/lib/price_history/research/days/`, while the market is closed,
   and the last 30 are kept there (the number is chosen on the same page).
2. **On this Mac**, from the checkout's root:

   ```bash
   lab/setup.sh
   ```

   makes `.venv-lab` (Python 3.12 or newer, with `requirements.txt` and `requirements-lab.txt`).
   The admin's pages and the Pine runner are built in `admin_ui/` (Node 24):
   `npm ci && npm run build && npm run build:runner`.
3. **The morning pull**: `lab/sync.sh` pulls the new days over SSH with your key (the server is
   the one of the deploy inventory, or `WATA_SERVER=user@host`) and loads them into the lab's
   database. To have this Mac run it each morning at 07:15 (and at wake, if it slept then):

   ```bash
   lab/launchd/install.sh
   ```

   `lab/launchd/install.sh --remove` takes it away. Its output goes to `var/lib/lab/log/sync.log`.

## Using it

```bash
.venv-lab/bin/python -m lab status        # what is there
.venv-lab/bin/python -m lab serve         # the lab's admin: http://127.0.0.1:8083
.venv-lab/bin/python -m lab stop          # the lab's admin and its database
```

The lab's admin is WATA's admin over the lab's database: its Backtests pages show the runs made
here, each day's chart and each trade on its turbo, and its Data pages the recorded assets. Its
password is in `var/lib/lab/admin/password.txt`. Operator actions are off, and the pages of the
live services (positions, the dashboard) say that they cannot reach them.

Runs, sweeps and the experiment journal are made from the command line, usually by Claude:

```bash
.venv-lab/bin/python -m lab exp new --title "EMA cross" --hypothesis "…" --family ema-cross
.venv-lab/bin/python -m lab run lab/specs/ema_cross.json --exp <id> --set stoploss_percent=-10
.venv-lab/bin/python -m lab sweep lab/specs/ema_cross.json --exp <id> --axis setting:day_profit_target=1,1.7,3
.venv-lab/bin/python -m lab show <run>
.venv-lab/bin/python -m lab sql "SELECT count(*) FROM price_bar_10s"
```

What a run is worth, beyond its total:

```bash
.venv-lab/bin/python -m lab edge <run>                    # the index's move after each entry signal, against random moments
.venv-lab/bin/python -m lab regimes <run>                 # each day's kind, and the trades by it
.venv-lab/bin/python -m lab stress <run>                  # the run again with later and dearer fills
.venv-lab/bin/python -m lab neighbours <run> --param in_0 # the values around the run's own
.venv-lab/bin/python -m lab reconcile <run>               # a replay of the journal beside WATA's real positions
.venv-lab/bin/python -m lab calibrate execution           # what WATA's own orders took and cost
.venv-lab/bin/python -m lab model calibrate|show          # turbos made up from Dukascopy's Nasdaq, for long histories
.venv-lab/bin/python -m lab model validate <spec>         # the same signals on recorded and on made-up turbos
.venv-lab/bin/python -m lab paper [<rule>]                # each shadow rule's days traded on paper
.venv-lab/bin/python -m lab calendar check                # whether the macro calendar knows the news ahead
```

A sweep takes `--walk-forward TRAIN/TEST` (days to choose on, days to keep it); its summary
says how likely choosing its best was fitting, with the family's earlier trials counted.

`python -m lab --help` lists the rest. `.claude/skills/wata-lab/SKILL.md` is the method Claude
follows.

## Where things are

Everything is under `var/lib/lab/` (git-ignored): the pulled days (`price_history/`), the
server's scripts and rules (`server_ta/`), the lab's scripts (`ta/`), the runs (`backtests/`),
the database (`pg/`), the experiment journal (`experiments.jsonl`) and the reports (`reports/`).
`python -m lab db prune --keep-months N` drops the oldest months of bars from the database; the
pulled files stay, and can be loaded again.
