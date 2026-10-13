# Changelog

## v0.16.0 - Python 3.14

**Release date:** 2026-10-13

The second of the two releases that move WATA from Python 3.12 to 3.14, and the one that does it: the image is now built with Python 3.14. Everything this needed was done in v0.15.0, on Python 3.12, so that this release is one line of the `Dockerfile` and nothing else. It was written to be deployed after v0.15.0 had traded for some days, so that a fault would say which of the two it came from. **The operator chose to deploy v0.14.0, v0.15.0 and this one together**, on 2026-10-13: the server went from 0.13.0 to 0.16.0 in one step, and a fault seen from now on may come from any of the three.

**Verification status:** CI built this image, with Python 3.14 for the server's platform, on the commits of v0.15.0: it holds exactly the libraries of `requirements.txt`, every service loads inside it, the tests pass on 3.14 and so do the tests against a real PostgreSQL and a real RabbitMQ. On a development machine, with Python 3.14.8: the same backtest, with its random entries, gives the same files to the byte as on 3.12; the admin application was started and every page visited in a browser, with a backtest launched from the page. **Not run yet**: the new CI job that builds the image with the server's own command (it runs on GitHub at the next push).

**At its deployment** (2026-10-13, 20:39 to 20:41 Paris: no position open, and no entry possible that evening, the day's result being above its limit; about two minutes without the trading services): the image was first built on the server while the application ran, and checked there: Python 3.14.8, the libraries of `requirements.txt` and no other, the server's own configuration accepted, every service loaded. After the start, all eight services ran on Python 3.14.8 with no error in their logs; the position monitor, the watchlist manager and the price recorder connected to Saxo's stream through the new WebSocket client and got their subscriptions; the trader took up its polling; the start messages of the trader and the monitor went through RabbitMQ and reached Telegram; `order_execution` was created. In the hour and a half that followed: Saxo closed the stream five times, at each renewal of the token, and each of the three clients came back within about a second with its subscriptions, as before the upgrade; the 21:55 closing signal went from the scheduler through the web server to the trader, which journaled that nothing was open; the 22:00 daily report went from the scheduler through RabbitMQ to the position monitor, reached Telegram and recorded the account's balance. **Not seen yet**: an order (none could be placed that evening: the first trade of the next session is the first on all of this), and a signal from TradingView (none arrived between the start and 22:04; the longest gap between two that day had been about three hours).

---

### What Changed

- **The image is built with Python 3.14** (`ARG PYTHON_VERSION=3.14`): the 3.14 current on the day the server builds it, 3.14.8 as this is written. Python 3.12 only receives security fixes and stops in 2028; 3.14 gets bug fixes until October 2027 and security fixes until October 2030.
- **The system under it does not change.** Looked up on the server at the deploy: the image in use already stood on Debian 13, the server asking the registry for the base image at each build, and the new one does too, with the same OpenSSL (3.5.7), which every connection to Saxo and to Telegram goes through, and the same compression library (zlib 1.3.1).
- **CI keeps testing Python 3.12** beside 3.14, and building the image with it, until 3.14 has run in production: going back stays something that is tested. A new job builds the image with the server's own command and checks that the Python it gets is the one the `Dockerfile` names.

Nothing changes in WATA's code, in its libraries or in the database.

### Upgrade Notes

- **This deploy stops the trading services longer than usual, unless it is prepared.** The install script stops the application and then builds the image, and this build has a new base image to download and every library to install again for Python 3.14. To keep the stop as short as any other, build the image once on the server while the application still runs, from a copy of this version's files and under a tag of its own (`docker build --platform=linux/amd64 -t wata-base:next .`): the build that follows the stop then finds the base image and the libraries already there. Done so on 2026-10-13: 46 seconds for that first build, and a stop of about two minutes.
- **Deploy it with no position open**, at a time when none can be opened.
- **To go back**: stop, put the previous version back in `/app/wata/VERSION`, give the previous image the tag `wata-base:latest` again, start. The previous image holds its own Python 3.12, so both come back together. Tag it before the deploy (`docker tag wata-base:latest wata-base:rollback-<version>`): the build replaces `latest`.
- **Watch after the start**: each service's start line, Saxo's authorisation without a manual login, the position monitor's first snapshot of the stream, the token renewal and the stream's reconnection about twenty minutes later, then the first signal from the webhook to its position and the first Telegram message.
- Nothing to configure.

### Known Limits

- **The price recorder writes one warning at its start**: `'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`. It comes from uvloop, when the recorder asks to be told of a stop signal, and changes nothing today. It is the one thing Python 3.14 said in the services' output.
- A day of prices fetched again is expected to give the same file as before: a backtest keeps a digest of the price files it read, the library that compresses them is the same in both images, and on a development machine the two Pythons gave the same bytes. Not compared on the server itself.
- A development environment on Python 3.12 still runs the tests, for as long as CI does.

## v0.15.0 - The libraries brought forward, ahead of Python 3.14

**Release date:** 2026-10-13, deployed together with v0.16.0: it never ran on a server on Python 3.12

WATA runs on Python 3.12, which only receives security fixes and stops in 2028. Moving to Python 3.14 is not held back by WATA's code but by its libraries: ten of the versions it was pinned to cannot be installed on 3.14 at all, and several others that every signal and every position go through were released long before 3.14 existed. This release brings those libraries to versions that run on both, **still on Python 3.12**, so that the change of interpreter that follows is that and nothing else. Nothing changes in what WATA does.

**Verification status:** every test passes on Python 3.12 and on Python 3.14.8 with the new libraries (1,279 tests), on a development machine. The stream client's new tests were written and passed on the library of before, then showed what its upgrade breaks, then passed again once the client was adapted. The database tests ran against a real PostgreSQL 16.2 with asyncpg 0.30 and 0.31, on both interpreters. The same backtest, with its two hundred runs of random entries, gives the same files to the byte on both. The admin application was started on Python 3.14 and every page visited in a browser, a backtest asked for from the page included. All fifty-four libraries have a ready-made build for the server's platform on both interpreters. CI then ran its new jobs: the image builds with Python 3.12 and 3.14 and every service loads in it, and the tests against a real PostgreSQL and a real RabbitMQ pass on both. One job, the tests on 3.12, failed once and passed on the commits before and after it; which test it was is not known, and a failed test is now named beside its job. **Against Saxo**, the stream's new client was first used at the deployment of v0.16.0: see there.

---

### What Changed

#### Libraries
- **Upgraded to the first release that supports Python 3.14** (each also runs on 3.12): pydantic 2.7 to 2.12, asyncpg 0.30 to 0.31, uvloop 0.19 to 0.22, uvicorn 0.29 to 0.38, httptools 0.6 to 0.7, websockets 12 to 16, pika 1.3 to 1.4, aio-pika 9.5 to 9.6, jsonschema 4.22 to 4.26 with referencing and rpds-py, duckdb 1.1 to 1.4, PyYAML 6.0.1 to 6.0.3, typing_extensions 4.12 to 4.14. The defaults of every call WATA makes into them were compared before and after: none changed, but for the WebSocket client below.
- **Removed, because nothing uses them**: twenty-five pins (selenium, trio, psycopg, typer, rich, orjson, ujson, Jinja2, python-multipart, watchfiles and others). Read from the installed packages' own requirements, not guessed: nothing WATA imports needs them.
- **`requirements.txt` is now the whole environment.** Sixteen libraries were installed without being named (aiormq, yarl, h2, cffi…), so their version was chosen by pip on the server at each deploy. They are pinned, and CI fails when anything is installed that the file does not name, or the other way round.

#### The link to Saxo's stream
- The WebSocket library's default client was replaced upstream by a new one. `SaxoStreamClient` is adapted to it: same keepalive (a ping every 20 seconds), same limits, same reconnection, and still a direct connection (the new client would have looked for a proxy in the environment).
- **Its connection had no test.** It now has thirteen, ten of them against a WebSocket server on the machine: opening with the token and the context, reading Saxo's frames, Saxo dropping the connection without a word (the case seen every twenty minutes in production), coming back from the last message read without subscribing again, a `_disconnect` message, leaving. Without the adaptation, the upgrade would have broken the stop of the position monitor and every change of price subscription, and no test of before would have said so.

#### How the services start
- The trader, the position monitor and the price recorder run on uvloop as before, without making it the default of the whole process: the mechanism that did so is deprecated in Python 3.14 and removed in 3.16.

#### The image
- **Its Python is one line at the top of the `Dockerfile`** (`ARG PYTHON_VERSION=3.12`). CI builds the image with 3.12 and with 3.14.
- **Libraries are installed before the code is copied**, and only from ready-made builds. Until now every deploy downloaded all of them again while the trading services were stopped, because the code was copied first. A deploy that only changes the code now reuses them. The first build after this release still downloads everything.

#### CI
- The tests run on Python 3.12 and 3.14.
- **The image is built** for each, as the server builds it, and every service is loaded inside it.
- **PostgreSQL and RabbitMQ for real**, through the services' own code, on each Python: what the other tests replace with fakes.

### Upgrade Notes

- Nothing to configure, no change to the database.
- The first deploy of this release takes longer than usual: a few minutes for the libraries, once.
- A development environment needs `pip install -r requirements.txt` again: with the libraries of before, the stream client's new tests fail.

### Known Limits

- The web servers still load the WebSocket library's old server code, through uvicorn, though none of them serves a WebSocket. It is deprecated upstream, not removed.

## v0.14.0 - Each order followed to its position

**Release date:** 2026-10-13, deployed together with v0.16.0

An order and the position it gave were linked in the database and hardly on the pages, and nothing said how long Saxo took to turn one into the other: an order's time was written once its position had been confirmed, an order that gave no position was cancelled and written nowhere, and the sell order that closes a position was never recorded at all. This release makes the trader and the position monitor keep the moments of every order, and the admin interface show them, so that a slow entry or an order given up can be explained from the Orders page.

**Verification status:** covered by unit tests, six deliberate faults in the new trading code were each caught by them, and the pages were driven in a browser on a development machine, in both themes and at a phone's width, on made-up orders. The new SQL (the table, the two writes of an order in either order, the admin's joined reads through a read-only connection and their fallback) was run against a real PostgreSQL 16.2 on that machine. **On the server** since 2026-10-13: the table was created at the start, and no order could be placed that evening, the day's result being above its limit. **No real order has been followed yet**: the first trade of the next session will be the first. No new request is made to Saxo; what is read is the answer to requests WATA already made.

---

### What's New

#### Orders page: what became of each order
- **Sell orders and orders that gave no position are listed**, like the others. Until now the page only held the buy orders that had given a position.
- **Result**: opened a position, closed a position, no position (cancelled, or cancel refused), not seen closed, stopped by an error. The position's id is a link to it.
- **Took**: from the order leaving to WATA seeing its position open (or closed), or to the giving up. Sort on it to find the slow ones.
- **Three figures for the period**: the typical time from order to position, the slowest entry, and the number of orders Saxo accepted that gave no position.
- **A row opens onto each moment**: signal received, order sent, accepted by Saxo, filled at Saxo, seen by WATA, each with the time since the step before. For an order given up: how long and how many times the trader looked, then what Saxo answered to the cancellation, in Saxo's own words when it refused.
- Two clocks are involved and the page says which is which: the server's for what WATA did and saw, Saxo's for the fill. "Order sent" to "filled at Saxo" is the time that speaks about Saxo; "seen by WATA" also holds WATA's own waits between two looks.

#### Positions page: the order that opened it, the order that closed it
- A position's row opens onto **"Opened by order"** and **"Closed by order"**, each with its moments and a link to the order.
- A position Saxo closed by itself (a knock-out, a stop resting at Saxo, a sale made there by hand) says so, instead of showing no sell order without a word.
- A link from an order lands on its position among all of them, found and opened; a link from a position does the same on the Orders page, whenever the order was placed.

#### What the trading services now keep
- A table of their own, `order_execution`: for each order Saxo accepted, when it left, when Saxo answered, when its position was seen open or closed and at which look, or when the service gave up, with Saxo's answer to the cancellation.
- Kept in memory while the order is placed and **written in the background**, once Saxo has the order: nothing on the way to a buy or a sell waits for it, and a failure to write it is logged and changes nothing of the trade.
- The journal of signals keeps the order's id for a signal whose order gave no position, so that the signal behind such an order is found.

### What Changed

- **An order's recorded time is when it left**, no longer the moment after its position was confirmed (up to some ten seconds later). Orders already recorded keep the time they have: for them the page shows Saxo's fill time and the second WATA wrote the order down, and says the rest was not kept.
- The Orders page hides the side, the type and the cost until they are picked in the column list: the result says whether it was a buy or a sale.
- The message of an order that gave no position (Telegram, logs, journal) carries Saxo's answer when it refused the cancellation.

### Upgrade Notes

- Nothing to configure. The table is created by the trader and the position monitor when they start with this version; until then the admin reads the orders alone, as before.
- **Past orders are not filled in**: an order placed before this version has no moments, and its sell order was never recorded.

### Known Limits

- A cancellation Saxo refuses can mean that the order was filled after the trader stopped looking: such a position is at Saxo and not in WATA's database. The page says so and asks for a look at the account; WATA does nothing about it by itself.
- The stop order resting at Saxo (opt-in) is still not listed among the orders.

## v0.13.0 - Backtests that say how far to trust them, and what to change

**Release date:** 2026-10-13

A backtest gave a total, and nothing to tell a good one from luck. With WATA's rules a position is sold at its stop or as soon as the day has reached its target, so much of a result comes from the exits whatever the entry was. This release makes every backtest say what its total is worth, lets the model be checked against what WATA really did, and fetches the prices without anyone watching. It then gives the means to look for what to change: several values tried at once with days held out, the trades broken down, one trade followed on the turbo, and the result in money. Nothing changes for the trading services.

**Verification status:** covered by unit tests, and the pages were driven in a browser against the admin application on a development machine: a backtest with its random entries, the measure of the turbo, a replay of signals and its comparison, a sweep and the backtest made of one of its combinations, the breakdowns, a trade followed, renaming and removal, in both themes and at a phone's width. All of it on three days of prices, with "real trades" made up from them. At its deployment (2026-10-13, market open, no position open, about two minutes without the trading services) the admin's new code was run read-only on the server: it read the live rules, the folders of prices and runs, and both database reads ran against the real PostgreSQL (442 closed positions, 2 journal rows). **Not yet run on a server**: a backtest, a sweep, the measure of the turbo and the comparison with real trades, and the unattended download against Dukascopy.

---

### What's New

#### Every backtest is set against entries taken at random
- The same days are traded again **with a coin and a clock**: on each day as many entries as the strategy asked for, each at a moment drawn in the hours it may enter in, long or short by a coin, and only WATA's rules to end them. Two hundred times over, with the same draws from one run to the next.
- A run now says **the share of those random runs it did better than**. Near 50 %, the entries have shown nothing the exit rules do not explain; the page says so in words, and shows where the run stands among them.
- In the list of runs, in the comparison of runs and for each leverage of a run: "Beats random".

#### Figures that say what they rest on
- **Per trade**: what a trade brings on average, what was won for 1 lost, the most trades lost in a row.
- **The range of the total** when the same number of days is drawn again from the days tested (not given for fewer than five days).
- **Room for a stop and a target**: each trade is kept with the worst level it went through, beside its best. A run says how far winning trades fell before they won, and how high losing trades had been.
- **"Too few to conclude"**, in words, for a run of fewer than 30 trades or 20 days.

#### The turbo measured on WATA's closed trades
- A backtest prices a turbo from the index with three numbers that were guesses: what is paid for it, how many index points move it by 1.00, and its spread. **"Measure on real trades"** (Backtests page) reads WATA's closed positions and the index at the open and the close of each, and finds the turbo they describe, with the range the leverage could be in and how far the result is from the real trades.
- Backtests are then made with it: once for the leverage found, once for each end of its range.
- From a checkout: `python -m src.strategy.calibration --trades trades.json`.

#### The signals WATA really received, replayed
- "New backtest" can replay **the signals of WATA's own journal** instead of a strategy, through the same rules and the same turbo.
- The run is then set **against what WATA really did**: each real trade beside the model's, how far apart their results are, whether they ended the same way, the trades only one of the two made, and the days of both. The gaps found are the margin every backtest is to be read with.
- A signal is replayed as the trader receives it: an entry the rules refuse closes nothing. The minimum confidence asked of a signal, when that check is on, is now one of the rules a backtest applies.

#### Prices without babysitting
- **"Keep the prices up to date"** (Backtests page, off until switched on): the service fetches what is missing of the last 30 to 500 days by itself, the newest days first, a few at a time.
- **Only while the market is closed** by the live rules, and it waits longer each time the source stops answering: half an hour, then twice as long each time, up to twelve hours.
- A day the source has no more of is not asked for again.

#### Several values at once, with days held out
- "New backtest" has a section **"Try several values"**: one setting or two (a parameter of the strategy, a rule of WATA), each with the values to try. Every combination is replayed **in one pass over the prices**, of a strategy or of the signals WATA received.
- **The last days of the period are held out** (30 %, or 10 to 50): the page shows every combination on the days a value may be chosen on and, beside it, on the days that had no say in the choice. It says in words what became of the best one there: "it held up", or "it was fitted to the first ones".
- A combination is made **a backtest of its own** with one button, to be read with all a backtest says. From a checkout: `python -m src.strategy.sweep --axis rule:day_profit_target=1,1.7,3`.

#### Where a result comes from, and one trade followed
- **Breakdowns** on a run: its trades by hour of entry, weekday, direction, what they were entered and left on, confidence and holding time, with the same figures as the Exits page has for real trades, and how much of the best level was given back.
- **A trade leads to its day**, brought into view around it, with **the turbo's result every ten seconds** from the entry to the exit against the stop, the day's target and the trailing stop.

#### Less kind fills, and the result in money
- A backtest filled every decision at the first price of the next ten seconds. It can now **wait longer** before a fill and **lose index points** on each: what a result owes to perfect fills shows at once.
- With a starting capital (10,000 unless said otherwise), a run gives its result **in money at the size WATA would have given each trade**: the sizing by time of day and by confidence of the configuration, in whole turbos. Per trade, per day, and the account after each day.

#### Runs that can be found again, and trusted to be what they were
- A run keeps **what it was made with**: the version of the code and a fingerprint of the prices it read. One whose code or prices have changed since is marked **stale**, in the list and on its page.
- A run can be **named again and tagged** after it was made; the list is searched by tags.
- **Several runs are removed at once**: those ticked, the failed or stopped ones, or those older than 30 days. The list says what the runs and the prices take on disk.

---

### Things to Know

- **A total proves nothing by itself.** Read it with the random runs it beat and with the number of trades and days it rests on.
- **A sweep finds the value that did best, which is not the value that will.** The held-out days are the guard, and on a short period they are few. A sweep runs on one turbo and without the random entries.
- **Nothing is removed by itself.** There is no automatic retention: runs are removed when someone asks, a tick or a button at a time.
- **The money leaves out fees**: the broker's commission on each order is not counted.
- **Files of prices fetched from now on are the same to the byte whenever they are written.** The ones fetched before keep the time they were written in their header: a run made on them is marked stale if they are fetched again, even with the same prices.
- **Runs made before this release** show their figures as before, and a note where the new ones would be: run them again to have them.
- **Once the turbo is measured, new runs use it**, and can no longer be compared side by side with runs made on the assumed leverages (80, 110, 140): they have no leverage in common.
- **The measure of the turbo is rough.** The index of the history is another broker's and in dollars; the turbo is quoted in euros. A line through fewer than thirty trades is a first idea, and the page says so.
- **Both need the prices of the days WATA traded.** A trade whose day has no prices is left out of the measure; a signal that came at a moment without prices is not replayed.
- **Disk space:** about 5 MB for each day of prices, some 400 MB for the 120 days the unattended download goes back by default.
- **A backtest takes longer**: the random entries are the larger part of it, the more so as the rules hold a position longer. Three days took one second on a laptop with the example rules, eight with rules that sell nothing before the end of the day: count minutes for a year, more on a small server, where it runs at the lowest priority. A run is stopped after three hours. `--baseline 0` leaves the random entries out from the command line.

---

## v0.12.0 - Price Recorder

**Release date:** not yet released

This release adds an optional service that records the prices of the turbos WATA could buy, and of their index, every ten seconds, and a page of the admin interface to look at them. Saxo's history goes no finer than one minute, so this is what a later backtest on real prices needs, and it can only be collected from the day it is switched on. Nothing changes for the trading services, and nothing is recorded unless enabled.

**Verification status:** covered by unit tests, including the recorder fed by the real streaming client with made-up messages. It has not been run against Saxo, PostgreSQL or in Docker when this entry was written. Two things in particular are unconfirmed: that Saxo accepts a third streaming connection on the same token beside the Position Monitor and the Watchlist Manager, and how many instruments it allows in one price subscription (the recorder asks for at most 100 at a time). Run it in Saxo's simulation environment first. The Prices page was driven in a browser against the admin application running over made-up prices; its queries, like the recorder's, have not been run against a real PostgreSQL.

---

### New Features

#### Price Recorder (opt-in)
- New `price_recorder` service (`src/price_recorder/`), started with the Compose profile `recorder` and `trade.config.price_recorder.enabled`.
- **What is recorded:** the candidates of the Trader's own turbo selection (the same search, 20 per indice and direction), each kept until the end of the day once seen, and the index of each allowed indice.
- **Ten-second bars** of the bid and of the ask (open, high, low, close), built from Saxo's one-second stream and written to PostgreSQL (`price_bar_10s`, one partition per month), with each instrument's direction, knock-out level and issuer in `price_instrument`.
- **Gaps are explicit:** no bar is written for a time when no quote was arriving, and `price_recorder_coverage` says for each ten seconds whether the recorder was running and receiving.
- **It says when it stops:** a Telegram message after five minutes without a bar during trading hours, another when it resumes, and a "Price recorder" line among the services of the admin interface.
- **A database outage does not lose bars at once:** up to an hour of them waits in memory and is written when the database answers again.
- `wataprices --from ... --to ...` exports a period to a compressed CSV file; `wataprices probe ...` says whether Saxo prices an instrument for the account, to choose the one recorded for an index.
- New optional section `trade.config.price_recorder` (see the README). `retention_months` is 0 by default: nothing is ever deleted unless it is set.

#### Prices page in the admin interface
- New **Prices** page (Analysis): the recorded bars of one instrument as a candlestick chart, for today, yesterday, the last seven days or any range.
- **Bars of 10 s, 30 s, 1 min, 5 min, 15 min or 1 h**, of the bid or of the ask. Longer bars are computed by PostgreSQL from the ten-second ones; a chart holds at most 12,000 bars, and the lengths that would exceed it for the period are not offered.
- **Under a turbo, the index it follows**, on the same time axis.
- **What WATA did, on the prices:** entries and exits at the price paid and received (the exit with its result), and the signals received with what the Trader did with them. On the chart of an index, the trades of all its turbos.
- **What is missing is shown as missing:** a short stretch without bars is left empty on the chart rather than closed up, and the times of trading hours for which nothing was recorded are listed with their duration and whether the recorder was not running or no quote was arriving (for periods up to 31 days).
- The figures of the bar under the pointer are written above the chart; the bars are also in a table, with CSV export. The view (instrument, period, bar length, side) is in the address and can be bookmarked.
- When the period includes today, the page reads the data again every 30 seconds.
- **Range bars** ("Range", beside the bar lengths): a bar ends when the price has left a range of a set size, as in TradingView's range charts. The size is typed in ticks (60R) or as a price move, the two fields following each other, stepped with "−" and "+", or left to the page ("Auto", decided once per view so that a day still going is not redrawn with other bars). Built from the ten-second bars of the side shown, for periods up to 31 days; under a turbo, the index at the end of each bar. Each bar is given with when it began, when it ended and how long it lasted. No bar is made up across a night or a recording gap: the bar in progress ends there and the chart leaves an empty slot. The last size used is remembered in the browser, one for the index and one for turbos.
- Charts are drawn with TradingView's Lightweight Charts (Apache License 2.0), whose notice is on the page. It is only loaded with this page.
- Four read-only routes for it: `/api/prices/instruments`, `/api/prices/bars`, `/api/prices/range-bars`, `/api/prices/context`.

#### Strategy backtests, from the command line and in the admin interface
- WATA can **replay a strategy on prices of the past** and say what it would have done with it: which entries the Trader would have taken or refused, and where the Position Monitor would have sold. This is the first part of computing the signals inside WATA. **Nothing live uses it**: TradingView still sends the signals and no service runs the strategy.
- **The strategy** (`kernel_vwap`) is the one of the TradingView script, on one feed: on range bars of the index, a kernel regression crossing the VWAP of the session is an entry. `entry_mode=cross_catchup` adds an entry the script does not have, for a move that began before entries were allowed.
- **WATA's own rules are applied**, read from the configuration: market hours, trade limit, cooldown, day profit cap and loss limit, then stop, take-profit, trailing stop, daily target and end-of-day close, on a turbo modelled from the index. Index points alone say little: with the example configuration a position is sold at −15 %, or as soon as the day reaches +1.7 %.
- **Prices** come from Dukascopy's public tick files (US Tech 100, since 2013), fetched and kept as the ten-second bars the Price Recorder writes: `python -m src.strategy.history.dukascopy`. Files exported by `wataprices` are read the same way.
- **In the admin interface**, a new **Backtests** page (under Analysis): ask for a run with its period, the strategy's parameters and, for that run only, other values of WATA's rules; follow it; read its figures per leverage assumed, its curve, its days, its trades and the entries refused, with CSV export; open **one day as a chart** (range bars, kernel, VWAP and band, entries, exits and refusals); set **up to four runs side by side**; see which days have prices and **fetch more from the page**.
- From a checkout: `python -m src.strategy.backtest --from … --to …`.

#### Light theme for the admin interface
- The interface was dark only. It is now light or dark: it **follows the system setting**, and a button in the top bar and on the login page sets it to always light, to always dark, or back to following the system. The choice is kept in the browser.
- The theme is set before the page is painted, so a load does not flash in the other theme.
- Every page, dialog, menu, toast and chart has both themes. In light, the profit, loss, warning and information colours and the secondary text were given darker steps than their dark counterparts, so that each is readable (4.5:1 at least) on every light surface.
- Dark is unchanged: its pages were compared pixel for pixel before and after.

#### Admin interface without a domain of its own, and from a phone
- The interface can be served on a **second HTTPS port of the API domain** (`https://<API_DOMAIN>:8443/`), with the same certificate and behind the same allowlist: `ADMIN_DOMAIN` set to the API domain, `ADMIN_ENTRYPOINT=adminsecure` and `ADMIN_PORT=0.0.0.0:8443` in `.env`.
- Given the API domain without the other two lines, the interface is not served and the webhook keeps its route: the admin route now has the lowest priority.
- The README describes how to reach it **from an address that changes**, such as a phone on cellular data: a WireGuard tunnel to the server, and the tunnel's range as allowlist.
- Both were run on one server: the second port answers 403 outside the allowlist and serves the interface inside it, and an iPhone signed in through the tunnel, recorded under its VPN address. The device configuration first written in the README never connected; the one given now did. No other device was tried.

---

### Bug Fixes

- **Admin interface, development only:** signing in through the Vite dev server (`npm run dev`) was refused as a cross-origin request, because the proxy rewrote the `Host` header. It is now passed on unchanged. The deployed interface was not affected.

---

### Security

- **Eleven dependencies with known vulnerabilities were upgraded** in `requirements.txt`: the 27 alerts raised by GitHub's Dependabot, and four more recent advisories on the same packages. `anyio` 4.14.2, `cryptography` 50.0.1, `duckdb` 1.1.3, `idna` 3.15, `orjson` 3.11.9, `Pygments` 2.20.0, `python-dotenv` 1.2.2, `python-multipart` 0.0.32, `requests` 2.33.1, `ujson` 5.13.0, `urllib3` 2.8.0. No other installed package changes version.
- WATA's own code uses three of them: `cryptography` (the encrypted Saxo and webhook tokens), `requests` (Saxo token requests, Telegram) and `duckdb` (the old migration tool). **The stored tokens stay readable:** a token file written with the previous versions is read with the new ones, and the reverse, so neither this upgrade nor a return to the previous image invalidates them. A DuckDB file written by 0.10.2 is read by 1.1.3.
- **FastAPI, Starlette and pytest now have a version in `requirements.txt`**, like every other line. Dependabot does not check a line without one, and the versions that were installed, held back by an old `typing_extensions` pin, had six advisories of their own, five of them on Starlette 0.50.0. Now `fastapi` 0.135.1, `starlette` 1.3.1, `typing_extensions` 4.12.2, `pytest` 9.0.3, `pytest-asyncio` 1.4.0 and `pytest-cov` 7.1.0. The public advisory database (OSV) reports nothing on any of the 81 packages installed from the file.
- **Webhook server: the IP allowlist could be skipped with a malformed `Host` header.** This is one of the Starlette advisories, as it applied to WATA: a request to `/webhook` whose `Host` was, for instance, `x/latest-signals?` was taken for one of the internal paths, which are exempt from the IP check. It was still stopped by the token, and Traefik does not forward such a `Host` from the internet, so it concerned a caller reaching the container directly. Closed twice: by Starlette 1.3.1, and by the check now reading the path the router uses.
- The new versions are installed when the image is rebuilt, that is at the next deployment.

---

### Things to Know

- **Backtests run in the admin container**, as a child process at the lowest priority, one at a time, and the container is now limited to **one processor** (`cpus: '1.0'`). They are files under two new folders, `var/lib/backtests` and `var/lib/price_history`, which the installer creates and the admin mounts. `admin.backtests_enabled: false` switches the page off. None of this had been run in Docker or on a server when this entry was written: the interface was driven in a browser against the admin application on a development machine, running real backtests on three days of prices.
- **A backtest's turbo is a model.** Its leverage and its spread are assumptions until they are calibrated on real trades, so a run is made for three leverages and a conclusion should hold for all of them.
- **Dukascopy's prices are another broker's**, from an address it does not document, and its server refuses many requests and stops answering for a while when asked too much: a month takes hours and several downloads. One download at a time.
- **Traefik listens on one more port, 8443, whether the admin interface is used or not.** Only on the server itself (`127.0.0.1`) unless `ADMIN_PORT` says otherwise, so nothing new is reachable from outside. If something on the server already uses that port, Traefik does not start and the webhook is down with it: check with `ss -lnt | grep 8443` before upgrading, and give `ADMIN_PORT` another one if so (`ADMIN_PORT=127.0.0.1:9443`).
- **The recording starts when it is switched on.** Earlier prices cannot be fetched at this resolution.
- **Disk space:** roughly 8 to 14 GB a year for one indice, more on days when the index moves a lot. `max_instruments` bounds it.
- **The index may need naming.** If Saxo does not price the underlying of an indice for the account, the log says so and the instrument to record goes in `price_recorder.instruments`.
- **A delayed feed is recorded as such** (`delayed_minutes`), not corrected.
- **A range bar here is not bar for bar TradingView's.** "1R" is one step of the recorded Saxo prices, which may not be the tick of the symbol you look at in TradingView; what happens inside ten seconds is assumed (low before high when the price rose); and a bar opens where the previous one closed, where TradingView does not say what it does. Close enough to read the same moves, not to count the same bars.

---

## v0.11.0 - Admin Web Interface

**Release date:** not yet released

This release adds an optional web interface for the operator, and the hooks it needs in the Trader and the Position Monitor: a pause of new entries, a journal of what was done with each signal, the live value of open positions, the close of a single position, and a history of the account balance. The interface is a new service that is not started unless enabled. No configuration change is required to upgrade; three new tables are created automatically.

**Verification status:** covered by unit tests of the trading-path changes, of the admin API and of the interface, and by driving the built interface in a browser against the real admin application running over fake data. Nothing in this release had been run in Docker, behind Traefik, or against the real PostgreSQL, RabbitMQ and Saxo when this entry was written. The changes to the Trader and the Position Monitor should be exercised in Saxo's simulation environment before a live deployment.

---

### Behaviour Changes

- **The Trader reads one more value before opening a position:** whether new entries are paused. Like the other rule checks that read the database, a failure of that read means the signal is not traded.
- **The Trader records what it did with each signal** (`signal_journal`): executed, positions closed, refused by which rule, failed with which error, or ignored. It is written in the background, after the decision, and a failure to write it is only logged.
- **The Position Monitor publishes the latest price and performance of open positions** (`position_live_value`) every `streaming.live_value_interval_seconds` (5 s by default, `0` to switch it off). It is a copy made on a timer beside the position checks, never inside them.
- **The Position Monitor accepts a request to close one position** on the `trading-ops` queue (`close_position`). A request older than 60 seconds is ignored and reported on Telegram, so one that waited in the queue while the monitor was down cannot sell anything later. Such a close is recorded with the reason `Explicit Close (position <id>)`.
- **The daily report records the account balance** (`account_balance_history`, one value per day, the latest wins).

---

### New Features

#### Admin Interface (opt-in)
- New `admin` service (`src/admin/`, FastAPI on port 8082) serving a React interface built from `admin_ui/`.
- **Dashboard:** day profit and loss, trades against the daily limit, whether the next signal would be traded and what holds it back (pause, trading hours, closed date, daily limits, cooldown), open positions with their live value and unrealised profit or loss, latest signals, state of each service and of the Saxo connection. Changes are pushed to the page as they happen, with optional browser notifications.
- **On every page:** the top bar says when new entries are paused, when Saxo needs a new authorisation and when the services run an older configuration, each with a link to where it is dealt with.
- **Positions, orders, performance:** history with filters and CSV export; cumulative and daily profit and loss, win rate, long/short and per-instrument figures, read from PostgreSQL and grouped by day in `trade.config.general.timezone`. Unlike the Observable dashboard, it includes the trades recorded since the migration to PostgreSQL.
- **Report:** the figures of the daily Telegram report (profit factor, drawdown, streaks, weeks, months, years, milestone projections), computed by the same code, with the account balance over time.
- **Exits:** results by what closed the trade, by hour of entry and by holding time, and how much of the best level reached was given back.
- **Signals:** latest signal per indice, the journal of what the Trader did with each signal, and a form to send a manual `long` / `short` / `close-long` / `close-short` / `close-position` signal (through the Web Server's `/internal/signal`, so the Trader's rules apply).
- **Watchlist and execution timing:** the turbo that would be bought now per indice and direction; the time the Trader spent on each step of its recent trades.
- **Operator actions:** pause and resume new entries, close one position, hand over the Saxo authorization code from the browser, request the daily report. Each asks for a confirmation, is kept in an audit trail and announced on Telegram. Pausing, which only holds things back, is confirmed with one click; everything that acts on money or re-enables trading is confirmed with a slide.
- **Configuration editor:** a field that does not hold a usable value says so and blocks saving, and leaving the page with unsaved changes asks first. `config.json` shown with secrets masked. Only `trade.rules`, `trade.config`, `reporting` and `logging.level` can be changed, with forms for the common settings (including sizing periods, confidence bands, allowed indices and closed dates) and a JSON view for the rest. A change is validated with `ConfigurationManager`, the previous file is kept, and previous versions can be compared and their trading parameters restored. It takes effect the next time each service starts.
- **System and logs:** RabbitMQ queues, Watchlist Manager snapshot, Saxo token expiry, and the end of each service's log.

#### Pausing new entries
- While paused, the Trader refuses every `long` and `short` signal before closing or opening anything. Closing signals, the end-of-day close and the Position Monitor's stop-loss, take-profit and trailing stop keep working.
- The pause survives restarts (it is stored in `trade_risk_state`) until it is lifted from the interface.

#### Enabling and securing it
- Started only with the Compose profile `admin`: set `COMPOSE_PROFILES=admin`, `ADMIN_DOMAIN` and `ADMIN_ALLOWED_IPS` in `.env`.
- Served by Traefik on its own domain with its own IP allowlist, then a password login (`wataadminpasswd`, at least 12 characters, stored as a scrypt hash). Until a password is set, logins are refused.
- Optional second factor: `wataadmintotp` prints a key for an authenticator app, after which a one-time code is required at login (`wataadmintotp --disable` to remove it).
- The container reads PostgreSQL through connections set to read-only (a safeguard against a query that writes, not a separate database account); the pause of new entries is the one thing it writes, through a separate connection. It mounts `var/lib/saxo_auth`, `var/lib/web_server` and `var/lib/admin` rather than all of `var/lib`, the logs of the other services read-only, and has no Docker socket.
- The configuration editor never writes credentials, infrastructure settings or the `admin` section, when saving or when restoring. The addresses the services connect to and listen on (`trade.config.signal_polling.service_url`, `trade.config.watchlist_manager.service_url`, `api_host`, `api_port`) sit inside the editable sections and are refused as well: they are changed on the server.
- The login lockout and the audit trail use the address reported by the reverse proxy named in `admin.trusted_proxy_hosts` (`["traefik"]` by default). A request reaching the admin port any other way is known by its own address.
- Optional `admin` section in `config.json`; `actions_enabled` and `config_edit_enabled` switch the operator actions and the editor off.

---

### Things to Know

- **Requesting the daily report early changes what is recorded for the day.** The report writes today's row of `trade_performance`, and that row is written once: requested before the last trade, it keeps the figures of that moment. The interface says so before sending.
- **A saved or restored configuration applies at each service's next start,** including when a service restarts by itself after a crash. Until then the running services keep the configuration they started with.
- **Restoring a previous version restores its trading parameters only.** Credentials, infrastructure settings and the `admin` section stay as they are on the server, whatever that version held for them.
- **A manual `long` or `short` signal closes the positions already open,** like a TradingView alert does. The interface says so before sending.
- **The signal journal starts with this version** and lists the signals the Trader received. A signal replaced by a newer one for the same indice before the Trader read it is not listed.
- **The account balance curve starts with the first daily report after the upgrade,** one point per day.
- **Building the package now needs Node.js** (version in `admin_ui/.nvmrc`): `./package.sh` builds the interface before creating the zip. Nothing is built on the server.

---

### Technical Changes

- New tables `position_live_value`, `signal_journal` and `account_balance_history`, created by `init_schema` when the Trader or the Position Monitor starts.
- `TradingRule.check_entries_paused()`; `AsyncDbRiskStateManager` gains `pause_entries`, `resume_entries` and `get_entries_paused_since`.
- `SignalJournal` (`src/trade/signal_journal.py`); `AsyncPerformanceMonitor.live_values()` and the `only_position_id` argument of `close_managed_positions_by_criteria`.
- `PostgresConnectionManager` accepts optional `server_settings`; `SaxoAuth.get_token_status()` describes the stored token without exposing it.
- `deploy/install_package.sh` creates `var/lib/admin` and `var/log/wata-admin` and installs `wataadminpasswd` and `wataadmintotp`.
- CI runs the interface's lint, type-check, tests and build.

---

## v0.10.0 - Exactly-Once Signals, Faster Execution & Resilient Monitoring

**Release date:** 2026-10-11

This release fixes how signals travel from the webhook to the Trader, removes fixed waits from the trade path, keeps the Saxo session ready between signals, and makes the Position Monitor independent of slow callbacks and of the database. No configuration change or database migration is required to upgrade.

**Verification status:** covered by unit tests, a local run of the real Web Server with the Trader's polling loop, and a simulation of the Position Monitor against fake Saxo latencies. The changes had not yet been exercised against Saxo's API when this release was cut. The broker-side protective stop in particular must be verified in Saxo's simulation environment before it is enabled on a live account.

---

### Behaviour Changes

- **Each signal is executed once.** The Web Server keeps the latest signal per `indice` until a newer one replaces it, and the Trader used to process every stored signal again on every poll. It now skips any `signal_uuid` it has already handled. Before this, a stored `long`/`short` stayed executable for `max_signal_age_seconds`, a stored `close-position` would have closed every later position, and the Trader log was flooded (two lines per stored signal every 500 ms).
- **The end-of-day close now works.** In v0.9.0 the Scheduler container did not mount `var/lib`, so it could not read the Web Server token: every `close-position` it sent at `close_position_time` was rejected with `401 Unauthorized`, and positions stayed open overnight and over weekends. After this upgrade, open positions are closed at `close_position_time` every day.
- **A restarted Trader does not replay old signals.** Signals the Web Server received more than `startup_replay_max_age_seconds` (10 s) before the first poll are ignored.
- **The Position Monitor reconciles the database with Saxo less often.** It used to call Saxo's REST API on every price update. It now reconciles when a position leaves the stream and every `reconcile_interval_seconds` (30 s).
- **The loss cooldown also applies to positions closed outside WATA** (for example in the Saxo platform, or by the protective stop) when they closed at a loss.

---

### New Features

#### Long-Polling Signal Delivery
- `/latest-signals` accepts `after=<seq>` and `wait_ms`: the request is held until a newer signal is stored. The Trader receives a signal as soon as the webhook stores it instead of on the next 500 ms poll.
- The Trader falls back to interval polling by itself if the Web Server does not support long-polling.

#### Saxo Session Kept Ready
- The Trader calls Saxo every `keep_warm_interval_seconds` (30 s) between signals. This keeps the HTTP connection open and renews the access token away from the trade path.
- Optional spending-power cache for order sizing (`balance_cache_max_age_seconds`, off by default).

#### Broker-Side Protective Stop (opt-in)
- With `position_management.protective_stop.enabled`, each new position gets a resting sell stop at Saxo, so that a Position Monitor outage is survivable.
- `percent` must be wider than `stoploss_percent`: the Position Monitor's own stop acts first in normal operation.
- The stop is placed after the entry and rounded down to a valid tick. Nothing is sent if the instrument does not list the order type as supported; the trade notification says whether the stop was placed.
- Any order resting on a position is cancelled before WATA closes that position.

---

### Changed

#### Trade Path
- On a reversal, the sell is sent and its bookkeeping (closed-position lookup, database update, notification) runs in the background. In close-then-open mode the Trader waits only until Saxo shows the old position closed, capped at the previous 1.5 s.
- Position confirmation after an order checks immediately instead of sleeping 300 ms first, then backs off within the same overall patience.
- The balance request starts alongside the turbo selection, and the watchlist lookup reuses one HTTP connection.

#### Saxo Authentication
- A renewed access token is swapped into the existing HTTP client instead of rebuilding the client.
- Token handling no longer runs on the event loop, and token requests have a 10 s timeout.

#### Position Monitor
- Reading the stream and checking positions are separate tasks. A slow check or a closure in progress no longer delays reading price updates, and updates that arrive meanwhile are merged so the next check works on current prices.
- Stop-loss, take-profit and trailing-stop sells are sent first; the closure is recorded in the background.
- Position checks keep working when PostgreSQL is slow or unreachable, using the last state read.
- The trailing-stop peak is kept in memory and saved when it rises.

---

### Fixed

- The Scheduler's end-of-day `close-position` never reached the Trader (missing `var/lib` volume on `scheduler1` in `deploy/docker-compose.yml`).
- Trader, Position Monitor and Watchlist Manager could renew the Saxo token at the same time with the same single-use refresh token. Renewal now takes a lock shared by the services, and the token file is written atomically.
- A position with a sell order in flight could be sold a second time.
- A closed position not yet listed by Saxo after 1.5 s was left open in the database; the lookup now retries.
- The database was not reconciled when the last open position left the stream.

---

### Configuration and Deployment

All new settings are optional; `etc/config_example.json` lists them with their defaults.

| Setting | Default | Purpose |
|---------|---------|---------|
| `trade.config.signal_polling.long_poll_wait_ms` | 1000 | Long-poll wait; `0` restores polling every `interval_ms` |
| `trade.config.signal_polling.startup_replay_max_age_seconds` | 10 | Replay window after a Trader restart |
| `trade.config.general.keep_warm_interval_seconds` | 30 | Saxo keep-warm call; `0` disables it |
| `trade.config.general.streaming.reconcile_interval_seconds` | 30 | Period of the safety-net reconciliation |
| `trade.config.buying_power.balance_cache_max_age_seconds` | 0 | Spending-power cache for sizing; `0` asks Saxo on every trade |
| `trade.config.position_management.protective_stop` | disabled | `enabled`, `percent`, `order_type` (default `StopIfTraded`) |

- Deploy the Web Server and the Trader together to get long-polling.
- Use the `deploy/docker-compose.yml` of this release: it adds the `var/lib` volume to `scheduler1`. Do not apply that volume to v0.9.0 on its own, where a delivered `close-position` would be executed again on every poll.
- The Saxo token directory gains a `.token_refresh.lock` file.

---

## v0.9.0 - Async Architecture, PostgreSQL & Streaming

**Release date:** 2026-10-11

This is a cumulative release covering all changes since the last published tag, v0.6.1. It includes the v0.7.0 transition to async services, PostgreSQL, and Saxo WebSocket streaming, along with the subsequent trading, reporting, watchlist, signal-handling, and deployment changes.

---

### v0.7.0 Foundation: Async Architecture, PostgreSQL & Streaming

#### Breaking and Migration Changes

- Replaced embedded DuckDB with PostgreSQL 16 and an async `asyncpg` connection pool. The DuckDB config is no longer used at runtime; `src/database/migration.py` migrates existing data.
- Added the `position_monitor` service. Position checks no longer depend on the scheduler's 7-second REST polling job.
- Split RabbitMQ routing into `trading-signals` for trade signals and `trading-ops` for operational commands.
- Added required `postgresql` and `trade.config.general.streaming` configuration and the PostgreSQL and Position Monitor deployment services. The migration guide below contains the v0.6.x upgrade steps.

#### Async Trading Services

- Moved service-critical paths to `async`/`await`. The Saxo HTTP client uses `httpx.AsyncClient` with HTTP/2, async rate limiting, and token refresh.
- Added async instrument search, order placement, position queries, trading orchestration, and performance monitoring, including parallel searches and bounded-concurrency position checks.
- Reworked the Trader to consume signals with `aio-pika` and added an async Telegram sender.
- Added PostgreSQL managers for orders, positions, and trade performance, with connection pooling and automatic schema creation.

#### Real-Time Saxo WebSocket Streaming

- Added a persistent Saxo streaming client for position subscriptions. It decodes binary and delta-compressed updates, maintains a position snapshot, and handles heartbeats, subscription resets, and disconnects.
- Added automatic reconnect with exponential backoff and message-ID resume, plus periodic WebSocket reauthorization.
- The Position Monitor evaluates streamed positions for stop-loss, take-profit, and trailing-stop rules; scheduled operational events, including daily statistics, use `trading-ops`.

#### v0.7.0 Performance Impact

| Metric | Before (v0.6.x) | After (v0.7.0) |
|--------|-----------------|----------------|
| Position-check latency | Up to 7s (poll interval) | Sub-second (stream push) |
| API calls per check cycle | 3+ REST calls | 0 (data pushed by WebSocket) |
| HTTP protocol | HTTP/1.1 (`requests`) | HTTP/2 (`httpx`) |
| Database | Embedded DuckDB | Pooled PostgreSQL |
| I/O model | Synchronous/blocking | Async (`asyncio`/`uvloop`) |

---

### Additional Changes Since v0.6.1

- Expanded configurable risk-management and trading rules, improved instrument ranking by Bid price and InfoPrices diagnostics, and optimized async turbo-candidate validation.
- Improved PostgreSQL and streaming reliability, including consistent date/datetime normalization.
- Enhanced simulation and reporting, expanded CI and unit-test coverage, and added a trading-dashboard build script.

---

### v0.9.0 Updates

#### Breaking Changes

- **Signal delivery no longer uses the `trading-signals` RabbitMQ queue.** The web server keeps the newest signal per `indice` in memory, and the async Trader polls `/latest-signals` (500 ms by default). Older signals for an `indice` are ignored; only the latest is retained.
- **Legacy synchronous Saxo trading services have been removed.** Integrations must use the async services in `src/trade/async_services.py`.

---

#### New Features

##### Background Turbo Watchlists
- Added a dedicated `watchlist_manager1` service that periodically selects the best turbo candidates for configured indices and directions.
- Candidate prices are refreshed from Saxo's streaming API and cached for the Trader. The manager exposes health and best-candidate endpoints and reports whether cached prices are stale.

##### Secure Webhook Deployment
- Added Traefik as the public webhook ingress, with HTTP-to-HTTPS redirection and automatic Let's Encrypt certificates.
- Added an IP allowlist for TradingView webhook traffic.

##### Trading Reports and Monitoring
- Daily reports now include account balance, milestone projections, closed-trade history, and daily profit history.
- Added signal timing details and an optional early trigger for daily statistics.

---

#### Changed

- Improved async turbo candidate discovery and validation to reduce unnecessary work during instrument selection.
- Improved Saxo streaming and PostgreSQL handling, including consistent normalization of date and datetime values.
- Updated trading simulation and risk-rule logic, and expanded reporting calculations.
- Improved deployment and package-install scripts, HTTP/2 support for the async API client, and configuration handling.

---

#### Configuration and Deployment

- Add the `trade.config.watchlist_manager` and `trade.config.signal_polling` settings from `etc/config_example.json` to the deployment configuration.
- Set `ACME_EMAIL` and `API_DOMAIN` in `deploy/.env` to enable HTTPS certificate issuance for the webhook endpoint.
- Deploy the new `watchlist_manager1` service with the updated Compose configuration.

---

## v0.7.0 - Async Architecture & Real-Time Streaming

**Release date:** 2026-03-29

This is a major architectural release that migrates WATA from a synchronous, polling-based system to a fully asynchronous, real-time streaming architecture. Every service-critical path is now `async/await`, the database backend moves from DuckDB to PostgreSQL, and position monitoring switches from 7-second REST API polling to Saxo's WebSocket Streaming API.

---

### Breaking Changes

- **Database: DuckDB → PostgreSQL.** The embedded DuckDB database is replaced by a PostgreSQL 16 instance. A migration tool is provided (see Migration Guide below). The `duckdb` config section is no longer used at runtime.
- **New service: `position_monitor`.** Position monitoring is now a dedicated container (`WATA_APP_ROLE=position_monitor`) that must be deployed alongside the existing services.
- **Scheduler no longer polls positions.** The `job_check_positions_on_saxo_api` job (every 7 seconds) has been removed. Position checks are now driven by real-time WebSocket streaming in the Position Monitor.
- **RabbitMQ queue split.** Trading signals now go to `trading-signals` (consumed by Trader); operational commands go to `trading-ops` (consumed by Position Monitor). Previously everything went to a single queue.
- **New config sections required.** `postgresql` and `trade.config.general.streaming` must be added to `config.json` (see Migration Guide).

---

### New Features

#### Real-Time WebSocket Streaming (Phase 2)
- **`src/saxo_streaming/client.py`** - New persistent async WebSocket client for Saxo Bank's streaming API.
  - Subscribes to position updates via `POST port/v1/positions/subscriptions`.
  - Parses binary frames using the existing `decode_ws_msg()` utility.
  - Applies delta-compressed updates to an in-memory position snapshot.
  - Handles all Saxo control messages: `_heartbeat`, `_resetsubscriptions`, `_disconnect`.
  - Auto-reconnects with exponential backoff and `messageid` resume.
  - Periodically re-authorises the WebSocket connection (default: every 15 minutes).

#### Async Architecture (Phase 1)
- **`src/saxo_openapi/async_client.py`** - Async HTTP client using `httpx.AsyncClient` with HTTP/2, async rate limiting, and hot-swappable tokens.
- **`src/trade/async_services.py`** - Async reimplementation of all trade services:
  - `AsyncSaxoApiClient` - token-refreshing facade.
  - `AsyncInstrumentService` - parallel turbo search.
  - `AsyncOrderService` - non-blocking order placement.
  - `AsyncPositionService` - concurrent position queries.
  - `AsyncTradingOrchestrator` - parallel instrument search + spending-power fetch.
  - `AsyncPerformanceMonitor` - SL/TP/trailing-stop checks with bounded-concurrency closures.
- **`src/database/postgres.py`** - Full async PostgreSQL layer with connection pooling (`asyncpg`), auto-schema creation, and equivalent managers for orders, positions, and trade performance.
- **`src/mq_telegram/async_tools.py`** - Async Telegram message sender using `aio-pika`.
- **`src/trader/__init__.py`** - Fully async Trader service consuming from `trading-signals` queue via `aio-pika`.
- **`src/position_monitor/__init__.py`** - New dedicated service that combines WebSocket streaming for real-time position monitoring with queue consumption for time-triggered events (`daily_stats`).
- **`src/database/migration.py`** - One-time DuckDB→PostgreSQL migration tool.

---

### Changed

- **`deploy/docker-compose.yml`**
  - Added `postgres1` service (PostgreSQL 16 Alpine) with health checks and volume mapping.
  - Added `position_monitor1` service as a new container.
  - Trader and web server now depend on `postgres1` health check.

- **`src/start_python_script.sh`**
  - Added `position_monitor` and `trader_legacy` cases.
  - The new `trader` role runs the async Trader; `trader_legacy` runs the old sync `main.py`.

- **`src/scheduler/__init__.py`**
  - Removed `job_check_positions_on_saxo_api` (7-second polling loop).
  - Added smart queue routing: `daily_stats` and `check_positions_on_saxo_api` → `trading-ops`; trade signals → `trading-signals`.
  - `job_daily_stats` and `job_close_position` are unchanged.

- **`src/web_server/__init__.py`**
  - Webhook now publishes to `trading-signals` queue instead of the generic queue.

- **`src/trade/async_services.py`**
  - `AsyncPerformanceMonitor` gains `check_positions_from_stream(streamed_positions)` - accepts pre-fetched position data from the WebSocket stream instead of polling the REST API.
  - Internal logic refactored into shared `_evaluate_positions()` used by both stream-fed and REST-polled paths.

- **`etc/config_example.json`**
  - Added `postgresql` section with DSN and pool sizing.
  - Added `trade.config.general.streaming` section with `refresh_rate_ms`, `reconnect_delay_seconds`, `max_reconnect_delay_seconds`, `reauth_interval_seconds`.
  - Existing `websocket` and `position_check` sections kept for backward compatibility.

- **`requirements.txt`**
  - Added: `asyncpg==0.30.0`, `aio-pika==9.5.4`.
  - Already present: `websockets==12.0`, `httpx==0.27.0`, `uvloop==0.19.0`.

---

### Architecture Diagram (Before → After)

**Before (v0.6.x):**
```
TradingView → Webhook → RabbitMQ (single queue) → Trader (sync, requests)
                                                       ↕
Scheduler (every 7s) → RabbitMQ → Trader → Saxo REST API (3+ calls per check)
                                                       ↕
                                                    DuckDB
```

**After (v0.7.0):**
```
TradingView → Webhook → RabbitMQ [trading-signals] → Async Trader (httpx/HTTP2)
                                                           ↕
                                                      PostgreSQL
                                                           ↕
             Saxo WebSocket Stream ←→ Position Monitor (real-time)
                                                           ↕
             Scheduler → RabbitMQ [trading-ops] → Position Monitor (daily_stats)
```

---

### Performance Impact

| Metric | Before (v0.6.x) | After (v0.7.0) |
|--------|-----------------|-----------------|
| Position check latency | Up to 7s (poll interval) | Sub-second (stream push) |
| API calls per check cycle | 3+ REST calls | 0 (data pushed via WebSocket) |
| HTTP protocol | HTTP/1.1 (requests) | HTTP/2 (httpx) |
| Database | DuckDB (embedded, single-writer) | PostgreSQL (pooled, concurrent) |
| Message broker pattern | Single queue | Split queues (signals vs ops) |
| I/O model | Synchronous (blocking) | Fully async (asyncio + uvloop) |

---

# Migration Guide - v0.6.x → v0.7.0

## Prerequisites

- Docker and Docker Compose
- Access to your current `config.json` and DuckDB database file
- A brief maintenance window (services will be restarted)

## Step 1: Set PostgreSQL Password

In `/app/wata/.env` in your server, set a secure postgres password:

```bash
POSTGRES_PASSWORD="your_secure_password_here"
```

## Step 2: Update Configuration

Add the following sections to your `etc/config.json`:

### 2a. PostgreSQL connection

Add at the top level (next to `duckdb`):

```json
"postgresql": {
  "dsn": "postgresql://wata:YOUR_PASSWORD@postgres1:5432/wata",
  "pool_min_size": 2,
  "pool_max_size": 10
}
```

> **Important:** Replace `YOUR_PASSWORD` with a strong password. Set the same password as the `POSTGRES_PASSWORD` environment variable in `/app/wata/.env`.

### 2b. Streaming configuration

Add inside `trade.config.general`:

```json
"streaming": {
  "refresh_rate_ms": 1000,
  "reconnect_delay_seconds": 1.0,
  "max_reconnect_delay_seconds": 30.0,
  "reauth_interval_seconds": 900
}
```

| Key | Description | Default |
|-----|-------------|---------|
| `refresh_rate_ms` | How often Saxo pushes position updates (milliseconds) | `1000` |
| `reconnect_delay_seconds` | Initial delay before reconnecting after a WS drop | `1.0` |
| `max_reconnect_delay_seconds` | Cap for exponential-backoff reconnection | `30.0` |
| `reauth_interval_seconds` | How often to re-authorise the WS connection | `900` (15 min) |

### 2c. Keep existing sections

The `duckdb`, `websocket`, and `position_check` config sections can remain - they are not used by the new services but won't cause errors.

## Step 3: Rebuild the Docker Image

```bash
./docker_build.sh
```

This rebuilds `wata-base:latest` with the new code and dependencies.

## Step 4: Start PostgreSQL First

```bash
cd deploy
docker compose up -d postgres1
```

Wait for it to be healthy:

```bash
docker compose ps  # should show postgres1 as "healthy"
```

## Step 5: Migrate Data from DuckDB

Run the migration tool from inside a container:

```bash
docker compose run --rm \
  -e WATA_APP_ROLE=trader \
  -e WATA_CONFIG_PATH=/app/etc/config.json \
  trader1 \
  python -m src.database.migration
```

This reads all data from your existing DuckDB file and inserts it into PostgreSQL. The tool is idempotent - running it twice won't duplicate data.

Verify the migration:

```bash
docker compose exec postgres1 psql -U wata -c "SELECT count(*) FROM turbo_data_order;"
docker compose exec postgres1 psql -U wata -c "SELECT count(*) FROM turbo_data_position;"
```

## Step 6: Deploy All Services

```bash
docker compose up -d
```

This starts all services including the new `position_monitor1` container.

## Step 7: Verify

1. **Check logs** for the Position Monitor:
   ```bash
   docker compose logs -f position_monitor1
   ```
   You should see:
   ```
   WebSocket connected.
   Position subscription created (refId=pos-xxxx). Snapshot: N positions.
   ```

2. **Check Telegram** - you should receive:
   ```
   WATA Position Monitor vX.X.X is running (WebSocket streaming + trading-ops queue).
   ```

3. **Verify the scheduler** no longer polls:
   ```bash
   docker compose logs scheduler1 | grep check_positions
   ```
   Should show no new `check_positions_on_saxo_api` messages.

4. **Test a webhook** to verify the full signal flow still works end-to-end.

## Rollback

If you need to roll back:

1. Revert to the previous Docker image (tag your images before upgrading).
2. The DuckDB database file is untouched - the migration tool only reads from it.
3. Remove the `postgresql` and `streaming` config sections from `config.json`.
4. Bring down the new containers: `docker compose down`.
5. Restart with the old image.

## New Service Map

| Container | Role | Queue | Purpose |
|-----------|------|-------|---------|
| `web_server1` | web_server | publishes to `trading-signals` | Webhook endpoint |
| `trader1` | trader | consumes `trading-signals` | Executes trades |
| `position_monitor1` | position_monitor | WebSocket + consumes `trading-ops` | Real-time SL/TP/trailing-stop + daily stats |
| `scheduler1` | scheduler | publishes to `trading-ops` / `trading-signals` | Timed events (daily_stats, close-position) |
| `telegram1` | telegram | consumes `telegram` queue | Notifications |
| `rabbitmq1` | - | - | Message broker |
| `postgres1` | - | - | Database |
