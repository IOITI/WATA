# Changelog

## v0.61.0 - A deploy restarts only what a release changes

**Release date:** not yet released

A deploy stopped everything. The playbook and the install each ran `docker compose down`: the 15 containers stopped, Traefik, PostgreSQL and RabbitMQ included, the image was built while nothing ran, and `watastart` brought it all back. That was one to five minutes without trading, without TradingView's webhook, and without Saxo's and Capital.com's bars, which are lost for good; the TA engine also loaded its rules again. So every release waited for the evening, or was forced. Yet most releases change one corner: v0.60.0, the AI agent's memory, changes the admin alone.

Now a release is installed beside the one that runs. Each container is recreated only if the release changes what it runs, and only when nothing holds it back. The others keep running, untouched, and what is held back waits until someone applies it.

- **Installing stops nothing.** `install_package.sh` builds `wata-base:<version>` and `wata-backup:<version>` beside the running images, and builds nothing when that version is built already with the same content. It then checks the image (`src/release/check.py`): every service loads, the libraries are `requirements.txt`'s, the services' validators accept the server's `config.json`, and the Pine runner runs, confined. Until now these checks were run by hand before each deploy. Only then is the release named the newest (`/app/wata/VERSION`). A release whose image fails leaves the server as it was. `docker_build.sh` now stops at a failed build (`set -euo pipefail`); before, the backup image's build hid a failed build of WATA's.
- **Each container runs the image its pin names** (`/app/wata/release/images.env`, read after `.env` by every compose command). Only `watadeploy` moves a pin, recreating that container alone. `watastart` starts what is not running and recreates nothing. The first install pins each running container to the image it runs, so the switch recreates nothing.
- **What a release changes is read inside the images** (`src/release/manifest.py`). A container's code is every file its service can load: its imports, inside functions too, and the files they read (the calendars, the Pine runner, the interface). Its libraries are the packages those imports need, so a library bumped by Dependabot recreates only the containers that import it. Python files are compared as syntax trees, so a change of comments or layout recreates nothing. Python, Node or the system's packages changing alone makes an optional change. Measured on the last releases: v0.60.0, v0.59.0 and v0.58.0 change only the admin, and v0.47.0 the admin and the TA engine; most others change code every service shares.
- **What holds a container back** is what holds a restart back from the System page, moved unchanged to `src/release/guards.py`, where the server's own python3 reads it:
  - the trading services wait for the market to close by WATA's hours, with no position open;
  - the TA engine waits for the close while a rule is live;
  - the admin waits for its backtests, studies and AI agent requests;
  - new for a deploy: the price recorder and the Capital.com feed wait for the market to close, since the bars they would miss are never fetched again.

  A fact that cannot be read holds back; the playbook's checks of before let a deploy through when the database could not be read. Containers that change because of the same file move together: the admin never runs a newer `src/admin/ta_rules.py` than the TA engine.
- **Applying** (`watadeploy apply`, `src/release/host.py`):
  1. The database is dumped first.
  2. One container at a time, the backups first and the admin last, each once the one before is at work again. "At work" uses the criteria of the admin's restarts, read from the server.
  3. A container that does not stay up is put back on its image of before, and the rest waits.

  The history is kept in `/app/wata/release/history.jsonl`. `watadeploy rollback` puts the last apply's containers back. `watadeploy full` stops and starts everything on the newest release, for a change of Traefik, PostgreSQL or RabbitMQ. `watadeploy prune` removes older releases' folders and images, keeping three and every one still named.
- **System › Release**:
  - the newest release, each container's release (also a column of the containers), what changes for those that wait, and what holds each back now;
  - **Apply**, or **Apply anyway**, recorded in the audit trail and announced on Telegram.

  The admin only writes a request into its own folder. A systemd path unit on the server hands it to `watadeploy`, which takes it out of the admin's reach, accepts only the newest release and containers that wait for it, and applies it with the same guards. The admin holds neither Docker, nor compose, nor the pins. An apply's outcome is said once, on Telegram and to the open pages. Once the market has closed, what still waits is said once a day. The top bar shows *Release waiting*, then *Deploying*. The admin's own restarts wait while an apply runs.
- **Different releases side by side, kept safe.** Each time `config.json` changes, the server checks that every image in use still starts on it. An image that would not is said on the System page and on Telegram, before its container restarts on it.
- **The server's commands run on the newest release** (`watatool`): `watasaxoauth`, `watawebtoken`, the admin's password and MCP tokens, `wataprices` and `watavault export-saxo`. Each runs in a container of its own, out of Traefik's routes. With `docker exec` they ran inside a service, on that service's image. They also brought their files into its code: `watawebtoken` brought the database module into the web server's, so every schema change would have recreated the webhook.
- **The playbook** installs, prints the plan, and applies what may go now:
  - `-e apply=none` installs only;
  - `-e services=…` applies those containers;
  - `-e force=true` applies whatever holds them back;
  - `-e full=true` stops and starts everything.

  The backups' script moved into their image: no container mounts a file of a release's folder any more.
- Claude's `status` (the MCP endpoint) gives each container's release.

**On the live path:** how a release reaches the trading services, not what they do. They are recreated only when their code, libraries or compose definition change, at a closed market with no position open, unless forced. The guards moved to `src/release/guards.py` unchanged; zoneinfo replaces pytz there, with the same answers minute by minute around the 2026 clock changes (tested).

**Backtests:** no file of the code digest changed (9c7a409571b614e5).

**Deploying it:** this release carries v0.60.0 to the server.
1. Install it with `-e apply=none` first, with trading running: it builds the image and pins the running containers to the 0.59.1 image they run.
2. Read the plan. Only admin1 (v0.60.0's code and this release's) and pg_backup1 (its script now in its image) should change.
3. Apply them: neither trades nor records, so the market may be open.

**Verification status:**
- 3,475 Python tests pass (23 skipped: the live database and queue, which CI runs). 92 of them are in five new files.
  - The guards: the same answers as before, minute by minute around the 2026 clock changes; standard library only, Python 3.10.
  - The fingerprints: closure rules on a made-up tree. On the real code: the trading services never reach the admin or the backtests, v0.60.0's files reach the admin alone, and every data file, dynamic import and command is declared. What each entry loads at import stays inside its closure.
  - The plan: groups, held back, optional, infrastructure, order.
  - The server's side, over a Docker made up in memory: first pins, an admin-only release with the market open, a group held back, what cannot be read holding back, a container put back, rollback, the lock, the admin's requests (out of bounds, a link, a stranger), prune, the config check.
  - The admin's routes and announcements.
  - The deploy files: one image per container's pin, no file of a release's folder mounted, `docker_build.sh` run against a stand-in `docker` (built once, again only when its content changed, a failed build failing it, the backup image sent its two files alone), the install's order, the systemd units, the commands on the newest release.
- The image check run here against the checkout: every check passes, the Pine runner included. The fingerprints of v0.59.1 and of this release: only admin1's code differs.
- 1,202 interface tests pass (16 new: the release's model, the panel and its dialog, the live event); lint, types and the build are clean.
- Seen in the browser pane on the development admin (a made-up release whose apply plays out step by step): the panel, the containers' release column, the top bar's chip, a group held back applied anyway with the slide, its progress, its end, the audit and Telegram lines.
- Not seen against real Docker or on the server: CI's new step runs the compose mechanics on Docker (two env files, one container recreated alone, a start that recreates nothing, a command out of Traefik's routes). The first install will be the first real run.

## v0.60.0 - The AI agent keeps a memory between its conversations

**Release date:** 2026-10-29

Each conversation of the AI agent started from nothing: what one learned (a unit trap, a strategy without edge, how the operator wants answers) was lost for the next, and a research task's conclusion stayed inside its own conversation. The agent now keeps entries of a memory it reads and changes itself, which the operator reads and manages on a page of their own, and which Claude Code reads too.

- **Entries, one thing each** (`src/admin/agent_memory.py`): a name, a kind (a preference of the operator, a lesson about WATA, its data and its tools, a finding of a study with the runs, trades and days it rests on, a reference), a line that says when it matters, its text in Markdown. Up to 200, 4,000 characters each. Text that looks like a secret (the shapes the logs are cleaned of, and the values registered as secrets) is refused, whoever writes it: the memory goes to Anthropic with every conversation.
- **What the agent is told**: a conversation opens with the list of the entries, a line each, and each later request with what the operator or another conversation changed since; it reads an entry whole when it bears on the work (`memory_list`, `memory_read`). The system prompt says what to keep and what never to (a request's progress, a tool's output, a secret, an instruction found in what it read), and that a locked entry is the operator's. None of it is in the system prompt itself, which stays the same from one request to the next: its cache and the thinking bound to it are kept.
- **Its own changes** (`memory_save`, `memory_forget`) apply at once: a change of an entry needs the revision its read gave, so that nothing written since is lost unread. Each change is kept in the memory's history with the entry before and after it, who made it and in which conversation. A save is a step of its request, said under its answer, and an artefact.
- **AI agent › Memory**: every entry with who wrote it, when it was changed and read last (by the agent, or by Claude Code); write one (named from its line until a name is typed), change it, rename it, lock it, forget it (with an Undo), download them all as a file. **Locked**, an entry is read and never changed or forgotten by the agent: the operator's start locked unless they untick it. **Pause its changes** lets the agent read its memory and change nothing, at once, until resumed. **History** keeps each change and brings back any version, or an entry forgotten, as whoever wrote it. ⌘K finds the entries.
- **Claude Code reads it**: `memory_list` and `memory_read` on the MCP endpoint, read only (43 tools); its instructions say to read it before a study.
- **Configuration › AI agent › Memory**: `admin.ai_agent.memory`, on unless false. Off, the agent neither reads nor changes it, its entries are kept, and Claude Code still reads them.
- The memory is kept in files beside the conversations (`admin.persistant.agent_path`/memory), outside the database's backups: the page's Download is the copy to keep.

**On the live path:** nothing. The admin's agent, its MCP endpoint and their pages alone.

**Backtests:** no file of the code digest changed (9c7a409571b614e5).

**Verification status:**
- 3,376 Python tests pass (3 skipped; the live database and queue left out of this run, as CI runs them), 18 of them new: the store (a change from the revision read, a secret refused, the lock and the pause the agent never goes past, the bound on entries, a rename, a version and a forgotten entry brought back as whoever wrote it, a read noted without a change, old history pruned), the agent's tools beside the endpoint's reads, the list first at a conversation's start and only others' changes later, the memory's rules in the system prompt only with its tools, the page's routes, and the endpoint's reads with a store whose changes are tripwires.
- 1,186 interface tests pass (12 new: the page and its dialog, the model of its sentences and checks; the steps' titles added to the model's); lint, types and the build are clean.
- Seen in the browser pane on the development admin with its made-up Claude (`FAKE_AI=1`): a run shared, the agent read two entries of its list, saved a finding and said so under its answer, its link opening the entry; an entry written from the dialog, locked by default; a key-shaped text refused with its sentence; an entry forgotten, its Undo, forgotten again, brought back from the history; the pause, under which the agent's save was refused and its next conversation was told so; the page at 375 px in dark and light (nothing wider than the page), and the switch on Configuration › AI agent.
- Against the real Claude API (a temporary key, $0.0055 in all): the token count took the system prompt with the memory's rules and the 43 tools as they are sent (1,982 and 15,044 tokens, on Haiku 5.5 and Opus 5.5 alike). On Haiku 5.5, asked to remember how to answer, the agent saved a preference with a fitting name and line and linked it in its answer; a new conversation opened with it in its list, read it and answered from it; once the entry was locked, asked to change it, it read it, changed nothing and said how the operator could.

## v0.59.1 - Saxo's account keys kept out of the logs

**Release date:** 2026-10-29

At each start the trader wrote Saxo's account key and client key in its log, `Account: <key>, Client: <key>`. The cleaning of log lines knew those keys only in an address's query (`AccountKey=`), and no service registered their values: the line stayed in the trader's log files on the server and was shown on the admin's Logs page. Seen while deploying v0.59.0, whose read of the trader's log printed them.

- **Registered where they are read**: the trader's helper, which the position monitor and the price recorder use too, and the watchlist manager's own register both keys (`register_secret`) as soon as Saxo gives them: no log line of those services can write them, whatever names them (an order's body, an error, a message).
- **The trader's start line** no longer names them: "Saxo account read (its keys are kept out of the logs)".
- **The lines written before**: the shapes cleaned on every line, and on the Logs page, now include `Account: <key>, Client: <key>` (a padded base64 value after the word, never a plain word) and `AccountKey: …` / `"ClientKey": "…"` in a body or a message. The admin never reads Saxo's keys, so only a shape cleans what its Logs page shows of the files of before. The files themselves keep those lines until they rotate away.

**On the live path:** the trader's start line, and the registration of two values in the trader, the position monitor, the price recorder and the watchlist manager; nothing that trades changes.

**Backtests:** no file of the code digest changed (9c7a409571b614e5).

**Verification status:**
- 3,335 Python tests pass (38 skipped: the live database and queue, and the Pine runner's, Node 24 not on that run's path; CI runs them), ten of them new (keys made up, in Saxo's shape): the keys read by either helper masked wherever a line names them, the trader's line of before written as `Account: ***, Client: ***`, the new shapes masked, and a word after "Account:" or "Client:" left as it is.

## v0.59.0 - Days on paper made with older backtest code are made again

**Release date:** 2026-10-29

Paper trading showed a rule's 23 October as +0.51 % in 6 trades on its days on paper, and -11.30 % in 28 trades in its report. Both replay the same 33 signals at the same moments under the same settings: the day on paper was made that evening by v0.33.0, before v0.38.0 priced fills at Saxo's next quote and judged a new position only on a fresh bid; the report is made each night with the server's code. With the old fills the first five trades lost 3.7 % together and the sixth reached the day's profit target (0.5 %) at 14:00, which stopped the day; with today's they lost 7.3 %, the day never came back to +0.5 %, and the rule went on trading an afternoon of back and forth. A day on paper was made once and never again, and the page did not say it was made with older code: the go-live check read it too.

- **Made again**: once the market is closed, paper trading makes again each kept day on paper whose run was made with other backtest code than the server's (stale, as its run's page says), after the due days, the reports and the pruning: newest first, five a tick (every quarter of an hour), and only while no other run waits, so that it never holds up the operator's runs or a report. The old run is removed once the new one has its place. The rules in shadow and the signals WATA received are made again; a rule out of shadow keeps its days as they were made, and a run that did not finish, or that names no code, is left as it is.
- **Said until then**: "Older code" beside the day, in a rule's days on paper and in the paper days of the signals received; its hint names the version that made it, and that it is made again once the market is closed. The days of `GET /api/backtests/paper` (and so Claude's `rule_list`) say `stale` and `made_with`; those of `GET /api/backtests/paper/live`, `paper_stale` and `paper_made_with`.
- **Only the code**: a change of the settings makes nothing again. A day keeps the settings of its night (the signals received are set beside the real day, which was traded under them); the report is made with tonight's.

**On the live path:** nothing. The admin's paper trading task and its pages alone.

**Backtests:** no file of the code digest changed (9c7a409571b614e5): every run made since v0.50.1 stays current. On the server, the days on paper of 21, 22, 23 and 26 October are made again the first time the market is closed after the deploy.

**Verification status:**
- Diagnosed on the server through Claude's MCP endpoint (read-only): the paper run of 23 October (v0.33.0, code 4da7412060b546c7, `stale`) and the report (v0.58.0) traded the same signals at the same moments; their settings differed only by `fill_price: next` and `wait_for_fresh_quote`, which did not exist on 23 October.
- 3,339 Python tests pass (24 skipped: the live database and queue), six of them new in `tests/test_admin_paper.py`: a day made with older code made again once the market is closed and not while it is open, its old folder gone; newest first, a few a tick, nothing while another run waits; a full queue leaving the rest for the next tick; a day made with today's code, naming none or not finished left as it is; a rule out of shadow keeping its days; a day of the signals received made again under the live settings.
- 1,174 interface tests pass (two more: the hint's sentence; both tables marking the day made with older code alone, its hint read by screen readers); lint, types and the build are clean.
- Seen in the browser pane on the development admin, two of its paper days given v0.33.0's digest: "Older code" and its hint beside each, at the pane's width and at 375 px (nothing wider than the page, both tables scrolling in their frames); the admin restarted, its first tick made those two days again (new runs, the old folders removed, the four others untouched) and the marks were gone.

## v0.58.0 - An accent and a material for the admin, chosen on its Configuration page

**Release date:** 2026-10-28

The admin had one look, neutral and flat. ioiti-ui, the design it is made of, has since offered accents and a glass material, which the admin left out.

- **Configuration › Accent and material** (the page's overview): ioiti-ui's picker. An accent (violet, indigo, blue or bloom) colours what is chosen, current or primary, the links and the focus ring; glass turns the panels, the bars and what floats over the page to glass, over washes of colour. Each browser's own, kept beside the light or dark theme and set before the first paint as it is: nothing of it is in `config.json`, nothing to save, nothing sent. Neutral and flat, the admin as it was, until another is chosen.
- What the admin draws itself follows them as ioiti-ui's components do: its cards on the page (the groups of the configuration, a rule's block, the logs, a script's problems, a library's note, an AI agent's proposal, the sign-in) are glass under glass; the group shown in the configuration, the conversation open in the AI agent's sheet, the chosen source of a new backtest and a run's progress take the accent; a conversation's composer is glass over what scrolls under it.
- On ioiti-ui 0.5.4. Since 0.5.3 a field that takes text no longer has a focus ring (it was drawn at every click into one); its border shows its focus. The Charts page's Length and R fields and the Pine editor, whose focus only the ring showed, take the border of a field in focus (the editor's frame). Since 0.5.4 what a page fades in as it arrives (`QueryState`) is no layer of its own once it has arrived: it held the picker's choices under the panel after it.

**On the live path:** nothing. The admin's interface alone; `package.sh`'s `npm ci` fetches ioiti-ui 0.5.4 from GitHub as it fetched 0.5.2, once its tag is pushed.

**Backtests:** no file of the code digest changed.

**Verification status:**
- 1,172 interface tests pass (one more: the panel's choices shown at once and kept beside the theme, under `wata.theme`, with nothing sent and no unsaved change); lint, types and the build are clean. No Python file changed; the Python tests were not run.
- Seen in the browser pane on the development admin, at the pane's width (910 px) and at 375 px, in the light and the dark theme: the panel and its picker (at 375 px its choices stay inside the screen, nothing wider than the page); violet, bloom and glass on the Configuration overview and a group, Logs, Positions, a new backtest's strategy step, a script's editor and its problems, a conversation and its composer, the AI agent's sheet, the sign-in page; the Charts page's Length field and the Pine editor in focus (a violet border, no ring); Bloom's grain served from the admin's own origin under its Content-Security-Policy; the choice kept through a reload.
- Found in the browser and fixed in ioiti-ui (0.5.4): the page's reveal (`QueryState`) left each block it faded in a layer of its own and the root of its glass's backdrop, which held the picker's choices under the panel after it and kept their glass from blurring it. Seen again on 0.5.4, the panel fading in with the others: nothing of the fade left once it had run, the choices above the next panel and their glass blurring it.

## v0.57.0 - The AI agent works unattended: research tasks and a daily debrief

**Release date:** 2026-10-28

A conversation stopped at the end of each request: a study of several hours meant coming back to ask "go on" each time, and the account of a day had to be asked for. The agent can now be left a goal with a budget, and write each trading day's account by itself.

- **Research tasks** (AI agent › New research task): a goal, and a budget of dollars, backtests and hours, each at most what the settings allow (`max_task_usd` $20, `max_task_backtests` 40, `max_task_hours` 12 by default). The agent works on it alone: once one of its requests ends, the next starts with what is spent and what is left, each request within its own limits and what is left of the task's, until the agent says it is done (`task_done`: its conclusion, the runs it kept, what to try next), its dollars or hours are spent, it made 30 requests, it is stopped, or the agent is paused or out of its month. Its conversation shows what it spent of each, how it ended and its conclusion, and **Stop the task**; its next requests are said in a line, as the admin's. The bell says when it ends, not at each request. A restart of the admin between two of its requests asks the next.
- **The daily debrief** (`debrief_enabled`, off by default): each trading day, once the market is closed by the live rules, the agent writes an account of the day (its trades, its signals, what Saxo refused, the day traded on paper) with `debrief_model` (Sonnet 5.5) at `debrief_effort` (medium), within `debrief_max_usd` ($0.50) and without a backtest, kept as a conversation marked "Daily debrief" on Activity; its first lines go to Telegram (`debrief_telegram`). A day without a signal, a trade or a request to Saxo is noted without a call to the API. The pause, a missing key or a spent month hold it back, and it is written once they no longer do.
- **Configuration › AI agent** has a "Research tasks" block and a "Daily debrief" block.
- On ioiti-ui 0.5.2: the composer's controls (the model, the effort, the context window) wrap where they do not fit beside the send button, rather than run under it on a phone. The composer of a conversation stays above what scrolls under it (the steps' marks showed through).

**On the live path:** nothing. The admin alone: the debrief reads through the admin's read-only pool and says its lines through the admin's own Telegram sender, as paper trading's drift is said. `package.sh`'s `npm ci` fetches ioiti-ui 0.5.2 from GitHub as it fetched 0.5.1.

**Backtests:** no file of the code digest changed.

**Verification status** (v0.55.0 to v0.57.0 were built and checked together):
- 3,334 Python tests pass on Python 3.12 (3.14 not run here), the Pine runner's included; 23 are skipped (the live infrastructure). New for this release: a task going on request after request until `task_done`, each request within what is left (its prompt and limits as sent), ending once its dollars or hours are spent, stopped by the operator, ended by the pause between two requests, taken up again after a restart between two requests, its budget bounded by the settings; `task_done` refused outside a task; the routes; the debrief's day (today once its hours are over, else the weekday before), written once the market is closed and once only, a quiet day without a call, held back and asked again, said on Telegram once (or not, when off), one that could not be written said so, its request's own cost and no backtest while a question after it has the usual limits, and its wait for the agent's tools; the settings' defaults and bounds.
- 1,171 interface tests pass (10 more than v0.54.0's), lint, types and the build are clean: a task started from its form and followed (its figures, the admin's next request said in a line, its stop), the live event said when a task ends and not at each request, the shares a task spent.
- Seen in the browser pane on the development admin with `FAKE_AI=1` (answers made up, the rest real): a research task started from the form, its two requests, `task_done`, its conclusion in the panel and one toast in the bell; the last finished weekday debriefed at the start, its title, Sonnet 5.5 at medium effort, and its Telegram line printed about a minute after it ended, its Markdown taken out; 375 px in the dark theme and the light theme, nothing wider than the page.
- Found in the browser and fixed before this entry: the debrief ran before the agent's tools had started and waited a quarter of an hour (it waits for them now); its conversation was titled with its prompt; the steps of the new tools showed their raw names; on a phone the composer's context button ran under the send button (ioiti-ui's row of controls did not wrap: fixed there, in 0.5.2, its gallery's composer seen wrapping at 375 px and on one line at 1,280, then in WATA's conversations and Activity at 375 px in both themes); in a conversation, the marks of its steps showed through the composer as they scrolled under it (it is above them now).
- **Against the real Claude API**, with a test key, on the development admin's made-up data (about $0.18 in all): the four models available; a new conversation counted with its 41 tools (the system prompt 1,563 tokens, the tools 14,771), so every new tool's schema is one the API takes; an incident on Haiku 5.5 (it called `ops_health` and named the warning), a day on Haiku 5.5 (it read the shared trades, signals and Saxo's 37 refusals right); a run made, then a change of settings proposed on it by Haiku 5.5 (the stop from −15 % to −12 %, resting on that run) and previewed by the editor, nothing saved; a research task on Haiku 5.5 that listed the runs and called `task_done` in its first request; the debrief of Tue 27 Oct on Sonnet 5.5 at medium ($0.17), titled, its Telegram line cut at a line's end with its Markdown taken out.
- Found against the API and fixed: the debrief said it had not looked at the day traded on paper, which no tool gives. A day is now shared with its paper run as the Performance page reads it (its result, its trades, the live reference's range); checked again on Haiku 5.5, which set the day beside it.
- **Not seen:** a debrief at a real market close (the development admin's market is closed at any hour) and on Telegram itself (its line was captured), the server, Python 3.14.

## v0.56.0 - The AI agent's proposals reach further: settings, new scripts, its own shadow rules

**Release date:** 2026-10-28

The agent could propose a new version of a script and a change to a rule that is not live. What a backtest most often finds, a better stop or fewer entries a day, it could only say in words.

- **A change of trading settings in `config.json`** (`propose_settings_change`): the settings a backtest reads from it (the entries, the day, the exits, the turbo, the sizing), each checked as a run checks it and within the bounds of the Configuration page, resting on finished runs whose figures the card shows (hover a run for its peek). **Preview and save** shows what the Configuration page would show of that save (each key from its value to the new one, the services it restarts, what works against what), then saves it with a slide: kept as a backup, written in the configuration's history as "saved from an AI agent's proposal" and in the audit (log, file, Telegram), applied at each service's next start, restarted from System. Only while configuration editing is on; a file saved since its preview refuses it (409); a rule removed from the file meanwhile is refused rather than added. Whether the window around US releases is on and its events, the hours' timezone and the TA engine's days stay the operator's alone: it says what it would set them to.
- **A new script of yours** (`propose_new_script`), an indicator or a strategy, from one the agent saved and compiled: the card shows it whole; **Create the script** makes it yours under the name proposed, its first version marked as written by Claude, and leads to it.
- **Its own shadow rules**: `rule_shadow_save`, `rule_shadow_remove` and `rule_set_reference`, as Claude Code has them: only once Claude's shadow rules are switched on (`admin.mcp.shadow_rules`), within `max_shadow_rules` and `shadow_day_ms`, never live. A rule it saves is run first on its history (one study at a time) and listed in Artefacts.

**On the live path:** a change of settings applied from a proposal is a save of `config.json`, the Configuration page's own, at the operator's slide; it applies at the services' next start. Nothing else.

**Backtests:** no file of the code digest changed.

**Verification status** (with v0.57.0's, which gives the counts):
- New tests: a change of settings recorded where `config.json` keeps it, with the runs it rests on and what was already the live value; each refusal (a setting a run does not know, the backtest's own, the operator's alone, a release's minutes while its rule is off, a run's bounds, `config.json`'s narrower bounds and whole numbers, nothing changed, a setting named twice, runs missing, unknown or unfinished, backtests off); applied only through the editor, a rule removed meanwhile never added back; every proposable setting one the Configuration page may change; the routes: a preview that writes nothing, a save without a revision or with a stale one refused, the save, its history and its audit, config editing off; a new script created as the operator's, its first version Claude's, and each refusal (from the operator's own, a library, a name taken, then or meanwhile); the shadow-rule tools through the agent, refused until switched on, never live, a rule saved as an artefact; `src/admin/agent/` never imports the editor.
- Interface: the settings card (its list, its runs and their figures, the preview's dialog and its slide), the new script's card (its source, Create the script), the step titles of the new tools.
- Seen in the browser pane with `FAKE_AI=1`: a run asked about, its stress test run and waited for, a tighter stop proposed, previewed (the key, the position monitor to restart, the standing warnings) and saved with the slide: the backup, "Restart needed", the configuration's history ("saved from an AI agent's proposal") and the audit's Telegram line; a script asked about, its three proposals, the new script shown and created (the operator's, its version 1 marked Claude's); both cards at 375 px in the dark theme.

## v0.55.0 - Asking the AI agent why: positions, signals, days and incidents

**Release date:** 2026-10-28

The agent read runs and scripts. Why a position ended so, why a signal was refused, what Saxo answered or why a service went down meant reading several pages, and the logs on the server.

- **"Ask the AI agent"** beside the live trading: a position (Positions, its row opened), a run of refusals (Orders: "Ask the AI agent why"), a row of the signal journal (Signals), a day (Report: "Ask about the day") and an entry of the health that is not OK (System). Each is shared whole at the conversation's start: a position with its orders and Saxo's answers, a signal with its row of the journal and the position it opened, a day with its trades, its signals, what Saxo refused and how it traded on paper, an incident with the health and its service's log around the moment. The panel opens beside the list, its subject in the address (`?agent=new&about=position:5001`), with requests to start from (for a position: "Why did it end so?", "Was the fill fair?", "What did Saxo refuse?"; for a day: "How did it go?", "What went wrong?"; for an incident: "What went wrong?", whose prompt ends "Act on nothing.", and "Is it still going on?").
- **Two research tools**, the MCP endpoint's too: `data_position` (a position whole: its orders in time order, Saxo's answer to each request, its refusal runs) and `data_orders` (a period's requests to Saxo as runs of refusals, counted by code and by hour). They read through named queries that never select a request's or an answer's body.
- **The admin's own state, read**: the agent reads the health as System shows it, and, unless `admin.ai_agent.read_logs` is false, a service's log lines around a moment (300 lines and an hour at most), cleaned of secrets as the Logs page shows them. The lines go to Anthropic with its request, which the setting says. Never a service itself.

**On the live path:** nothing. The admin, its interface and the docs alone.

**Backtests:** no file of the code digest changed.

**Verification status** (with v0.57.0's, which gives the counts):
- New tests: the two research tools' shapes (no body of a request or an answer, the lock box refused, an unknown position), their named queries' SQL, their place in the endpoint's exact tool list and its import allowlist; the health and the log lines as the agent reads them (trimmed, its window, the newest lines kept, cut when long), and the health alone while `read_logs` is off; each subject named when asked and refused when it cannot be (no position, a signal without its day or not on it, a day or a moment mistyped, a kind unknown), and shared as the tools read it.
- Interface: the subject carried in the address and the panel opened beside a list on it, the requests to start from.
- Seen in the browser pane with `FAKE_AI=1`: a position asked about from Positions and an incident from System, each read whole at the conversation's start (the position through `data_position`, the incident through the health and its service's log), the health read again by the agent.

## v0.54.0 - An AI agent in the admin: Claude on a run or a script, from their pages

**Release date:** 2026-10-28

Handing a run or a script to Claude took Claude Code on the Mac, over the MCP endpoint and the VPN. The admin does it itself now, through the Claude API with a key set on the Configuration page, and the agent goes on working when the page is closed.

- **"Ask the AI agent"** on a backtest's page and in the script editor opens a panel beside the page, its state in the address (`?agent=<conversation>`): requests to start from ("Analyse it", "Find better settings", "Stress it", "Improve its script"; for a script "Review it", "Explain it", "Improve it"), the conversations about it, and one of them followed as it goes. Claude's notes between its steps, each tool call with what it read or started ("Read run …", "Started a backtest: …", "Waited for run … (2nd in line)"), then its answer in Markdown, its tables, and links to the runs and scripts it names, with what it cost.
- **It works on the server.** Requests are queued and run by the admin (two at once by default), each step kept as it comes (`var/lib/admin/agent`). A page closed, or the admin restarted, and the request goes on from what was kept; a write it had sent when the admin stopped is said to be interrupted, not sent again. A request that ends is a toast and a line in the bell; **Stop** ends one between two tool calls.
- **Its tools are those of Claude Code's endpoint**, run inside the admin under the same guards: 29 of them, to read the data, the runs and the scripts, run Pine studies, start backtests, stress tests and checks, and keep the experiment journal. Its runs are Claude's, and their page says which conversation started them ("Started by the AI agent"). Each request has its limits: tool calls (60), backtests (8), cost ($5).
- **What it proposes, you apply**, from a card under its answer: a new version of one of your scripts (shown beside yours, saved as the next version, marked as Claude's in its history; never a library), or a change to a signal rule that is not live (the version of its script, inputs, days of history). It never changes `config.json`, a live rule or a rule's mode: it says what it would change there.
- **A change of what a rule runs restarts its week in shadow**: the version of its script, its libraries, inputs, bars, asset, side or days of history, whoever changed it (`run_changed_at`). The readiness says "What it runs changed on …". A rule not changed since this version keeps its count.
- **The AI agent section** of the menu: **Activity** (the month's spending against its budget, the key, the agent's pause, what is under way, every conversation, removed with an Undo), each conversation on its own page, and **Artefacts** (the runs, scripts, experiments and proposals it made, applied or not). ⌘K finds the conversations.
- **Configuration › AI agent**: the Claude API key (sealed for the admin alone, opened at each request so that it applies at once, never shown again; **Test the connection** spends nothing), the model (Claude Opus 5.5 by default; Sonnet 5.5, Haiku 5.5, Fable 5.1), its effort, the limits of a request, the month's budget ($50), and how long conversations are kept (a year).
- **What it costs** is estimated from each answer's tokens at the model's prices (October 2026), for each request, each conversation and the month: a request near one of its limits is asked to answer with what it has, and stops at the limit; a month past its budget takes no new request. Waiting for backtests spends nothing.
- **Each request chooses its model and its effort**, under the field it is typed in (the conversation's last, else the settings'): each model with its prices and window, as the Models API says them for the key, one the key cannot use greyed. A model that cannot read the notes another wrote in the conversation says so before it is sent, and answers without them.
- **The context window, in detail**: beside them, how much of the model's window the conversation fills, from its last answer. Opened, the Claude API counts it as the next request would be sent (a count costs nothing): the system prompt, the tool definitions, Claude's thinking, what was asked and shared, the tool calls and their results, the answers, what context editing leaves out, the room an answer may take, and what the last answer used, read from the cache or written to it.
- On ioiti-ui 0.5.1: EasyUI's `ai-response`, adapted (both themes, links through the router, its tests), the test `ai-agent-activity` lacked, a `PromptComposer` of its own with room for controls, and popovers placed again as what they hold grows (the context window opens above its button once counted).

Fixed on the way:

- A credential no service restarts for said "It applies when these start again: ." on its card.
- The "Claude" badge said what it means only on a mouse's hover (`title`): it is a hint now, on focus and on a tap too.
- AGENTS.md said the admin may write to seven places: there are six.

**On the live path:** nothing. The admin, its interface and the docs alone, and the agent is off unless `admin.ai_agent.enabled` is true. `package.sh`'s `npm ci` fetches ioiti-ui 0.5.1 from GitHub as it fetched 0.4.3. Its library (`anthropic==2.3.0`, with `jiter` and `docstring_parser`) is in every image, as each library is, and loaded by the admin alone, only while the agent is on.

**Backtests:** no file of the code digest changed: no run is marked stale.

**Verification status:**
- 3,253 Python tests pass on Python 3.12 (3.14 not run here), the Pine runner's included; 23 are skipped (the live infrastructure). New: the agent's files (ids checked before a path, a line cut by a stop, pruning, the month's spending), what the API is sent (the prompt and tools cached, fallbacks but for Haiku, old thinking dropped under another digest, a note left unanswered), the loop against a fake provider (reads at once and the rest in order, the limits said and held, a stop before and during the tools, a refusal, an answer that could not be read, a fallback's calls, a restart's request taken up again without a write made twice, a request stopped while it waits its turn), each request's model and effort (asked, kept for the next, refused when not offered or not for the key), the Models API's answers, the context window (counted once per model and per state, for another model, estimated without the API), the costs, the tools (the exact allowlist, a call outside it, an answer too long, a backtest counted in the request and its experiment), the proposals (a script saved meanwhile, a library, a live rule, a rule's after a script's, the week in shadow), the routes and a page's stream, and what the agent may never reach (`tests/test_agent_boundary.py`); the credential, its redaction, the catalog, the restart guard. `requirements.txt` matches `pip freeze` of an environment built from it.
- 1,161 interface tests pass (33 more than v0.53.0's: the activity and answer's model, the sheet and the section's pages, Stop, apply, the editor holding back a new version while it has changes, the model's and the effort's menus, the context window, the Claude key's card, the trail, the live event, readiness counted from a change); lint, types and the build are clean. ioiti-ui 0.5.1 (`github:IOITI/ioiti-ui#v0.5.1`, pinned in `admin_ui/package.json`): its 484 tests and its release checks pass, and the admin's lint, types, tests and build pass after an `npm ci` from the lock.
- **Against the real Claude API**, with a test key, on the development admin's made-up data (about $0.20 in all): the Models API for the four models; the context window of a new conversation counted (the system prompt 1,400 tokens, the 31 tools 10,876); a request on Haiku 5.5 at low effort, then one on Opus 5.5 at low effort with a tool call and thinking; a follow-up after a thinking answer, nothing dropped; the window counted for Opus 5.5 (its thinking 72 tokens, as its answer said) and for Haiku 5.5 (no thinking it can read, Opus 5.5's notes named). It found that a count with the tools written otherwise than sent is refused once a thinking block is in the history: fixed before this release, as AGENTS.md says.
- Seen in the browser pane on the fake admin with `FAKE_AI=1` (answers made up, everything else real): a run asked from its page, streamed into the panel, a real stress test started and waited for, the answer's table and links, the toast and the bell; the tab closed mid-request and the conversation found answered; Stop between two calls; a script asked from the editor, the rule's change refused before the script's version, both applied, the editor at version 2 and its history marking it Claude's, the rule's readiness counting again from the change; the script's version held back while the editor had changes, the rule's change not; the menus of model and effort, a request on Sonnet 5.5 at low effort, the context window for Sonnet 5.5 and for Haiku 5.5 (Sonnet's notes said unread), opened above its button; the key's card and its test; Artefacts; 375 px in the dark theme with nothing past the page.
- **Not seen:** an admin restarted mid-request in a browser (the fake's files go with it; the loop's tests take a request up again); the server, and Python 3.14.

## v0.53.0 - Paper trading reports: every day since a rule began, as a backtest, and a place in Backtests

**Release date:** 2026-10-28

The days on paper said "4 days, +1.20 %" and no more: each was a run of one day, and nothing put them together or showed their trades, and the Backtests section did not list them at all.

- **Each rule in shadow has its report** (`src/admin/paper.py`): one backtest of every signal it raised since it was made, to the last finished weekday, its account carried from one day to the next, every trade on its page as on any backtest's.
  - It is made again after each finished day, once the market is closed, the older one removed once the newer is done (`paper/reports.json`). It trades no random entries: it is made every night. A report that fails says so beside the last good one, and is asked again for the next day.
  - It covers 92 days at most (`admin.backtests_max_period_days`) and the newest 20,000 signals of the rule; it says which versions of the script raised them when the rule changed meanwhile.
  - **"Make it now"** asks for it at any hour.
- **So do the signals WATA receives**: their report since their first day on paper, under the live settings, each paper trade set beside WATA's real one.
- **Backtests › Paper trading** (`/backtests/paper`): each rule's report (its result, trades, worst drawdown, days, where it stands), its days on paper each a link to its run, its reference run; and the signals received, their report and their days beside the real ones.
  - Each report has an address that outlives its run, `/backtests/paper/<rule>`: the run behind it changes each night, a bookmark keeps working.
  - The Signal rules page says a rule's report in place of its days added up ("Since 14 Oct: +1.18 % in 9 trades"), a link to it, and "Every day on paper"; Performance says the report of the signals received; the Engine monitor's paper days lead to the page.
- **A paper run's page says what it is**: "Signals <rule> raised" where it said "Pine script", and "The 68 signals <rule> raised" where it said "signals WATA received"; under Paper trading in the trail; a report cannot be removed (WATA keeps them), a day can.
- **"Run again with changes" works on a paper run**: it crashed the form. The New backtest form takes **"The signals a rule raised"** as its third source, for any period, with random entries and changed settings, the period's days saying how many entries the rule raised.

Fixed on the way:

- A day on paper read at most the newest 2,000 of a rule's signals: a busy rule's morning was left out without a word (`AdminQueries.rule_signals`, oldest first, 20,000).
- A rule on an indice the trader does not take stopped the whole night's paper trading: it is said in the log, and the others go on.
- A stress sweep or model check of a paper run looked like a paper run; it is an ordinary run, in the list. Claude's neighbours of a run of a rule's signals replayed the journal in their place: they are refused.

**On the live path:** nothing. The admin, its interface and the docs alone. The reports are the backtest service's own runs: one at a time, at the lowest priority, while the market is closed.

**Backtests:** no file of the code digest changed: no run is marked stale.

**Verification status:**
- 3,149 Python tests pass on Python 3.12 (3.14 not run here), the Pine runner's included; 22 are skipped (the live infrastructure). New: the reports (asked once per finished day, held to 92 days, a read of more signals than a run takes, the older let go once the newer is done, one that failed, two asks at once, a rule the backtests refuse, "Make it now" at any hour), the signals received's report (their first day kept, without an entry), the requests a paper run is, derived runs, the route, the form's new source and its coverage, `body_of`, Claude's refusal.
- The new SQL was run on a PostgreSQL 16 (pgserver), through the live test CI runs: a rule's signals of two local days, a close journaled without an action left out, the newest kept.
- 1,128 interface tests pass (25 new: the page's model, the page, a report's address and its run page, "Run again with changes", the form's new source, the Signal rules cell); lint, types and the build are clean.
- Seen in the browser pane on the fake admin, which trades its rules on paper now: the reports made (a rule's of 7 days and 9 trades, the signals received's), the page in light and dark at 1,440 px and at 375 px (nothing past the page), a report opened from its address, its run page, "Make it now" (made again, and refused for a rule made that day), "Run again with changes" made a run of the same 68 signals.
- **Not seen on the server:** a report of the real rules and journal, and how long one takes there.

## v0.52.1 - Nothing wider than a phone's screen

**Release date:** 2026-10-28

On a phone, pages of the admin slid sideways, onto nothing or onto a chart drawn past its card. Fixed in ioiti-ui (released as 0.4.3, `github:IOITI/ioiti-ui#v0.4.3`) and in the admin's own tables. The admin's interface only.

What was found at 375 px on the fake admin, with a check that lists what stands past the page:

- **The Dashboard**: "Latest signals" and "Services" were 580 px wide in a column of 337: a card in a grid's single column (every grid on a phone) grew to its widest line, a service's detail meant to be cut short. ioiti-ui's `Panel` is never wider than its place now, and the detail ends in "…".
- **A backtest's "Day after day"**: both charts were 640 px wide, past their card. A chart is drawn 640 px wide until it is measured, and in a grid's single column it held the column open at that width, then was measured so. A chart takes its width from its container alone now.
- **Performance, Signals, the Engine monitor and a backtest** scrolled sideways onto nothing: the table each chart gives screen readers was made 1 px wide, which a table never is; it stood as wide as its text, past the page. It is in a box of its own now, read as before.
- **System and the Engine monitor** (and the Dashboard's open positions on a tablet) did too: the name of a table's last column for screen readers ("Restart", "Revoke", "Median against the longest step"), and the words of a rolling value, stood out of the frame that scrolls the table. The admin's three tables, ioiti-ui's frames and its rolling digits are positioned now, and keep them.
- **An asset's tabs** (Chart, Statistics, Data quality, Trades, Details) stood 23 px past a phone's screen: the row scrolls in itself now, each name on one line, without a bar. The Engine monitor's periods did so already; their own setting is gone.
- At 320 px, the Performance page's "Long and short" table scrolls in its own frame, and an asset's bid and ask (O, H, L, C) go on two lines.
- **And the page itself never scrolls sideways** (ioiti-ui's shell): what would still stand past its edge is cut there. A table wider than a phone needs a frame of its own (AGENTS.md says so now).

The top bar on a phone is as it was: at 375 px and under it holds more than fits, the services' badge cut short.

**On the live path:** nothing. `package.sh`'s `npm ci` fetches ioiti-ui 0.4.3 from GitHub as it fetched 0.4.2.

**Verification status:**
- The interface's 1,103 tests pass, with lint and types, and the Python tests that read its files; ioiti-ui's 454 (8 new, on the CSS that keeps each part to its place: they fail without the fixes) and its release checks.
- In a browser, on the fake admin with ioiti-ui 0.4.3 installed from its tag: every page at 375 px, and the heavier ones at 320, 430, 768 and 1,024 px. Nothing scrolls sideways, and nothing stands past the page with its clip lifted, the top bar apart. At 1,280 px, 31 pages measured on v0.52.0's build and on this one: their cards, grids, tables and charts are where they were (the only differences, in cells of live figures, come and go between two loads of one build), and the backtest page no longer scrolls sideways by 29 px. Looked at: the Dashboard's two cards, the backtest's "Day after day" at 430 px, an asset's tabs and prices at 320 px. ioiti-ui's gallery at 375 px: no page scrolls sideways.

## v0.52.0 - The admin interface on ioiti-ui 0.4.2: its motion, its save bar and leave guard, its change lists, live values and charts

**Release date:** 2026-10-28

The admin interface on ioiti-ui 0.4.2 (`github:IOITI/ioiti-ui#v0.4.2`), from 0.2.1, and nothing left of what the admin wrote by hand where the package has a component. The admin's interface only: no server route, query or trading service changes.

ioiti-ui 0.3.0's own changes, which came with the update:

- **Motion, calm and the same everywhere**: a change of page is a cross-fade of the page alone, the sidebar and the top bar staying as they are; a change of theme is one fade of the whole page; a toast leaves before the next one comes; what arrives where a skeleton was fades in; dialogs grow from what opened them, and the theme button's icon turns. Motion is reduced as the system asks.
- **The tables' filters are a filter group**: a button for each column that filters, opening its options as a list to tick, each with the rows it would leave, a search of its own from eight options (the Orders page's Result); a column of two options is an on/off pair (Long, Short); "Clear filters" clears them all, where "Reset" did.
- **The check boxes draw their mark**: the tables' row boxes, "Restart anyway", "Go live anyway", a script's yes-or-no inputs.
- **A skip link**: the first Tab of a page shows "Skip to the page", past the menu and the top bar.
- **The bell's button** looks like the top bar's other buttons; the charts' small text (the trend tiles, the funnels, the heatmaps, the live lines) is the muted text of the design, 4.5:1 on the dark card where it was 3.6:1.
- **React 19.3**, which ioiti-ui 0.3 asks for (the page's cross-fade is its `ViewTransition`): declared `^19.3.1`; 19.3.0 was installed already.

The package's components in place of the admin's own (what they lacked was added to ioiti-ui, released as 0.4.0):

- **The Configuration page's bar of unsaved changes is ioiti-ui's `SaveBar`**: a floating bar at the bottom of the page, there only while something is unsaved (the bar of before stayed, saying "No unsaved change"), with Check, Discard and "Review and save"; while a field holds what cannot be used, it says so and Save is held back. The toasts rise above it while it is there, and sit at the bottom of every other page (they were lifted on all of them for this bar).
- **Leaving it asks through ioiti-ui's `RouterLeaveGuard`**, as the script editor does: "Leave and discard" or "Stay on this page", the page's own views (`/config/...`) being no leaving, and the sign-in a lapsed session is sent to let through. The browser's own question on a reload or a closed tab is unchanged.
- **Every list of changes is ioiti-ui's `ChangeList`**: a save's review (by group, each parameter a link, the trading ones said, from its old value to its new one), a backup to restore, the price recorder's and Capital.com's settings, a run's settings against the live ones. A key added reads "New", one taken out "Removed" (they read "not set" and "removed", or "set to" and "taken out").
- **An open position's live value** (the dashboard, the Positions page, the live pill) is ioiti-ui's `SignedValue`, `LiveValue` and `RelativeTime`: the same figures where they were; the bid shows a rise or a fall in its tone a moment; after a minute without the monitor every figure is muted and said stale to a screen reader (the figures were dimmed); the age has the moment in a hint, and is never said to be in the future when the server's clock is a little ahead of the browser's. One clock for the whole page (ioiti-ui's `useNow`): the positions' columns are no longer made again every 5 seconds.
- **The charts Recharts drew are ioiti-ui's `LineChart` and `BarChart`**: Performance, Report, Exits, the backtests (a run's days, its random runs with the run marked among them, runs compared, a sweep's walk-forward), an asset's spread through the day, the engine monitor. The same figures, each chart's table read by screen readers, the arrow keys going from one point to the next. A long period's time axis may name months where it named days. Recharts is gone: the JavaScript is 378 kB smaller (106 kB gzipped).

And the top bar's trail, fixed in ioiti-ui (0.4.2):

- **The trail never runs under what stands beside it.** On a wide screen it is whole as long as it fits the room the top bar leaves it, the page's own name cut short to 8rem at most; otherwise its last two steps take its place, as on a narrower screen, until there is room again. The steps before the page kept their width whatever the room: at 1,024 px the configuration's trail ran under the live pill, and under the "Restart needed" chip when it was there (v0.51.0 did the same).

My calls: the toasts are no longer lifted by 80 px on every page. A run's settings and the configuration's keys use the same list. ioiti-ui's accents (bloom included) and glass are not offered. Tests follow the new filter group (Orders, "Refused by Saxo"), find the bar by its region, the save review's list by its name, and two new ones check the script editor's leave guard and the live value.

**On the live path:** nothing. `package.sh`'s `npm ci` fetches ioiti-ui 0.4.2 from GitHub as it fetched 0.2.1.

**Verification status:**
- The interface's 1,103 tests pass, with lint and types; `npm ci` fetches the locked commit with SSH turned off. ioiti-ui's 445 tests pass, and its CI and release check passed for 0.4.0, 0.4.1 and 0.4.2.
- The built CSS keeps every rule of the 0.3.0 build but those of what the admin no longer writes (the old bar's, the Recharts tooltip's, the backtests' change list's), in the same order; 0.4.1 adds none, 0.4.2 the four of the trail that gives way (`lg:invisible`, `lg:absolute`, `lg:left-0`, `lg:w-max`).
- In a browser, on the fake admin, the computed styles of every element of 26 pages in both themes, build against build. 0.3.0's against v0.51.0's: the differences are 0.3.0's (above). 0.4.1's against 0.3.0's: the design's tokens the same on every page; the elements differ where the admin's parts were replaced (the charts, the configuration's bar, now there only with something unsaved, the live value's hints) and in the fake's live data (the live pill's gain turned to a loss, the bell's count, a hovered row), nowhere else.
- Looked at with 0.4.1: every chart replaced (Performance, Report, Exits, a backtest of the signals received, the engine monitor, an asset's spread), the configuration's bar, its leave dialog, the save's review, a saved change and its backup, the live values on the dashboard, the Positions page and the live pill; a run's random runs in the dark theme too.
- One difference was found there and fixed in ioiti-ui (0.4.1): a line chart wrote its days "7 Oct" where every other date says "07 Oct".
- The trail with 0.4.2, on the configuration's page, with and without the "Restart needed" chip: at 1,024 px its last two steps ("Configuration › Market hours and calendar", cut short beside the chip), at 1,280 px with the chip the same, whole, at 1,440 px the whole trail; the trail that gave way takes no focus; no page scrolls sideways; on a phone, the step back as before.
- Not changed here, seen while looking: on a phone the top bar holds more than it has room for (the trail squeezed to its chevron, "Degraded" cut at the edge), as in v0.51.0. A change of page waits for the browser's next frame (a view transition): in the desktop app's browser pane, hidden, which draws none, the page stays as it was until one comes. Not seen in a browser that is looked at.
- Not deployed.

## v0.51.0 - The admin interface on IOITI's design system, ioiti-ui

**Release date:** 2026-10-27

The admin interface's design and generic components now come from ioiti-ui, the package made from it on 26 October (`github:IOITI/ioiti-ui#v0.2.1` in `admin_ui/package.json`). The admin's interface only: no server route, query or trading service changes, and it looks and behaves as it did.

- **One design, kept in one place**: the tokens and the design (`src/styles/index.css` imports `ioiti-ui/styles.css`), EasyUI's 44 components as the admin had changed them, the blocks of the pages (`Panel`, `PageHeader`, `KpiTile`, `Hint`, `ButtonLink`, `QueryState`…), the formats, CSV and date ranges, the shell (the sidebar, the top bar and its trail, the page's column), the 404 and the theme are the package's.
  - WATA's copies went: 88 files, 13 of them tests, which the package holds.
  - So did the dependencies only they imported: `clsx`, `tailwind-merge`, `tw-animate-css` and the two fonts come with ioiti-ui now, at the same versions; `class-variance-authority`, which nothing imported, is gone.
  - A change to one of them is made in ioiti-ui, released as a tag and installed here (`admin_ui/README.md`, AGENTS.md).
- **What is WATA's stays in WATA**: the menu (`components/navigation.ts`), what the shell holds (`components/AppShell.tsx`: the Saxo and restart chips, the live pill, the palette, the bell, the services' state, signing out), `ConfirmLiveAction` (the package's `ConfirmAction`, saying first that the account is live), the 404's way back to the dashboard, the trading badges, the charts, `formatWallTime` (`lib/wallTime.ts`) and every page.
- **Named as the package names them**: `PnlValue` is `SignedValue`, `ServiceStatusBadge` `StatusBadge`, EasyUI's `payment-status` `RequestStatus`; a service's state is said with the package's words, the same.
- **The theme is set before the first paint by the package's script** (`ioiti-theme.js`, written and put at the top of `<head>` by `ioiti()` in `vite.config.ts`), under the key it always had (`wata.theme`): a choice kept in a browser stays. `public/theme.js` went.
- **ioiti-ui 0.2.1, for the one regression found**: 0.2.0's `RouterLink` sent a check's link to a part of the page (`#trust` among a run's checks, `#rules` on the Engine monitor) through the router, which wrote `/backtests/…#trust`, dropped the address's query and scrolled nothing. Fixed in the package, with its test: such a link is a plain `<a>`, which the browser follows in the page.

My calls: ioiti-ui 0.2's accents and glass material are not offered (no picker): the admin stays neutral and flat. `ConfirmLiveAction` and `NotFoundState` stay as a few lines around the package's. The tests that follow a link a component of the package draws mount their page under `RouterRoot`, as `App.tsx` does.

**On the live path:** nothing. The interface is built on the Mac by `package.sh`, whose `npm ci` now fetches ioiti-ui from GitHub (a public repository: no key).

**Verification status:**
- The interface's 1,101 tests pass, with lint and types (1,163 before: the copies' 64 tests are the package's now, 2 added for `formatWallTime`); ioiti-ui's checks pass on 0.2.1.
- The built CSS holds every rule of the build before, in the same order; the package adds its accents' and glass's rules, inert without their attribute on `<html>`.
- In a browser, on the fake admin: the computed styles of every element of 26 pages, in both themes at 1440 × 900 and of 5 at 375 × 812, the same as with the build before, but for the fake's live data. The pause's dialog, ⌘K, the bell, the theme kept under `wata.theme` and the toasts' place were looked at.
- `npm ci` installs ioiti-ui 0.2.1 from GitHub with SSH turned off (its tag pushed on 2026-10-27), as CI and `package.sh` will.
- Not deployed.

## v0.50.1 - The backup image builds

**Release date:** 2026-10-26

- **v0.50.0's backup image stopped at its last check, in CI** (`deploy/pg_backup.Dockerfile`):
  - The cause: rclone takes every `RCLONE_<flag>` of its environment for that flag, and the build argument `RCLONE_VERSION` (v1.75.2) was in the environment of its `RUN`, read as `--version`, which takes true or false. The downloads, their sha256 checks and age had passed.
  - The build arguments are named `PINNED_*` now, in the image and in `lab/backup_tools.sh`.
  - A test holds that nothing rclone is given starts with `RCLONE_`, but the bucket's own settings (`RCLONE_CONFIG_OFFSITE_*`).
- **CI's check of a copy sent off the server** read the public key from the key file itself, which age-keygen writes as root and for its owner alone from inside the image. The check now asks `age-keygen -y` there. That step had not run yet: the image build failed before it.

**On the live path:** nothing. v0.50.0 was not deployed, and the image is built at the deploy.

**Verification status:**
- The compose and lab tests pass (the names, the CI step).
- The image is built and a copy checked only by CI, which runs the real age and rclone.

## v0.50.0 - Operations: the database's dumps off the server encrypted, a bot that answers and only reads, and more US releases

**Release date:** 2026-10-26

The last of the three releases from the search of 2026-10-26.

- **The database's dumps off the server, encrypted** (`deploy/pg_backup.sh`; off until `.env` gives `BACKUP_AGE_RECIPIENT` and `BACKUP_S3_BUCKET`): each night's dump is encrypted with age to the operator's public key, then sent with rclone to an S3-compatible bucket, under `nightly/` or `weekly/` (the Sunday dumps).
  - Retention: 14 nightly and 8 weekly copies are kept in the bucket (`BACKUP_OFFSITE_KEEP`, `BACKUP_OFFSITE_KEEP_WEEKS`; 0 deletes none there, for a key of the bucket that may not delete).
  - Encryption:
    - The private key is made on the Mac and never leaves it: nothing on the server can open a copy.
    - Nothing is sent unencrypted: a bucket without a key is said, and sends nothing.
  - Health: `last.json` gains what became of the copy, and health says it, and warns after two nights without a copy sent. A failure's details are in `docker logs pg_backup1`, never in health.
  - Restoring:
    - `pg_backup.sh offsite` lists the bucket.
    - `python -m lab backup check [--offsite]` restores, on the Mac, the newest dump pulled by `lab/sync.sh`, or the bucket's newest copy opened with the key, into a scratch PostgreSQL, and counts the trading tables as `pg_backup.sh check` does.
    - `lab/backup_tools.sh` puts the same versions of age and rclone in `.venv-lab/bin`, checked by their sha256.
  - The image: pg_backup1 now runs `wata-backup` (`deploy/pg_backup.Dockerfile`, built by `docker_build.sh` from that file alone). It is `postgres:16-alpine` with age v1.3.2 and rclone v1.75.2, each download checked against the sha256 its release lists.
- **The bot answers commands, and only reads** (`reporting.telegram_commands`, off until true). The commands are `/status`, `/positions`, `/day`, `/signals` and `/help`:
  - `/status` gives the trader and the position monitor by their heartbeats, the pause and since when, Saxo's login, the price feeds, the positions and the day, and the next US release with its window.
  - Who and how often:
    - The telegram service reads them (getUpdates) in a thread beside the queue's consumer.
    - Only the chat the bot sends to is answered; another chat is passed over without a word.
    - Each command gets one message, none older than 2 minutes (sent while the service was stopped) and none past 6 a minute.
  - Never an action:
    - Nothing it answers pauses, resumes, closes, restarts, saves a setting or switches a rule, and nothing is published to a queue.
    - Each command opens a connection of its own with `default_transaction_read_only` and runs only the SELECTs it lists.
    - Only getUpdates and sendMessage are asked of Telegram: never a webhook, never the bot's settings.
    - What the services wrote is cleaned as log lines are, and no answer names an id, a key or a token.
    - `tests/test_telegram_commands.py` holds its imports (pure modules alone, no queue client), its statements and its chat.
- **More US releases** (`src/trade/data/macro_calendar.csv`): producer prices (`PPI`), retail sales (`RETAIL`), the GDP's advance estimate (`GDP`) and job openings (`JOLTS`), from late 2026 to December 2027.
  - Sources and gaps:
    - Each row names the official page it was read from on 2026-10-26: BLS, Census's economic indicator calendar, BEA.
    - BLS had not given JOLTS's 2027 dates: JOLTS holds 3 November and 1 December 2026.
    - ISM's manufacturing index is not there: only its rule is public (the first business day, 10:00), which it broke in January 2026, and its dated calendar is for ISM's members.
    - PCE's twelve 2027 dates were added from BEA's 2027 schedule: the calendar ended on 23 December 2026.
  - When they apply:
    - A rule that names no release keeps the four as before (`DEFAULT_EVENTS`): the new ones apply once named in the `macro_news` rule's `events`.
    - The calendar's end is now that of the releases the rule names, said with the kind that ends first: health, the scheduler's morning check, the dashboard and a run's note. A kind no rule names (JOLTS, the shortest) warns nobody.
    - Each kind is known from its first row: a backtest that names one on earlier days says so.
  - Backtests and the lab:
    - Four backtest settings repeat them (`news_ppi`, `news_retail`, `news_gdp`, `news_jolts`, off).
    - The kinds of day still go by the four, which the calendar holds since 2024.
    - `python -m lab calendar check` lists each kind from its first day to its last, and judges the four, or those named with `--events`.

**On the live path:**
- The trader keeps the same windows: a rule that names none keeps the four, and no rule on the server names the new ones.
- With PCE known to December 2027, the morning's "update the calendar" message is not due before November 2027.
- The telegram service starts its commands only when switched on.
- The backups change nothing until `.env` names a key and a bucket; the backup image is built at the deploy.

**Backtests:** `src/backtest/settings.py` and `regimes.py` changed (the four settings, the kinds of day held to the four): every run is marked stale, as since v0.48.0 already. The calendar's digest changes.

**Verification status:**
- 3,121 Python tests pass on Python 3.12, the Pine runner's included (3.14 not run here), and 22 are skipped. The new ones:
  - the bot's commands:
    - what each answers on a fake database;
    - the read-only connection, every statement a SELECT;
    - the two files' imports: no queue client, no trading code;
    - the chat: another chat passed over, an old command and the seventh of a minute not answered;
    - Telegram's failures waited and said without the token, and the switch;
  - health's copy off the server: sent, one night failed, two nights, none yet;
  - the backup image: versions and sha256 checks, built from its file alone, no key on the server that opens a copy, the variables named in `.env.example`, CI's step;
  - the lab's restore check: a real restore with pgserver, the newest copy chosen, the flow from the bucket with fakes;
  - the calendar:
    - the new kinds applying only when named, ISM refused;
    - each kind's first and last day, JOLTS at 10:00;
    - the end per the rule's kinds;
  - the validator's boolean.
- On a PostgreSQL 16 (pgserver): every command ran on the real schema, before and after the feeds' tables exist, and a write was refused on the commands' connection. The same is a new live test in `tests/test_infrastructure_live.py`, which CI runs against its own PostgreSQL.
- `deploy/pg_backup.sh` ran on this Mac with pgserver's own pg_dump and stand-ins for age and rclone (the downloaded binaries were not run here). The cases:
  - not asked; sent, the oldest copies deleted down to 14; 0 deleting none;
  - no key; a failed send; a bad key;
  - the bucket's path, the listing, the restore check, a failed dump.
- The interface: no file of it changed. Seen in the browser pane on the fake admin:
  - the Reporting group's "Telegram commands" switch;
  - the eight kinds of the Releases, four on by default;
  - health's warning after two nights without a copy.
- **Not seen:**
  - the real age and rclone: CI's image job runs them, against a folder standing for the bucket and a key made for it;
  - a real bucket, and the real bot's updates;
  - the server's own 429s: the logs of the trader and the position monitor say whether they waited 60 s.

## v0.49.0 - Evidence while entries are paused: the signals received traded on paper, a drift watch, a real trade on its chart, and your own metrics on the Exits page

**Release date:** 2026-10-26

The second of the three releases from the search of 2026-10-26. New entries have been paused since 21 October: these say each day what the live settings would have made of the signals received, and why a real trade went as it did.

- **The signals WATA receives, traded on paper each day** (`src/admin/paper.py`): each finished weekday, once the market is closed, the day's signals of the journal (TradingView's alerts) replayed under the live settings.
  - Their fills are WATA's own measured ones when enough orders were measured (10 of each), the settings' fixed ones otherwise.
  - Their days are drawn on the index of the indice: the Capital.com feed's, Dukascopy's while the feed is off.
  - A day without an entry signal is kept as such and not asked again.
  - The runs are kept apart from the list of runs, 60 days, beside the shadow rules' paper days.
- **A drift watch** (`src/admin/live_drift.py`): on a replay of the signals WATA received, "Set as the live reference" makes its daily range (5 % to 95 % of its days) the one each paper day, and each real day, is set against.
  - The last two days with a result both outside it are said once on Telegram, and are a warning of health ("Live against its reference", off the services map).
  - While entries are paused only the paper days have a result; once trading again, the real days count as much.
- **The Performance page lists those days**: the real result, the paper one, each outside the range or not, the trades on paper, the paper run a click away, and the warning when there is one (`GET /api/backtests/paper/live`).
- **A closed position drawn on its chart**: opened on the Positions page, a closed position is drawn on its turbo's bid every ten seconds as the price recorder kept it, with the index below, as a backtest's trade is drawn.
  - The chart shows the signal that opened it, where it was bought and sold, the price paid, the best and worst it reached, and the level its close reason names (a stop-loss, a take-profit, a trailing stop's peak).
  - `GET /api/positions/{id}/chart` finds the recorder's instrument of its Uic, the newest it named. A turbo the recorder did not keep is said to have no chart.
- **Your own metrics on the Exits page** (`src/admin/live_breakdowns.py`, as the trading style file asks):
  - by the signal's confidence, and by the turbo's price at entry, in the backtests' own buckets;
  - by where the signal came from: a signal rule (its `ta_signal` sent with the same `signal_uuid`), or TradingView's alerts and the signals sent by hand, which the journal does not tell apart;
  - by the signal's delay, from its own time to the trader's decision;
  - by the worst the trade reached (recorded since v0.48.0).
  Each is read from its own table only if it exists yet, so an engine that never ran empties the source groups and nothing else.

**On the live path:** nothing. Only the admin and its interface change. The paper runs are the backtest service's own: one at a time, at the lowest priority, while the market is closed.

**Backtests:** no file of the code digest changed: no run is marked stale.

**Verification status:**
- 3,052 Python tests pass on Python 3.12, the Pine runner's included (3.14 not run here), and 21 are skipped. The new ones:
  - the live paper days: a journal day traded with fixed fills, then with measured ones; days without an entry kept as such; the queue left alone; the rules' paper days unchanged, their pruning with the live days;
  - the drift: each day against the range, the warning after two days outside, said once on Telegram, a real day back in the range ending it; health's entry;
  - the Exits page's groups; the position chart route (turbo, index, day, signal, an unknown position).
- The new SQL was run on a PostgreSQL 16 (pgserver): the Exits extras without and with the TA engine's table, the worst read beside each trade; a position's chart refs, without the recorder's tables and with two rows of one Uic.
- 1,163 interface tests pass (the live paper days' sentences, a position's marks, levels and focus, the services map's ids); lint, types and the build are clean.
- Seen in the browser pane on the fake admin: the Performance page's paper days with the warning, the reference's range and a day without an entry signal; the five groups on the Exits page; a closed position drawn on its turbo's bid with the index below.
- **Not seen on the server:** a paper day of the real journal, the drift on the real days, a real position's chart on the recorder's bars.

## v0.48.0 - Guards before entries resume: orders a second apart, one source of signals per index, an account guard, the day's breakers, a spread limit, and each position's worst

**Release date:** 2026-10-26

The first of three releases from the search of 2026-10-26 for what WATA is missing. They come from the record of 2 September to 20 October 2026: 118 trades, −69.75 % summed, the losses on days of 11 to 15 trades, three stop-losses at −7 % filled at −16.7 %, −30.2 % and +0.5 %, one order in ten waiting 60 s or more for Saxo. Each new setting is off until set, and the backtests and the dashboard repeat each new rule.

- **An order is no longer held a minute by Saxo's rate limit.** Saxo takes one order a second per session (its `SessionOrders` limit), and an order refused for it (429) carries `X-RateLimit-SessionOrders-Reset`, about a second. The client read only `X-RateLimit-Session-Reset`, absent there, and waited its 60 s default before asking again: one order in ten of 27 Jul–26 Oct 2026 took 60 s or more to be accepted (the 90th percentile, 60.3 s, median 0.26 s). Now (`src/saxo_openapi/async_client.py`):
  - order requests (POST, PATCH or DELETE of an order, not its precheck) leave `ORDER_SPACING_SECONDS` (1.05 s) apart, `OrderPacer`: two sales at once, or the other side sold right after a purchase, wait under a second instead of being refused;
  - a refusal is waited as long as the limit that ran out says (`wait_after_refusal`): about a second for the order limit, the session's reset for the others, and not at all for the app's limit of the day, which is not asked again.
  The services share one session at Saxo: two of them ordering in the same second can still be refused, and now wait about a second.
- **One source of signals per index** (`trade.config.signal_sources`): each index takes TradingView's alerts (`tradingview`, and any index not named) or the TA engine's live rule (`engine`), never both. They feed one store, which keeps the newest signal of each index: a rule live beside TradingView's alerts would close what they opened, and the reverse.
  - The web server answers a webhook for an `engine` index without keeping it (`ignored`), and counts it (`/internal/webhook`): the Signals page says how many came since it started and the last one, so an alert left on in TradingView is seen.
  - The TA engine sends nothing for an index that takes TradingView's alerts, journaled with why (`NOT_SOURCE`, counted on the Engine monitor).
  - A rule goes live only on an index the web server runs with set to the engine: a hard requirement, which "Go live anyway" does not skip.
  - The scheduler's close of the day and the signals sent from the admin come through `/internal/signal`, and are taken whatever the source.
  - Health warns of an `engine` index with no live rule, and of a live rule on an index that is not. The review before a save warns of a change that leaves either.
  - The Configuration page offers each index's source in a list (a map's values may now be picked from one).
- **An account guard** (`risk_management.pause_below_balance_eur`, `pause_after_drawdown_percent`; off until set): new entries are paused once the account is worth less than the floor, or that much less than its highest since entries were last resumed.
  - It is the operator's own pause, said to be the guard's on the dashboard (its banner and the Next entry tile) and once on Telegram. Only the operator resumes it, and the resume starts the highest afresh (`entries_resumed_at`).
  - The value is Saxo's `TotalValue`, read with the balance the trader asks for anyway: every 30 s on its keep-warm timer, where the guard runs between signals, and before an entry, read again only when older than 2 minutes.
  - The highest is the larger of the daily values the report records since the resume (`account_balance_history`) and those the trader read since its start. A deposit raises it; a withdrawal reads as a fall.
- **The day's breakers** (`risk_management.max_consecutive_losses`, `max_drawdown_from_day_best_percent`; off until set): no new entry for the rest of the day once that many of the day's trades lost in a row, or once the day's compounded result is that many points under the best it reached at a close (0 before the first). The −25 % day limit let 19 October reach −28.5 % over 15 trades. One module (`src/trade/day_breakers.py`) for the trader, the backtests and the dashboard's Next entry tile.
- **The widest spread bought** (`trade.config.turbo_preference.max_spread_percent`; no limit until set): of the turbos in the price range, those whose spread is wider than that share of their ask are left out before the cheapest is chosen, in the trader, the watchlist manager and the backtests. With none left, the entry fails and names the narrowest spread seen.
- **Each position's worst result**, kept beside its best: 0 until it falls under its open, never while a fresh quote is awaited, the database keeping the lower. It is in a table of its own (`position_worst`: the schema never alters an existing table), shown on the Positions page ("Worst so far" for an open position, a column one click away for the closed ones, and in the CSV), returned by Claude's `data_trades`, and exported for research. Positions closed before this version have none.
- **The Positions and Orders timelines read earliest first** (ab60152, committed on 2026-10-26 and released with this version): the steps of an order, and the events of a position, are put in the order of their moments, whichever clock they were read on. Saxo often dates a fill before its answer reached WATA.

**On the live path:**
- The orders' pacing and the 429 waits apply at once, with no setting.
- The worst result is written after the sales are attempted, as the best is, from the position monitor.
- An index absent from `signal_sources` takes TradingView's alerts as before. A rule live on an index not set to the engine sends nothing from now on (none is live on the server).
- Everything else is off until set.

**Backtests:** four new settings repeat the breakers, the guard and the spread limit (all 0, which changes nothing), and `src/trade/async_services.py`, which every run's code digest holds, changed: every run made before is marked stale. `src/trade/account_guard.py` and `src/trade/day_breakers.py` join the digest.

**Verification status:**
- 3,040 Python tests pass on Python 3.12, the Pine runner's included (3.14 not run here), and 21 are skipped. The new ones:
  - the client against a mock transport: each limit's wait, the request id kept, the app's day not waited, orders spaced, other requests never;
  - the webhook ignored and counted for an `engine` index, `/internal/signal` taken, the state behind the internal token;
  - the engine's refusal journaled, the go-live requirement (forced or not), health and the save preview's warnings;
  - the guard: floor, highest recorded and read, the resume starting afresh, one pause and one message;
  - the breakers: the row, the fall from the day's best, off;
  - parity with the live trader for each new refusal (8 breaker and 7 guard scenarios), the spread limit against the live helper;
  - the worst result written when it falls, never for a position that only rose.
- **The new settings absent change nothing**: a Pine run (3 days, 24 trades, random entries, edge) and a sweep of 4 cells, each made on the same recorded files by v0.47.0's code and by this version's, gave the same files, but for what says when and with which code they were made.
- The new SQL was run on a PostgreSQL 16 (pgserver), through the real managers and the admin's queries: the day's results in close order, the worst kept at the lower, the guard's pause and the resume, the highest since a moment, the worst read beside each position.
- 1,156 interface tests pass (the Next entry tile's new states, the Signals page's notes, the closed table's columns); lint, types and the build are clean.
- Seen in the browser pane on the fake admin: the dashboard's banner and tile say the pause is the guard's; the Signals page counts TradingView's ignored alerts; "Worst so far" on the open positions; the four limits and the spread on the Configuration page; the source picked from a list; the save preview's warning.
- **Not seen on the server:** the 429 waits on Saxo's real answers, the account guard on the real balance, the web server ignoring a real TradingView alert.

## v0.47.0 - Range bars answered faster, read live a bar at a time, and the Charts page's scripts continued on the bars that are over

**Release date:** 2026-10-25

- **A range-bar answer takes half the time, and a sixth of the bytes** (`GET /api/data/assets/{provider}/{key}/range-bars`):
  - The bars are built in a thread (`_range_answer`), the rows still read on the request loop. A month's build no longer holds up the admin's other requests, its event streams and live prices among them.
  - Their rows are written by `pydantic_core.to_json`, the rest of the answer as FastAPI writes it (`_with_rows`, which the `/bars` route uses too). FastAPI's own encoder took 58 ms of a day of R60's 113; now it is 3.4.
  - `price_decimals` reads each distinct price once: a day of US100's ten-second bars holds 6,720 distinct prices among 34,080. It was 381 ms of a month of R600's 914.
  - The admin's answers of 4 kB or more go out gzipped, at level 5 (`CompressedAnswers` in `src/admin/app.py`). A day of R60 is 0.13 MB instead of 0.82, compressed in 12 ms. A request that accepts `text/event-stream`, and `/mcp`, go around it: Starlette's compressing middleware holds an answer's headers back until its first bytes.

  A day of R60 (9,601 bars) is now 52 ms of Python instead of 113, and a month of R600 526 ms instead of 914. Measured on the Mac: the route's steps over a made-up US100 shaped on a real day.
- **A live range chart reads only its new bars.** `range-bars` takes two new parameters:
  - `after`: the bars from a moment on, still built from `from`, their volumes counted over all of them;
  - `newest`: the bars built from the first day of the period from which they fit in 12,000. That day is said in `from`, rather than a refusal.

  The Charts page asks for `newest` once. Before, it asked for three days, then two, then one when they were too many. Then, at each closed ten seconds, it asks for the bars from its last one on: about 1 kB, where each read was the whole series again (0.8 MB a day of R60). Its live stream starts from the newest bar the chart was given, not from a whole read for its last two bars. The asset pages and the Data section read as before: no `after`, no `newest`, the refusal kept.
- **The Charts page's scripts run on the bars that are over**, as the TA engine runs a rule's: a bar of time 10 s after its end, a range bar once the server says it is complete. The bar still growing shows its prices, and the script's values once it is over. A script runs again when one more bar is over, not at each quote: on a chart of time, that was every second.
- **Their runs continue with the new bars, in Vela-pinets' worker** (`admin_ui/vite/velaContinuedRuns.ts`). It is patched at build time like the time-zone formatters and Vela's seconds, and the build stops if a piece of the worker's code is not found exactly once.
  - The new bars are appended to the PineTS of the last whole run and run on its context, as the TA engine continues a rule's run (`Runner.continueRun`).
  - A script that reads what a whole run gives otherwise (`barstate.*`, `request.*`, a strategy…) runs whole each time, as before. That check is `wholeBecause`, now `engine/wholeRuns.ts`, shared with the runner.
  - A whole run is made at the start, on a change of inputs, of the visible range or of history, when bars before the new ones changed, and every 10 minutes.

  Measured on the user's Kernel VWAP script (light v5, with its library), over Dukascopy's US100 of 15–17 Sep 2026:

  | Bars | New bars, 1 to 20 at a time | A whole run |
  |---|---|---|
  | 2,670 bars of one minute | 41–62 ms | 3.8 s |
  | 4,344 range bars of 6 points | 73–152 ms | 20.5 s |

  The models and alerts are a whole run's, but for the labels' numbers: PineTS numbers labels with a counter of the whole worker, which every whole run moves on.

**On the live path**: nothing. Only the admin and its interface change. `RangeBarBuilder` and the files of the backtests' code digest are unchanged, so no run is marked stale.

**Verification status:**
- 2,952 Python tests pass on Python 3.12, the Pine runner's included (3.14 not run here), and 20 are skipped. 7 of them are new:
  - the decimals equal to a price-by-price reading's, on 25 random series;
  - `after` equal to the tail of a whole answer;
  - `newest` cut to the newest bars rather than refused;
  - `newest` built from the first day whose bars fit, said in `from`;
  - the rows' JSON parsed equal to FastAPI's;
  - gzip on a big answer and not on a small one;
  - an event stream and `/mcp` never compressed.
- 1,147 interface tests pass, 14 of them new:
  - the bars that are over: time, 45 s, the last ones, range;
  - a run only when one more bar is over, a timer when the market closes, history, the throttle of range charts;
  - WataBars: one request from the first day that fits, a cut answer not widened, older days and too many bars read anew;
  - the live stream started from the newest bar given;
  - the patched worker run in a Node `vm`: continued models and alerts equal to whole runs' (a script with `var` and `ta.*`), and whole runs for a script not marked or when the bars before changed.

  Lint and types are clean, and the build holds the patched worker.
- The user's script was compared in the patched worker, as above.
- **Seen in the browser pane on the fake admin**, its made-up market opened on a Sunday:
  - A live R60 chart of US100 with the EMA cross script made one `newest` read of 570 kB (111 kB sent), then one `after` read of about 0.9 kB each ten seconds.
  - The scripts were handed the bars up to the last one over, and each new one once. A new bar's run took 1 to 7 ms, against 26 to 36 ms for a whole run of about 500 bars.
  - On bars of one minute, a run came when a minute was over plus ten seconds, and none in between.
- **Not run on the server**: the answers' timings on its database, and the Charts page on the real Capital.com feed.

## v0.46.1 - undici 8.12.3 under the interface's tests

**Release date:** 2026-10-25

- **undici 8.12.3** (from 8.11.2), for GitHub's high advisory GHSA-xjj6-f33q-929c (Dependabot alert 49 on `main`), along with two moderate ones (GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472):
  - a response with too many content encodings could crash the process;
  - an event stream could take unbounded memory;
  - an integrity check could be passed with an algorithm written in capitals.
- undici comes only with jsdom, the browser the interface's tests run in (`"dev": true` in the lock). jsdom asks for `^8.11.2`, so only the lock changes.

**On the live path**: nothing. The built interface, the Pine runner and the checker hold no undici: their 71 files are byte for byte the same after the update. The server's package would differ only by its version, so this release is not deployed.

**Verification status:**
- 1,133 interface tests pass; lint and types are clean.
- One whole run failed once, on `orders.test.tsx` ("open onto what happened…"), which checks a disclosure as visible right after its click. The same file then passed five times alone, and in two more whole runs.
- OSV lists no advisory left for any package of `requirements.txt` or of the lock (348).

## v0.46.0 - A signal rule's days of bars, counted, its inputs in the script's groups, and the recorded bars found by their time

**Release date:** 2026-10-25

- **"Days of bars before each day" says what it is, under the field rather than on hover, and counts what it makes.** It decides how many bars the TA engine runs the rule's script on: each day at its `restart_time` (21:15 UTC) the engine starts the rule again on the bars of those days before, runs the script over them once without sending anything, then adds each new bar, so that the script runs on those days of bars after the restart and on one day more by the next. A day is 24 hours counted back from the restart: a weekend or a holiday counts and brings no bar, so that on a Monday one day before Sunday evening's restart holds none.
  - The Market step of the rule's dialog counts the bars for the asset, side and bars chosen, and the days typed: those the script was started on at the last restart, those it would run on now, and those of each engine day of the week (`GET /api/ta/history`, `src/admin/ta_history.py`). They are made by the engine's own builders on the Capital.com feed's rows, from the rule's own start: the tests hold the count to what the engine loads, on bars of minutes, 45 seconds, hours and range bars.
  - The list of rules gives the engine's restart time beside its `history_days` (`engine_restart_time`), which the dialog says in the trading timezone of the day and in UTC.
- **A script's inputs are set out in the groups it declares** (the `group` of its `input.*()` calls, a constant's value included, as PineTS reads them), the sections of TradingView's Inputs tab: each group in a box named after it, where its first input is declared, with all of its inputs in their order; the inputs of no group where they are declared, between the groups. In the rule's dialog and in the Backtests form, which share the fields.
- **The bars of a period across every instrument are found by their time** (`price_bar_10s`, a BRIN index on `ts`, `idx_price_bar_10s_ts`). Until now they were found through the key, which starts with the instrument: a day's read went through the whole month. On a month in the server's shape (42 turbos, 860 minutes a day, 4.8 million bars), a day is now 3,206 pages read against 68,144:
  - the research export's day;
  - the Availability page for today alone, 173 ms against 186;
  - each page of `wataprices export`, about 40 ms against 121;
  - the lab's reload of a day, 50 ms against 215.

  The index weighs 32 kB a month, and the price recorder builds it at its first start, in 0.5 s for a month. Writing a bucket of 42 bars stays at 0.43 ms (0.42 before).

  On the server, without the index, a day's read went through all of October's 1.65 million bars so far: 25,598 pages, 177 ms (2026-10-25).
- **One read is slower when everything is in memory: the Availability page's week**, 860 ms against 708, although it reads four times fewer pages. PostgreSQL now sorts the week in one process, where before it scanned the month in three.

**Verification status:**
- 2,921 Python tests pass on Python 3.12, the Pine runner's included (3.14 not run here), 28 of them new: the count equal to what the engine loads, on bars of one minute, 45 seconds and an hour and range bars, with none, one and two days of the rule's own; a day 24 hours back from the restart, a weekend bringing no bar (one day before a Monday's restart holds none, three reach Friday's session); the week counted whatever the rule's days; the route (the feed's rows read from the first engine day of the week, a page at a time, the engine's days and restart time without days of the rule's own, what it refuses, behind the session), the restart time in the list of rules; the schema holds the BRIN index on the bars' time. The lab's round trip runs it on two real databases.
- The new SQL run on a real PostgreSQL 16 (`pgserver`): read a page at a time through a read-only pool, it gives the engine's own rows exactly (25,568 of them, the asks missing included).
- Measured on a real PostgreSQL 16 (pgserver) with the server's memory settings, over a month made up in the server's shape, through WATA's own queries (`DatabaseData`, `AdminQueries`, the research export, `export_bars`). Per-instrument reads keep their plans and pages: a backtest's day, the charts, the list of assets.
- The recorder's own `init_price_history_schema` builds the index over a recorded month in 0.47 s, and costs nothing at the next start.
- 1,133 interface tests pass, 9 of them new: a draft's request (a length typed as people write it, range bars, the days only when the rule has its own), the help in the trading timezone of the next restart (summer and winter time), the sentences (a weekend restart, no day before), the week's days, the inputs' sections (gathered, ungrouped between, names as written), the dialog's count asked again for typed days, the inputs in their groups and changed there.
- **Seen in the browser pane on the fake admin**: the Market step's help and count on minutes, three days and range bars, on a Sunday whose last restart found only Friday's last minutes; the inputs of a script with groups (one named by a constant, an input of no group between them, a group's inputs gathered from further down) in the dialog and in the Backtests form; desktop light, phone width dark.
- The server's database was read before the index, read-only (sizes, settings, two query plans).
- **Not run on the server**: the count on the Capital.com feed's recorded rows; the BRIN index.

## v0.45.0 - The Configuration page by section, and a purge the services survive

**Release date:** 2026-10-25

- **The purge of the credentials' old places would have stopped every service at its next start.** It takes Saxo's AppKey and AppSecret out of `config.json`, which v0.44.0's `ConfigurationManager` still required. It now takes an app object without both (its addresses stay, read while `SAXO_REDIRECT_URL` is not set) but still refuses one without either half. The purge also checks the purged file with that validator before it writes anything, and refuses otherwise: run it from this version on.
- **`watavault`**, a command of the server made by the install script: `watavault migrate [--dry-run | --purge-old]` runs the compose file's `vault_tools` in the folder of the version installed, and `watavault export-saxo` runs in the trader's container. `vault_tools` starts PostgreSQL itself, so that it runs with every service stopped.
- **The Configuration page's groups are under the sections of the menu**:
  - Trading: day targets, market hours, signals, size, exits, the watchlist.
  - Analysis: the daily report, backtests.
  - Data: Saxo, the price recorder, Capital.com.
  - Technical Analysis: the TA engine.
  - Operations: the admin, Claude, logs, infrastructure, credentials.

  Backtests, Claude and the daily report are groups of their own, taken out of "Admin and Claude" and "Reporting and logs". Each section and group has its icon, on the overview and in the list beside each group, which runs across the top on a phone, with the group shown brought into view.
- **Inside a group, the parameters that go together are under one heading**, linked from the top of the page: the session, the stream and the calls to Saxo's API; buying power, size by time of day, size by confidence, which turbo. The keys nothing reads come last, under "Read by nothing". Every parameter is under one heading, which the catalog's tests hold.
- **The services WATA connects to have their own marks**: Saxo, Capital.com, TradingView, Telegram, PostgreSQL, RabbitMQ and Claude. They appear on the groups, the credentials, the System page's Saxo and Claude panels and the Data providers, and each is listed in the third-party notices.
- **The tabs JSON and Whole file are gone, and Previous versions opens again.** The tabs were links that waited for the page to follow them, and this page never did.
- **The admin's page is sent with `Cache-Control: no-cache`.** A browser kept the page of before a deploy for a while, which asked for assets the deploy had replaced.

**Verification status:**
- 2,878 Python tests pass on Python 3.12 and 3.14, 10 of them new: Saxo's app in `config.json` with its key and secret or neither, a purged file taken by the services' validator, a purge refused (nothing written) when the validator would refuse it, the groups under the menu's sections in its order, each parameter under one block of its group, the page given both, `watavault` and `vault_tools` starting the database, the page sent with `no-cache`.
- 1,124 interface tests pass, 7 of them new: the overview by section, the Previous versions tab followed and back, a group's headings and their links, the keys nothing reads under their own, the blocks and sections worked out from the catalog.
- **Seen in the browser pane on the fake admin**: the overview by section with each group's icon and the Saxo, Capital.com and Claude marks; a group's list by section, its headings and links; the credentials with Telegram's and TradingView's marks; PostgreSQL's block; the Previous versions tab; desktop dark, phone width light.
- **Not run on the server**: the purge itself (its check was run on the example configuration, purged and validated).

## v0.44.0 - Saxo's login in PostgreSQL, authorised from the admin

**Release date:** 2026-10-24, with v0.40.0, v0.41.0, v0.42.0 and v0.43.0

Until now Saxo's login was an encrypted file in `var/lib/saxo_auth`, its app in `config.json` (mounted into every container), and a lapsed login was handed back over SSH or by pasting a code the admin wrote into that folder. Each service that found the login lapsed said so on Telegram in turn, every five minutes, and held the lock between them while it waited.

- **Saxo's login is kept in PostgreSQL**, sealed for the four services that log in to Saxo: the trader, the position monitor, the watchlist manager and the price recorder.
  - They renew it in turn, one at a time under the lock they already shared in `var/lib/saxo_auth`, and a renewal is written only if no other service renewed it meanwhile: a refresh token is used once, and Saxo refuses it the second time.
  - While PostgreSQL cannot be written, a renewed token waits beside the lock, sealed, and the next service to renew writes it: Saxo's login does not depend on the database.
- **Two apps, live and simulation**, each set on the Configuration page's Credentials: its App Key and App Secret, or the app object copied from Saxo's developer portal, which is checked against the account (an app of the other environment, a grant type other than Code, redirect addresses without this server's) and whose addresses are never kept. Saxo's login and token addresses are WATA's own, one pair per account. The services log in with the app of the account `saxo_auth.env` names, which stays a setting of the server: switching it would mix the two accounts' positions in one history.
- **A lapsed login is authorised from the admin**, System page, Saxo connection:
  - "Open Saxo's login" opens Saxo's page with a link made for this login alone, its state kept hashed for ten minutes and used once;
  - with `SAXO_REDIRECT_URL` (`deploy/.env`) set to the admin's `/api/saxo/callback` and registered in the app, Saxo sends the browser back to the admin, which hands the code over by itself and says what came of it; otherwise the address Saxo redirected to is pasted, its state checked;
  - the code is sealed for the four services, and one of them takes it (one only) and exchanges it. `watasaxoauth` does the same on the server.
- **Said once**: Telegram says a lapse once, then every half hour until a login comes, and says when it is back. A service waiting for a code no longer holds the lock: it looks for one every five seconds for five minutes, then asks again at its next need.
- **The admin no longer mounts Saxo's token folder.** It reads when the login was saved and by which service, when it ends, since when it is wanted and what Saxo last answered, never a token.
- **Moving it in**: at the deploy, with the services stopped, the migration of v0.43.0 also takes the app from `config.json` and moves the token file in under the services' lock, then puts the file aside, so that the login goes on without a new one. `export-saxo`, in the trader's container, writes it back for a return to v0.43 (README, Credentials).

**Verification status:**
- 2,868 Python tests pass on Python 3.12 and 3.14, 22 more than v0.43.0: Saxo's login in its row (a fresh token used without asking Saxo, an expired one renewed once under the lock and written as the next generation, one renewed meanwhile by another service used, a refusal said in the row), a lapse (a code handed over exchanged, said once, awaited without the lock, a second service silent), the file of before read while it is there, a renewal kept beside the lock while PostgreSQL cannot be written and read back, the admin's link and its state (hashed, ten minutes, used once), the callback, `watasaxoauth`, the token file moved in and written back for a return (after the purge too), where the admin says each app is read now.
- The new SQL run on a real PostgreSQL 16 (`pgserver`, the live tests of `tests/test_infrastructure_live.py`): two services renewing an expired login at once renew it once, the second reading the first's; a code handed over taken once, a lapse said once. 15 live tests pass, RabbitMQ's left out (none here).
- 1,117 interface tests pass, 12 of them new: the Saxo panel (the login and who saved it, a link opened for this login, an address pasted with its state and a code alone, the callback's outcome, no row yet, no app set and no redirect), the state read from a pasted address, the app object read and checked, a Saxo app filled from it and sealed.
- **Seen in the browser pane on the fake admin**: the login lapsed, its link followed back through the callback to the System page's notice, the login then shown with who saved it; a refused callback; the live app set from a made-up app object of the simulation account, its three warnings, then sealed with its readers' restart offered; desktop dark, phone width light.
- **Not run against Saxo nor on the server**: the exchange of a real code, Saxo's acceptance of the admin's callback as a redirect, the move of the server's token file, the four services sharing the row.

## v0.43.0 - Credentials sealed in PostgreSQL, set from the Configuration page

**Release date:** 2026-10-24, with v0.44.0

Until now WATA's credentials were in five places: `config.json`, mounted into every container, with its twenty backups in clear; `.env`; two encrypted files; the MCP tokens' file. The admin could change none of them.

- **The credentials are kept in PostgreSQL, each sealed for the services that read it**:
  - the Telegram bot (its token and chat), read by the telegram service and each service that sends to Telegram itself when RabbitMQ does not take a message;
  - TradingView's webhook token, read by the web server and the admin;
  - a new internal token, which the trader, the scheduler, the TA engine and the admin send the web server. Until now they sent TradingView's token: an alert's address seen by someone opened the internal endpoints too;
  - the Capital.com login, read by the feed alone;
  - the outside watcher's address, read by the position monitor alone.
- **Sealed**: a value is encrypted once, its key wrapped for each reader with that service's public key; only a reader's private key opens it, and a row moved under another name opens no more. Each service has its key pair, made by the install script in `/app/wata/keys` and mounted into its own container alone. The keys are in no dump nor export: a copy of the database is ciphertext. Keep a copy of `/app/wata/keys`.
- **The Configuration page's Credentials group** shows each one: where the services read it now, when it was set and from where, its last four characters, and what each service last saw when it used it (a message sent, a ping answered, the feed's login). Each has how to obtain it, step by step, and a dialog to set or replace it, checked by the server before anything is written. The admin cannot read back what it seals, except what it uses itself.
- **The tokens are rotated** from the page or `watawebtoken --new`: the one before stays accepted for an hour for TradingView's, the time to change the alerts, and fifteen minutes for the internal one. TradingView's address is shown on request, audited. The Telegram bot can be tried with a test message.
- **A change applies at the next start** of the services that read it, which the card offers to restart.
- **Claude's tokens are in PostgreSQL too**, made from the System page (shown once) or `watamcptoken`. The file of before is moved in when the admin first starts.
- **A service never reads a credential elsewhere than its row** when it has one. One that cannot open it does without it and says so: the web server refuses, the trader's watch says it cannot poll, the feed idles, the telegram service leaves the messages in the queue. Nothing stops and starts again in a loop. One whose row is absent reads its old place, said once in its log.
- **The web server never makes a token up any more**: with none, it refuses every request and says so.
- **Moving them in**: at the deploy, with the services stopped, `docker compose --profile tools run --rm vault_tools migrate --dry-run`, then without `--dry-run` (README, Credentials). The old places stay as they are, so that going back to v0.42 works. `--purge-old`, at your word after a day, clears them.
- **The telegram service keeps the messages Telegram refuses for a wrong bot or chat** (401, 403, 404, "chat not found") in the queue for when they are set right. It dropped them, as it drops a message refused for itself.
- The telegram service and the scheduler now wait for PostgreSQL at their start, where their credentials are.

**Verification status:**
- 2,846 Python tests pass on Python 3.12 and 3.14, 72 of them new: sealing (each reader opens, no other key, any byte changed, a value moved under another name), the keys made once and never replaced, each credential's checks without its value in a refusal, the loader (its rows laid over the configuration and hidden in the logs, an absent row read at its old place, one that cannot be opened never read elsewhere, the database waited for, given up, and the stop heard), the web server's two tokens and the one before a rotation, the admin's list, sealing for the readers alone, rotation, the audit without a value, the webhook's address, the bot tried, the tables it writes, Claude's tokens in the table, the migration and the purge, the keys in the compose file and the install script.
- The new SQL run on a real PostgreSQL 16 (`pgserver`, the live tests of `tests/test_infrastructure_live.py`): a credential set by the admin, replaced, checked, opened by the feed through the admin's read-only pool, not by the trader, removed; Claude's tokens made, used, revoked.
- 1,105 interface tests pass, 9 of them new: what is said of each credential, a bot set and its readers' restart offered, a token rotated and its address shown, the bot tried, nothing set without the keys, a Claude token made and shown once.
- **Seen in the browser pane on the fake admin**, with keys made for it: the Credentials group, a token made and its address shown, the Telegram bot set with the restart of its seven readers offered; desktop dark, phone width light.
- **Not run on the server**: the keys made by the install script, the migration on the server's own files, the services reading their rows.

## v0.42.0 - Services restarted from the admin to apply a change

**Release date:** 2026-10-24, with v0.44.0

A service reads `config.json` when it starts. Until now a change saved from the Configuration page waited for someone to restart the services on the server, and the admin, which has no access to Docker, could only guess which ones still ran without it, from its own start.

- **A control service** (`service_control1`, started with the admin's profile) holds Docker's socket in place of the admin.
  - It makes two calls of Docker's API: what a container is doing, and its restart, at the version of the API the daemon says it serves.
  - It acts only on WATA's ten containers, each checked by its compose label and its image: never PostgreSQL, RabbitMQ, Traefik, the backups or itself.
  - The admin alone reaches it, on a network of their own with no way out, with a token both are given as a secret, made by the install script at the first install.
  - It restarts one container at a time, and a service of the trading path at most once in five minutes and three times an hour, whatever asks it.
- **The System page lists the containers**: running since when, how often restarted, stopped for lack of memory, and the saved changes each does not run with yet. Those are worked out from the journal of saves against each container's start: a change undone since is none, and a change made on the server by hand is said as such. The top bar's "Restart needed" names the services, and the provider pages and the Configuration page's card after a save offer the restart of those concerned.
- **A restart is held back while it could cost a trade**, unless asked anyway (recorded in the audit trail):
  - the trading path while a position is open, in the database or as Saxo lists it to the position monitor, or while the market is open by WATA's hours, as for a deploy;
  - the position monitor while it is still trying to sell a position;
  - the scheduler within two minutes of one of its jobs, which it schedules at its start (a restart just after its time skips the day's);
  - the TA engine while a live rule runs and the market is open;
  - the admin while a backtest or one of Claude's studies runs.

  Those held back can be left out and the others restarted at once.
- **Several services are restarted one after the other**, the admin last, each once the one before is at work again: a heartbeat of its new start, a consumer on its queue, an answer, a state row or a bar of its new start, its log's line. The guards are read again before each one, and the plan stops at the first that does not start again. Its progress is shown as it goes, said on the bell when it ends, and on Telegram when it stopped: whoever asked may have left the page.
- **Restarts are operator actions**, switched off with `admin.actions_enabled`, audited and announced on Telegram, and never within reach of Claude's endpoint. A change of `.env` or of `COMPOSE_PROFILES` still needs `watastop` and `watastart` on the server, which the pages say.
- **The market's hours were read three ways** by the downloads, the research export and the paper days. One function now decides it for them and for the restarts, as the deploy does.
- **The compose file's setup step rewrote `config.json` at each start**, even when the password it writes was already there. It leaves the file as it is then.

**Verification status:**
- 2,774 Python tests pass on Python 3.12 and 3.14, 59 of them new: the Docker client and the calls it refuses, against a fake Docker on a Unix socket too; the control service's token, its list of containers held to the compose file and the catalog, one restart at a time and the trading path's pace; who holds the socket, the network and the token in the compose file; the guards; the changes a service does not run with; a plan's order, readiness, guards read again, failures and their message; the routes, the audit, the top bar.
- 1,096 interface tests pass, 17 of them new: what the pages say of the containers and of a plan, the restart asked, asked anyway, the held ones left out, and the event of a plan's end.
- **Seen in the browser pane on the fake admin**, with a made-up control service: a save, its card's restart, the confirmation with the services held back by an open position, those left out, the others restarted one after the other; desktop dark, phone width light.
- **Not seen on a real Docker here** (none on this Mac): CI runs the control service on the runner's Docker, restarts a container named like WATA's through it, and checks that it then reads a file bind-mounted alone and replaced since, as the editor replaces `config.json`. Not run against the server.

## v0.41.0 - Every parameter of the configuration on its page, with what it changes

**Release date:** 2026-10-24, with v0.44.0

Until now the Configuration page had fields for about forty keys of `config.json`. The rest of `trade.config` (the trading timezone, streaming, polling, the watchlist manager, the TA engine) was reachable only as JSON, and the `admin`, credentials and infrastructure sections only as the whole file.

- **A catalog of every key** (`src/admin/config_catalog/`). For each of the file's parameters it holds:
  - what the parameter does and what else it changes;
  - which parameters it works with;
  - what to watch for: a bound only the code holds (60 turbos asked at most), a timezone that is not the one expected (the TA engine's restart is in UTC, the sizing periods follow the trading timezone);
  - which services read it at their start, so which a change restarts, and what else reads it (the dashboard's limits, new backtests, paper days, the research export);
  - its bounds and its default;
  - whether the page may change it, and if not, why and how it is changed on the server.

  A test holds the catalog to the code:
  - every key of the example file, every setting the sections check themselves and every path the code reads has an entry;
  - no entry describes a key nothing reads;
  - the bounds offered are accepted by the validators, and refused beyond them where the services check them;
  - what the page may change is what the editor takes.
- **The Configuration page is built from it.**
  - An overview leads to fourteen groups and searches every parameter.
  - Each group has a page of its own, with a field for each parameter, what it changes, the parameters it works with as links, and a reset to its default.
  - An hour of the market is shown as it is that day in New York, Paris and UTC, and the page says when the clocks are changed apart.
  - Optional rules (risk limits, US releases) are switched on and off from their group.
  - Read-only keys (credentials masked, infrastructure, the admin's switches and addresses) are shown with why.
  - A key of the file WATA does not know, a typo or a leftover, is listed with the key it resembles: nothing reads it.
  - A draft is kept from group to group, and leaving the page with changes unsaved is asked about.
- **Before a save, the review says** what each parameter becomes, which services apply it at their next start, what else reads it, and what works against what: a close of the day after the end of trading, a trailing stop that arms above the take-profit, the TA engine on with the Capital.com feed off, exits judged on stale quotes. The server checks the change as a save would, the admin's own section included (`AdminSettings`, and bounds for the limits the page now changes).
- **After a save:**
  - the keys changed are written down beside the file, values masked;
  - the Previous versions tab shows what each save changed;
  - the audit entry and Telegram name the keys and the services to restart;
  - the page gives the restart command of the services named. Restarting them from the page comes with the next version.
- **The admin's limits can be changed from the page**: session times, backtests, Claude's endpoint and its limits. Its switches (`actions_enabled`, `config_edit_enabled`), the proxies it believes, its address and its folders stay the server's.
- **Keys nothing reads no longer have to be in the file**: `general.position_check`, `general.websocket`, `api_limits.top_positions`, `retry_config.retry_sleep_seconds`. The page says so.
- **The admin takes the watchlist manager as on unless switched off**, as the trader does; it took it as off.
- **The Capital.com provider page said `docker restart capital_feed1` after a change of the login in `.env`.** A restart keeps the environment the container was created with: it now says `watastart`.

**Verification status:**
- 2,715 Python tests pass on Python 3.12 and 3.14, 143 of them new: the catalog against the code and the files (138), the preview, the change journal and history, the audit's detail, and the keys nothing reads taken out of the file.
- 1,079 interface tests pass, 31 of them new: the catalog's model, the timezones, and the page.
  - The overview and its search.
  - A group with who reads each parameter and what it changes.
  - An optional rule switched on and the draft kept through another group.
  - A value out of bounds refused before anything is sent.
  - A group that does not exist.
  - The review and the save with its restart command.
  - Leaving the page asked about, moving between its views not.
- **Seen in the browser pane on the fake admin:** desktop dark and light, phone width light.

## v0.40.0 - Services that stop cleanly, and secrets kept out of the logs

**Release date:** 2026-10-24, with v0.44.0

The first step of the configuration's rework (every parameter on the page, the credentials in PostgreSQL, services restarted from the admin): a restart from the admin is only safe once a service can be stopped without being cut off, and secrets must not be left in the logs the admin shows.

Until now no WATA service ever received Docker's stop. The image starts each role through a shell script, which never passes SIGTERM on to Python. Docker waited 10 seconds and killed the service, so no stopping code ever ran: not the trader's, not the monitor's, nor the handlers the recorder, the feed and the TA engine already had.

- **Each service now stops cleanly**, at a deploy as at any restart.
  - The start script hands its place to Python (`exec`), and the compose file runs tini as PID 1 (`init: true`), which hands it the signal.
  - **The trader** finishes the signal it is handling, then polls no more (`stop_grace_period` 60 s). An order cut off between Saxo taking it and WATA writing it down would leave a position with no row, which nothing manages or adopts.
  - **The position monitor** starts no new sale and waits for the one it has sent to Saxo (15 s at most), then stops its stream and its loops (`stop_grace_period` 45 s). What was still to be sold is decided again at its next start.
  - **The scheduler** leaves its loop between two jobs.
  - The others stop at once.
- **No secret in a log line.**
  - Every line a service writes is cleaned:
    - by shape: a token in an address, a Bearer header, Saxo's stream address and account keys, Telegram's bot address, a password in an address;
    - by value: the secrets of `config.json`, the webhook token, Saxo's tokens, the Capital.com login and the outside watcher's address.
  - uvicorn's access log is cleaned too; it wrote TradingView's webhook token with each request.
  - The HTTP and WebSocket libraries stay at `WARNING` whatever `logging.level` says. At `INFO`, httpx wrote:
    - the outside watcher's address every minute;
    - Telegram's address with the bot's token, each time a message was sent directly;
    - Saxo's account keys with every request.
  - The admin's Logs page shows and searches the older lines cleaned the same way.
- **The Telegram service no longer crash-loops on a message Telegram refuses.**
  - Its request has a time limit.
  - Its errors no longer name the address, which holds the token.
  - A message refused for what it is (400 to 499 but 429) is dropped with a log line instead of stopping the service again and again.
  - One Telegram could not take for now goes back to the queue after 5 s.
- **The deploy's own "Stop the application" step works.** It ran a `cd … &&` without a shell and always failed, leaving the install script's stop as the only one. It now stops the version running at the time, every container included.
- `python -m src.saxo_authen` no longer prints the access token.

**Verification status:**
- 2,572 Python tests pass on Python 3.12 and 3.14. The new ones cover:
  - the shapes and registered values masked;
  - a plain word not taken for a secret;
  - an exception's text cleaned;
  - uvicorn's access log keeping its five arguments;
  - a service's log file holding no secret, its libraries kept quiet;
  - the admin's Logs page;
  - the Telegram service's answers;
  - the trader's wait for signals cut off at once while the signal in hand is finished;
  - the monitor starting no sale once stopping and waiting for the one under way;
  - the compose file, the start script, CI's check and the deploy's stop;
  - the monitor's stop kept out of the code every backtest's digest holds, so no run is marked as made with other code.
- **Not yet seen:** a service stopped by real Docker. CI's image job does it (it stops the scheduler's container and reads its line).

## v0.39.0 - Real-time prices from Saxo, and New York's hours read as such everywhere

**Release date:** 2026-10-24

The deploy of v0.38.0 showed that Saxo serves WATA's session at `OrdersOnly`, the level at which Saxo gives an app delayed market data. Saxo flagged none of the quotes recorded since 13 October as delayed (0 of 1.79 million bars), but the turbos WATA trades were quoted about every 30 seconds while the index moved every second.

- **Real-time prices, when switched on** (`trade.config.saxo_session.full_trading_level`, off unless true; a switch under Market hours on the Configuration page). The trader asks Saxo for the full level of WATA's session (`FullTradingAndChat`) at its start, reads it back ten seconds later, then checks it every minute.
  - The services share one login, hence one session: the position monitor and the price recorder get the level the trader got.
  - One session of an account holds that level, the last to ask: while WATA holds it, SaxoTraderGO loses it and may be logged off.
  - When another session takes it, WATA says so on Telegram and asks for it again.
  - A refusal is said every quarter of an hour, and is a warning in health while WATA is below the level it asks for.
- **Hours written in New York's time are read in it everywhere**, as the trader, the monitor and the scheduler already read them: in the price recorder, which would have taken the nights without quotes for incidents and the US session's silences for none, in the admin's downloads, paper days and research export, and in the MCP's market. Days are still counted in the trading timezone.
  - In the weeks Europe and the US are five hours apart, 02:00 in New York is 07:00 in Paris, an hour before Saxo quotes the turbos: an entry then finds none.

**Verification status:**
- 2,545 Python tests pass, 13 of them new: the level asked for and confirmed, asked for again after another session took it, a refusal said every quarter of an hour and warned of in health, nothing asked without the setting, the setting's check, and New York's hours read in New York's time by the recorder, the downloads, paper days and the research export, with Paris days.
- 1,048 interface tests pass; lint and type checks are clean.
- Not seen: Saxo granting the level, and whether real-time prices change the turbos' 30-second quotes (to be read on Monday's bars).

## v0.38.0 - A position judged from Saxo's next quote, and backtests priced at it

**Release date:** 2026-10-24, with v0.34.0, v0.35.0, v0.36.0 and v0.37.0

From the record. Saxo sends the turbos WATA trades a new quote about every 30 seconds (40 at the 95th percentile on the turbos recorded from 13 to 20 October), while it fills an order at the live price. On 19 October a long was bought at 10:38:37 for 5.055 while Saxo still quoted the turbo at 4.627, its quote of 10:38:10, from before the index rose 25 points. The monitor set the one against the other, saw −8.47 % and sold; the sale filled at +0.51 %. From July to October, six daily profit targets were "met" 1 to 6 seconds after a purchase, on gains of +0.7 to +4.2 % that filled at −0.07 to −1.96 %. The backtests made the opposite mistake: they bought at the quote standing at the fill, on average 0.48 % under what WATA paid (8.3 % that day).

- **A position just bought is judged from Saxo's next quote** (`src/trade/fresh_quote.py`). Seen within two minutes of its opening, it is not judged on its bid until that bid changes: no stop, take-profit, trailing stop or daily target, and its best result is not raised. A quote that does not change says nothing new, so waiting for it costs no information. The day's close and the sale before a US release are not held back. On unless `position_management.wait_for_fresh_quote` is false: a switch on the Configuration page, shown on while the key is absent.
- **Backtests price an order at the turbo's next quote**: the first quote Saxo sent after the one standing at the fill, within two minutes, for a purchase and for a sale by a signal, by the day's close or before a release; an exit of the monitor still sells at the bid that decided it. On WATA's 31 purchases of 13 to 20 October, the next quote is 0.04 % from the price paid on average and the standing one 0.48 % (median error 0.21 % against 0.34 %, the largest 1.46 % against 8.3 %).
  - A new run is priced at the next quote and judged as the monitor judges; both are settings of the form.
  - A run made before keeps the standing quote, and is marked as made with other code.
  - With the next quote and the wait switched off, a run replays the monitor of before, its false exits included.
  - Measured fills keep pricing from the quote when the order left, which is what their measure compares.
  - A fill 1 % or more from the standing quote is said in the trade's notes.

After deploying: make a rule's reference run again before reading its days on paper beside it, so that both are priced alike.

**Verification status:**
- 2,532 Python tests pass, 38 of them new: the module, the monitor on the record's cases (the stop of 19 October not taken, a target not met on a quote from before a fall, a stop after the next quote taken at once, a position held since before a restart judged at once, the sale before a release still made), parity with the backtest with the wait on and off, the next quote read from a turbo's bars, the 19 October trade replayed on a test day (bought at the next quote 5.092 where WATA paid 5.055; without the wait, sold at −9.13 % as the monitor of before sold at −8.47 %), the settings of new and older runs, the configuration's check.
- 1,048 interface tests pass, 1 of them new (a switch shown on while its key is absent); lint and type checks are clean.
- Not seen: against Saxo, nor a backtest of the server's days (they run the deployed code).

## v0.37.0 - Going live held to its readiness, and sweeps of the signals' delay

**Release date:** 2026-10-24, with v0.38.0

The last of four releases on what WATA lacked to trade reliably (v0.34.0 to v0.37.0). Its changes to trading were to be measured first, and none was built. A replay of the journal's signals 0.5, 15, 30 and 60 s after WATA received them (experiment 439d40, the 9 recorded days of 13 to 23 October) ended at +4.67 %, −40.0 %, −44.2 % and −35.7 %. All of the difference is two days, and neither is a cost of seconds: on 21 October the day's first trade met the day's +0.5 % target in 24 s at 0.5 s and missed it at 15 s, after which the day lost −26.9 % over 21 trades; on 19 October the 0.5 s replay was saved by one trade bought at a bad recorded quote. Past 15 s, more delay costs nothing more. Inconclusive. Set beside WATA's real positions, the replay at 0.5 s with fixed fills makes +0.67 points a trade more than WATA did (the median), and with 15 or 30 s added it comes out like WATA's. The other measurements planned (a pause after losing days, exits in index points, fewer flips) wait for 20 recorded days: on a strategy without a measured edge, a rule that trades less looks better for that alone.

- **A signal rule goes live only once it is ready** (`src/admin/ta_readiness.py`): a week of weekdays in shadow, a signal journaled, no day on paper outside the range of its reference run, the engine running it without fault. Until now the confirmation said these check by check and left the switch to the operator. Short of one, the server now refuses the switch unless the operator ticks "Go live anyway", and the audit trail says what was missing. The script's latest version is still only said.
- **A sweep of the signals' delay measures it**: each combination's signals arrive its own delay after WATA received them, where every combination had the run's and the sweep gave one result five times. A combination that closes the day at another time gets its own closes.

**Verification status:**
- 2,494 Python tests pass (v0.34.0 to v0.37.0 together, the live PostgreSQL and RabbitMQ tests not run), 7 of them new (a rule short of its readiness refused, then taken live anyway and so audited; each check of the readiness; a sweep of the delay and one of the close time), with Node 24 for the Pine runner's own, its bundle built again first (the one kept here predated v0.30.0).
- 1,047 interface tests pass, 3 of them new (a rule not ready waits for "Go live anyway", which the server is told; a refusal the page did not foresee offers it; what the server holds back, check by check); lint and type checks are clean.
- Not seen: the confirmation in a browser, and a rule taken live on the server.

## v0.36.0 - Calendar and operations

**Release date:** 2026-10-24, with v0.38.0

- **The US market's holidays and early closes are a file** (`src/trade/data/us_market_calendar.csv`, from NYSE's announcement of 23 December 2025 for 2026 and 2027; `src/trade/market_calendar.py`). The trader takes no entry on a US holiday (WATA traded Labor Day 2026) and none late on a day that closes early, and the day's close comes that much earlier (scheduler and position monitor alike). The backtests and the dashboard's tile do the same, parity tested. From 30 days before its last day, the scheduler's morning message and health say it needs its next year. `market_closed_dates` still applies.
- **`market_hours.timezone`** (optional, `America/New_York` for instance): the trading hours, the risky minutes and the day's close are then New York's, which keeps them right in the weeks Europe and the US change clocks apart (25 October to 1 November 2026).
- **The risky minutes run from their hour and minute to the end of the day.** Counting the minute alone in each hour left holes in a window of more than an hour.
- **The database is dumped each night** (`pg_backup1`, `deploy/pg_backup.sh`, at 02:17 UTC, without the bars except on Sundays). 14 nights and 4 Sundays are kept and `lab/sync.sh` pulls them to the Mac. `pg_backup.sh check` restores the newest into a scratch database and counts the trading tables. Health shows the last dump.
- **A deploy is refused while WATA holds a position or the market is open by its hours** (`-e force=true` deploys anyway).
- **Out of memory, the kernel kills the admin and the backups before the trading path** (`oom_score_adj`).
- An optional number on the Configuration page may stay empty: it no longer holds the form back.

**Verification status:**
- `pg_backup.sh now`, then `check`, run against a PostgreSQL 16 (pgserver): dumped, restored, the trading tables counted. The compose file and the playbook are held by `tests/test_deploy_compose.py`.
- The Configuration page's optional numbers checked in a browser against the dev server.
- Not seen: a dump on the server, a deploy refused, an early close.

## v0.35.0 - Know when WATA is blind

**Release date:** 2026-10-24, with v0.38.0

- **The trader and the position monitor beat every 15 s** (`service_heartbeat`, `src/heartbeat.py`). The admin's health judges them by it, not by a log line or a queue consumer, and lists the sales the monitor is still trying to make.
- **The trader watches itself** (`src/trade/trader_watch.py`): a web server it cannot poll, no signal for `reporting.signal_silence_minutes` in trading hours (off unless set; on the Configuration page), Saxo refusing to renew the login, the level Saxo serves the session at. No new position while Saxo refuses the renewal, and the dashboard's tile says so.
- **Saxo's answer to a refused renewal is kept and said**: on 23 October the login lapsed at 20:05 and nothing said why. A 429, a 5xx or no answer is tried again twice.
- **No turbo is bought on a quote Saxo says is delayed.**
- **A silent position stream with positions open is replaced by REST checks every 5 s**, and the stream client takes its token in a thread: a renewal no longer freezes the monitor's loop.
- **Telegram is reached directly when RabbitMQ refuses a message**, and `WATA_HEARTBEAT_URL` (in `.env`, the position monitor's alone) pings an outside dead-man's switch each minute while it is healthy.

**Verification status:**
- The heartbeat's write run against a PostgreSQL 16 (pgserver); the stream client's tests against a local WebSocket server (`LocalSaxoStream`) pass with the token taken in a thread.
- Not seen against Saxo: a refused renewal's answer, the session's level, a delayed quote, a silent stream replaced by REST checks. The dead-man's switch and the Telegram fallback were not tried.

## v0.34.0 - Never hold what WATA cannot sell

**Release date:** 2026-10-24, with v0.38.0

From the record of 25 July to 24 October: a long held through the CPI of 14 October (its turbo unquoted for 250 s) sold at −30 %, two Friday positions held over the weekend, 43 of 245 closes labelled "SaxoAPI" though WATA sold them.

- **A sale is kept until Saxo no longer lists the position** (`src/trade/close_intents.py`): tried again with growing waits and said on Telegram less and less often. A sale already working at Saxo is waited for. The position monitor tries every second, whether the stream brings prices or not.
- **The trader hands what it could not sell to the position monitor** (`src/trade/ops_queue.py`, a `close_position` ops message with its reason). A close says what it could not sell and why, where it said "No open position to close".
- **With a cash reserve, an entry that failed still sells the other side**, as the classic mode does (the simulator too).
- **Nothing is held after the day's close**: the monitor sells what is left a minute after `close_position_time`. With the `macro_news` rule's new `flatten_minutes_before`, nothing is held into a US release either (mirrored, parity tested; on the Configuration page). The scheduler's close is tried three times, and its failure said on Telegram.
- **Every order request has its own `x-request-id`** (the id it is kept under): Saxo refuses an identical order request within 15 s.
- **A position WATA bought but lost (no row) is recorded again and managed**; one bought by hand is named once and left alone.
- **The reconciliation keeps WATA's own sale reason**: "SaxoAPI" only for what WATA did not sell.
- `src/trade/macro_calendar.py` is part of the backtests' code digest.

**Verification status:**
- The new reads of the order requests (WATA's sale reasons, its purchases) and the row of an adopted position run against a PostgreSQL 16 (pgserver).
- Not seen against Saxo: a sale kept until the position is gone, the `x-request-id`, a position adopted, a close handed over by the trader.

## v0.33.0 - Admin: a close followed to its sale, live lines, funnels, trend tiles, Claude's trace, an Undo, previews, a map of the services, clocks, a live pill

**Release date:** 2026-10-23, with v0.30.0, v0.31.0 and v0.32.0

Twelve more of EasyUI's components, three of them added to EasyUI that day (the funnel, the live line, the stat card). The admin interface only: no server route, query or trading service changes; the dev server's fake data follows (a close that takes a few seconds, Claude's calls).

- **A close is followed to its sale** (EasyUI's `payment-status`): once the slide is done, a card under the top bar says each step, from the close asked to the sell Saxo took, then "Sold" with the price, the result and the time it took; or Saxo's refusal as Saxo wrote it, the position still open; or, a minute and a quarter on, that no sell was seen (the monitor drops a request older than a minute). A signal sent by hand is followed the same way, to the trader's decision and the position it bought. The card reads what the pages read and acts on nothing; it outlives the row it was asked from.
- **An open position drawn live** (EasyUI's `live-line-chart`): its performance as the monitor publishes it, scrolling over fifteen minutes, on the dashboard and in an open position's row, its change over the window said in points; hovering or touching it stops it to read a moment. The server keeps only the latest value: the line starts when the tab first read it, and the tab keeps half an hour of it.
- **Where the signals go, as a funnel** (EasyUI's `funnel-chart`): a run's "Why" from its entry signals to its winning trades (passed the rules, had a recorded turbo, bought, won), the biggest drop named; the Signals page the same for the entry signals the trader decided, over the journal it read. The four tallies stay under the run's.
- **Tiles with a trend to scrub** (EasyUI's `stat-card`): the dashboard's day profit and loss with the days before it, and the Report's account value with its change since the first daily report, each a line read day by day by pointer, touch or the arrow keys. The other tiles are unchanged.
- **Claude's calls as a trace** (EasyUI's `ai-agent-activity`): System › Claude's last calls, each with its outcome in words and its time, opening on its arguments as the log keeps them and the token that made it.
- **Runs are removed with an Undo** (EasyUI's `undo-toast`): from the selection, the failed attempts or a run's page, they leave the list at once and are deleted when the bar runs out, eight seconds on; Undo keeps them. Nothing is deleted before: a tab closed meanwhile deletes nothing.
- **A run's actions in one button** (EasyUI's `split-button`): "Run again with changes", its menu holding Make a shadow rule, Set as the rule's reference, Rename and Remove. Stop stays a button of its own while a run is active. Links stay links.
- **Previews on hover, focus or a tap** (EasyUI's `peek-card`): a link to a run shows its total, trades, won and random runs beaten (Experiments, Compare, a stress or a model check, the bell); a link to a position what it was and made (Orders, an order's and a refusal's details, the Signals journal, where the position is now a link); a run's rule its mode, script and bars. A click still follows the link; on a phone the first tap opens the preview, whose own link leads on.
- **A map of the services on System** (EasyUI's `dependency-trace`): TradingView, Saxo and Capital.com outside, the ten services, PostgreSQL and RabbitMQ, each with its health as an icon and a word; hovering or focusing one traces what it calls and what calls it, each link named. From a laptop's width up; the table stays.
- **New York beside Paris on the dashboard** (EasyUI's `airport-matrix-clock`), with the US open in the session's time and the hours between the two: from 25 October to 1 November the open is at 14:30 in Paris, five hours apart.
- **.pine files dropped on Indicators** (EasyUI's `animated-file-upload`): several at once, imported one after the other, libraries first, each with its outcome and a link to its script; one opens in the editor, as the button's did.
- **A live pill in the top bar** (EasyUI's `dynamic-island`): on every page, the open positions' live result, or whether the next entry would be taken; it opens on the positions with their Close and what holds the next entry back. It replaces the "Entries paused" chip.

My calls: one followed request at a time, its card under the top bar (toasts and a table's selection are at the bottom); a close said dropped 75 s after its ask; an Undo of eight seconds, the former backtester's runs keeping their question; Stop never in the menu; a preview opening on a tap only where it is a link of a page (the bell's entries open on a pointer and the keyboard only, a tap following them); the funnel's stages in one blue ramp (`--chart-funnel-1..5`), its last step allowed to recede (2.5:1, its figure written beside it); no change pill on a tile of profit and loss (a percentage of a profit means nothing); the pill hidden on a phone when it has nothing to say. Fixed in the copies on the way: the undo toast's bar went back to full after a pause, and a re-render restarted its countdown; the live line gave a change against the first value ever seen and drew sixty frames a second for a fifteen-minute window; the preview card added a second tab stop and followed the link it was opened from; the trace's rows and the map's nodes took no keyboard; the map cut names to seven letters; the clock had no Paris; the uploader started every file at once. The components were copied from EasyUI's commit `7c106d4` of 23 October; `admin_ui/README.md` lists each change.

**Verification status:**
- 1,038 interface tests pass, 55 of them new (a close followed to its sale and to Saxo's refusal, a signal to its decision, the live samples and the dashboard's lines, both funnels, the trend tiles scrubbed with the keyboard, the market clock around the clock changes, Claude's trace, an Undo in time and an expired one, the run's menu and removal, previews by focus and by a tap, the services map's data and trace, two .pine files dropped, the pill, its Close behind a confirmation); lint, type checks and the build are clean.
- 2,367 Python tests pass (16 skipped: the live PostgreSQL and RabbitMQ tests and the lab's round trip, which need what this Mac does not run), with Node 24 for the Pine runner's own. No Python file changed but the dev server's fake data.
- In a browser against the dev server, light and dark, at about 890 px, 1,440 px and a phone's 375 px: a close from the pill refused by Saxo, then sold, the card saying each step; a long signal sent and refused by the cooldown; the dashboard's live line, clocks and trend tile; both funnels; Claude's trace opened; the services map traced from the trader and from PostgreSQL; a run's menu, a run removed and undone (kept on the server past the eight seconds) and one removed for good; a position's preview on the Orders page; the pill on a phone, opened across the screen.
- Fixed after that look: the opened pill grew over the menu (now from its left edge); the followed card sat under the toasts at the bottom right (now under the top bar); the undo toast's line was cut; a funnel stage losing nothing said "−0.0%".
- Not seen: a real tap (the pane's taps are clicks: the touch paths are in the tests), files dropped in a browser, a screen reader, the server's services and events.

## v0.32.0 - Admin: a bell for events, ⌘K, searchable lists, hints, versions as a ledger, runs side by side, heatmaps, rolling figures

**Release date:** 2026-10-23, with v0.33.0

Eight more of EasyUI's components, where the admin's pages had a use for them. One server change, for the heatmaps: the Exits page and a backtest's breakdowns count the trades by weekday and hour of opening.

- **A bell in the top bar keeps this tab's events** (EasyUI's `notification-bell`): what each toast said (a position opened or closed, a sell Saxo refused, a signal decided, a service down or back, Saxo's authorisation, a backtest finished, prices downloaded, entries paused or resumed), 50 at most, newest first, the unread counted. Each leads to where it happened: the position, its order, the run. A reload keeps them, another tab has its own. The switch for browser notifications moved into its footer.
- **⌘K (Ctrl+K), or the top bar's search button, goes anywhere by name** (EasyUI's `spotlight-search`): the menu's pages, New backtest, New script, the runs, the signal rules, the scripts, the assets (turbos that ended left out), a position or an order by its number. It only goes somewhere: nothing in it trades or changes anything.
- **Long lists can be searched** (EasyUI's `combobox`): the asset of the script editor's test (every turbo not ended: type a level, "24150", or a symbol), a backtest's asset and the setting of each sweep axis. Short lists keep their select.
- **Hints show on a tap and on keyboard focus** (EasyUI's `directional-tooltip`, through `Hint`), where a `title` showed only to a mouse: why a control is disabled or what a state means. The top bar's chips and Live/Polling, a live rule's Delete, a run made with other code, a rule's days of history, the editor's Save while its script has errors, an interval with too many bars, a sell Saxo refused, a flagged asset, a configuration field in error, the basket's disabled actions. Screen readers read each with its control.
- **The configuration's previous versions are a ledger** (EasyUI's `recovery-ledger`): the file in use at the top, then each version it replaced, newest first, with its size and file name. A version opens on what restoring it would change, and only then offers Restore, which still asks for the slide, now saying how many changes it makes.
- **Runs compared are a matrix** (EasyUI's `smart-comparison`): a column per run (its name a link, its colour on the chart, its source and total at the top), rows of what it ran, what differs between the runs and their figures, "Only what differs" and a search over the rows; on a phone, one run at a time. The chart of the days stays.
- **Heatmaps** (EasyUI's `activity-heatmap`), gains and losses each on a scale of its own, by size:
  - Performance: the period's days as a calendar, a column a week from Monday.
  - Exits: the trades by weekday and hour of opening, in the trading time zone; the same in a run's "Where the result comes from" ("Weekday and hour"). Each cell says its result, its trades and the share won, read cell by cell with the arrow keys; the figures are also in a table under it.
  - Teal for gains, red for losses, four steps each: a gain and a loss of one size stay apart for colour-blind readers, which the bars' emerald and red do not at their paler steps.
- **The live figures roll to each new value** (EasyUI's `animated-number`): an open position's performance and unrealised result (dashboard and Positions), the day's profit and loss on the dashboard. Still where the system asks for less motion; screen readers read the value, not the rolling digits.

Server: `/api/exits` adds `by_weekday_hour`, a run's breakdowns `weekday_hour` (`src/admin/weekday_hour.py`). It stays out of `src/admin/analysis.py`, which is part of every run's code digest: no run is marked as made with other code.

My calls: Restore offered only once a version is open and read; Monday to Friday always on the weekday heatmaps, a weekend day only with trades, the hours from the first traded to the last; the calendar from the start of the period (a year when it has none); 50 entries listed in ⌘K, the others found by typing. Fixed in the copies on the way: the heatmap found the cell a column and a row off the pointer, and its keyboard could start on a day not drawn; the ledger's line ran beside its pips; rolling digits stood higher than a sign beside them. The components were copied from EasyUI's commit `50effaf` of 23 October; `admin_ui/README.md` lists each change.

**Verification status:**
- 983 interface tests pass, 36 of them new (the bell and where each event leads, ⌘K, the searchable lists, the hints, the ledger and a restore, the matrix's "Only what differs", the heatmaps' cells, colours, keyboard, table and month labels, the Exits and Performance panels, a run's "Weekday and hour", the rolling figures with and without reduced motion); lint, type checks and the build are clean.
- 2,352 Python tests pass (16 skipped), the weekday and hour of the Exits query and of a run's breakdowns among them. The Exits query run on a throwaway PostgreSQL 16: weekdays and hours of Paris across the change of clocks, a trade opened at 23:10 UTC on Sunday 25 October counted on Monday at 0:00.
- In a browser against the fake admin, light and dark, at the pane's width (about 890 px) and a phone's 375 px: the Exits heatmap (the cell under the pointer, its tooltip and what a screen reader hears), the Performance calendar (the period's days only, read with the arrow keys), a run's "Weekday and hour"; the bell's events leading to their order and read once followed; ⌘K by its shortcut and its button (a turbo found by "24150", runs, rules and a script by "ema cross", each opened); the backtest form's asset list searched; a live rule's Delete hint on a phone; a version of the configuration saved, opened, restored after the slide, the ledger then listing the file the toast named; two runs compared, "Only what differs" leaving their one different input and the figures; the day's profit and loss and a position's live values settling on their digits.
- Fixed after that look: the rolling digits' columns were one digit tall, so a roll moved them a tenth of a digit and showed the wrong ones; the calendar's months, the heatmap's cells when a run traded in two hours, a value cut after its minus sign on a phone, a tooltip's arrow beside its button.
- Not seen: a real tap (the pane's taps are clicks: the tests cover the touch path), a screen reader, 1,440 px, the editor test's asset list in a browser (its test covers it), the events of the real services, the server.

## v0.31.0 - Admin: EasyUI's 404, a run's parts as a nav, a rule in steps, timelines, a selection basket

**Release date:** 2026-10-23, with v0.33.0

Five of EasyUI's components where the user asked for them. The admin interface only: nothing the trading services, the TA engine or the server's routes do changes.

- **Every page whose address names nothing shows EasyUI's new 404** (a frosted folder over an orange "404", redesigned since the copy of 11 October): the address that was asked, the way to the dashboard and back, and up to three pages it may have meant. Besides the router's catch-all (which suggests the page of the menu the address was under), a backtest, a day it did not cover, a script, an asset or a provider that does not exist now say so in it, named in the top bar's trail ("Backtests › No such backtest") and the tab, where they showed "Could not load" or a panel of their own. `/data/providers/constructor` no longer reads as a provider.
- **A run's parts are a nav that follows the scroll** (EasyUI's `scroll-progress-nav`): numbered, the part being read lit, a line of how far down the page is, a way back to the top. It stays at the top of the page, on a phone too as the part being read and a menu of the others. Each part is still a link (`#edge`), opened in another tab at its place; a click scrolls to it and puts it in the address without a step more to go back through. Trades, Signals and Days stay tabs.
- **A signal rule is made in four steps** (EasyUI's `stepper`, as a backtest is): the script, its market (asset, bars, indice, days of history), its inputs and alerts, then the review, unchanged. What is wrong is said under its step; the review lists what is left, each leading back to its step, and the rule is made only once nothing is. Changing a rule takes the same steps. "Run v3" opens on the inputs and alerts, what a new version may change; "Run Helpers v2" on the review.
- **An order's moments are a timeline** (EasyUI's `interactive-timeline`), on the Orders page: each step's moment and note, open; what went wrong in red, what did not end as it should in amber; the first of them, or else the last step, picked out. **A position's events are one timeline**: the moments of the order that opened it, the sells Saxo did not take, then those of the order that closed it, each marked Entry or Exit, under the two orders' lines and sentences.
- **What is selected in the backtests and the signal rules is in a basket** (EasyUI's `selection-basket`) that stays at the bottom of the screen while the list is on it: how many, all of them (every row the search and filters leave, on every page), none, and what can be done with them.
  - Backtests: Compare (a link, from two to four finished runs) and Remove, which leave the top of the page. A run that waits or runs has no box, as before.
  - Signal rules: Switch off, Switch to shadow, Delete (after a confirmation that lists them). One rule after the other, each with the revision the last answer gave; the first refusal stops it and says how far it went. A live rule has no box: going live, or leaving it, stays one rule at a time with its confirmation. The table's boxes did nothing until now; the header's is disabled while no rule of the page can be chosen.
  - The signal journal's rows lose their boxes: records, with nothing to do with several at once (they did nothing either).

My calls: the 404's way back uses the admin's own buttons; a live rule and a waiting run cannot be selected; a newer script version opens the rule on its inputs and alerts. The components were copied from EasyUI's commit `50effaf` of 23 October and given a light theme, links where they lead elsewhere and the admin's focus ring (`admin_ui/README.md` lists each change).

**Verification status:**
- 947 interface tests pass, 22 of them new (the not-found pages and their trail, the nav's parts and a click on one, the rule's steps and where each start opens, the bulk changes of rules with their revisions and a refusal, the basket of runs, the table's held selection, the timelines' steps); lint, type checks and the build are clean. No Python changed; the Pine runner and checker are untouched.
- In a browser against the fake admin, light and dark at 1,440 px and a phone's 375 px: the 404 of an unknown address, backtest, script, asset and provider (trail, tab, address, suggestions); a run's nav (all ten parts fit at 1,440 px, the part being read lit as the page scrolls, a click puts `#curves` in the address and lands the part just under the nav, the phone's pill and menu, nothing scrolling sideways); a rule made through the four steps, two rules switched off then deleted from the basket, a live rule without a box; two runs removed from the basket, Compare disabled with its reason; a position's timeline and an order's, one that gave no position in red.
- Not seen: the smooth scroll itself (the browser pane was hidden, which stops a page's animations: where a part lands was checked with an instant scroll), Compare as a link with two finished runs (the tests cover it), the basket in the dark theme, a position's refused sells in a browser (the tests cover them), the server.

## v0.30.0 - Technical Analysis: each new bar continues its rule's run

**Release date:** 2026-10-23, with v0.33.0

The TA engine ran a rule's script over all of its bars at each new one. On the server that was 2.7 s a run (3.8 s at 95 %) for the user's Kernel + Avg multi VWAP rule on R60, and more as the day went on: about 5,600 bars at 08:47 in Paris with a day of history, 9,500 by the evening. The kernel library was 4 % of it; the rest was PineTS running every bar again. Each new bar now continues the run where it stopped.

### Continued runs

- **A rule's new bars continue its run** (`continued` in the load the engine sends, `Runner.continueRun` in `admin_ui/src/features/ta/engine/runnerCore.ts`). The runner keeps the PineTS and the context of the rule's whole run, and runs each new bar on them, as a whole run runs its last bar: PineTS's own loop (`_executeIterations`) on the same context, the bar added as PineTS adds one (`_appendCandle`). These are PineTS's private members, checked at each load: a PineTS without them runs every rule whole, as before.
- **Measured on the user's rule** (script v5 with its KernelFunctions library, R60 on capital/US100, the bars of 21 and 22 October read through the MCP), with the built runner and the shadow runner's heap of 256 MB: loaded on the 4,263 bars before the restart, then 5,213 new bars one at a time, the day's every alert was the whole run's (5,238 of 5,238, the 13 that make signals among them). 0.34 ms a bar on the Mac (3 ms at most), where a whole run of the day's 9,476 bars took 2.0 s there and about 4.6 s on the server.
- **What still runs whole at each new bar**: a script that reads what a whole run gives otherwise at an earlier bar (`wholeBecause`, read in its code, comments and strings left out): where the bars end (`barstate` but `isfirst`, `last_bar_index`, `last_bar_time`, `session.islastbar`), the clock (`timenow`, `math.random`), another series (`request.*`), the chart's visible range, and a `strategy()`. Its rule says why: `whole` in its state, said in the Engine column of the Signal rules page.
- **Checked against a whole run**: at a rule's first new bar, then every ten minutes (`CHECK_SECONDS`), at the end of a tick that leaves the time it should take (its last whole run's and a quarter more), or anyway once ten minutes late; one check a tick, live rules first. The whole run's alerts and plots (keyed as PineTS keys them, two plots of one title apart) are compared with the continued run's over the bars since the last check; the same, the next bars continue the whole run's context. On the user's rule, a check every 60 bars over the day: 87 checks, all the same, 1.3 s each on the Mac.
- **A difference**, or a whole run that fails where the continued one went on, makes the rule run whole at each new bar until the engine starts again. It is said on Telegram once (once a day for a shadow rule an agent made).
- **Memory**: a held run keeps its memory between bars (56 MB of heap for the user's rule at 9,476 bars, 65 MB with one held, 175 MB with three). After a load or a check, a runner whose heap is past half of its old space (`HELD_HEAP_SHARE`, measured after a collection the runner makes itself, its `gc` in a context of its own that no script sees) puts its run of the most bars back to whole runs. A runner that ends while it holds continued runs (its memory, a timeout) runs its rules whole until the next `restart_time`.
- **Unchanged**: replays, backtests, studies and paper days load whole days, as before; the backtests' code digest is the same, so no run is marked stale. The Pine runner's answers kept per day for backtests (`_signal_cache`) are keyed by the runner and are made again once.

### The Engine monitor

- **Each rule's runs say how many continued its run**, and its checks against a whole run: how many, the longest, and the last difference (the Rules table).
- **A new check of the last 15 minutes, "Continued runs as whole runs"**, fails when a whole run gave otherwise than a live rule's continued run, and health says it; a shadow rule's difference is a note.
- **The ticks that checked a continued run are left out of "Ticks on time"**, as those that loaded a rule are: a whole run more, by design.
- **The charts of the runs say what a break in their lines is**: a time in which no rule had a new bar to run, so nothing was measured. On range bars, a quiet price: on 23 October between 10:52 and 11:55 in Paris, the only minutes without an R60 bar of US100 were 11:03 and 11:14, where the lines broke.
- The checks are kept in their tick's `figures.checks` (`ta_engine_tick`); a run's continuation in its row's `figures` (`ta_rule_run`, which the engine now writes).

**Deploying it:** nothing to set. `package.sh` builds the runner again.

**Verification status:** 2,366 Python tests pass, the built runner's among them (Node 24: a rule's continued appends against a whole runner's, bar by bar, then a check); the 16 skipped need a database and a queue of their own. On a real PostgreSQL 16 (pgserver), the TA engine's live tests, a continued run and a check that differed among them, written and read back by the monitor's queries. 924 admin interface tests, the type check and the lint pass; in the runner's, a script of many built-ins (averages, bands, an anchored VWAP, `var` state and arrays, a library's weights made on the first bar, times in Paris, labels, `alert()`) continued bar by bar answers what a whole run answers. The user's rule on the real bars of 21 and 22 October, as above. The Engine monitor's Rules table seen on the dev server.

**Not seen:** the engine on the server: a trading day of continued runs, a check's time there, the shadow runner's memory holding a run.

## v0.29.1 - A deploy no longer sets off the TA engine's feed warning, and the engine gets 2.5 GB

**Release date:** 2026-10-22

- **The feed check leaves out the engine's first tick after a start.** At the deploy of v0.29.0, the engine's first tick found the Capital.com feed 99 s behind, the feed having stopped with it, and health said a TA engine warning for the next 15 minutes; every tick after it was 1 to 2 s behind. The engine now marks that tick (`figures.first` of its `ta_engine_tick` row), and the feed's check, its tile and health leave it out. The charts still show it. The daily restart is not a start: the engine's process and the feed keep running.
- **The TA engine's container has 2.5 GB** (`2560M`, was `1024M`; held by `tests/test_deploy_compose.py`). Its first measures on the server: a shadow runner of 256 MB of heap held 729 MB, the native memory PineTS's compiles keep beside the heap, and the container 768 MB of its 1,024, where the memory check fails at 85 %. The runners' heaps are unchanged (`runner_memory_mb` 512, `shadow_runner_memory_mb` 256 by default).

**Deploying it:** nothing to set; the container takes its new memory when `watastart` creates it again.

**Verification status:** 2,357 Python tests pass; the 15 skipped need a database and a queue of their own. On a real PostgreSQL 16 (pgserver), the TA engine's live tests, a first tick 99 s behind among them, left out of how far the feed was.

**Not seen:** a deploy without the warning; the container's 2.5 GB on the server.

## v0.29.0 - Technical Analysis: the Engine monitor

**Release date:** 2026-10-22, with v0.27.0 and v0.28.0

How fast the TA engine runs each rule, how late its signals are, and what became of them, on a page of its own. The engine measures itself to that end and keeps it a week; the signals' delivery and results are read from the journals WATA already keeps, which nothing deletes.

- **The Engine monitor** (Technical Analysis › Engine monitor, `/ta/monitor`):
  - **The last 15 minutes**, checked against WATA's own limits, a check failing at half of its limit: the live signals sent; the live bars run in time (a signal is refused past `max_signal_age_seconds`); the runs on the live runner within their limit (`run_timeout_seconds`, or the rule's bar when shorter); the ticks within their ten seconds; the Capital.com feed up to date (`stale_feed_seconds`); the container's memory under 85 %. Each check leads to the part of the page that says more, five tiles set the figures against their limits, and the page notes what waits on no live signal (a shadow rule waiting its turn, a script that fails, the shadow runner's troubles, measures that could not be written).
  - **Health says the first check that failed**: the TA engine's OK becomes a warning with its sentence in the top bar, on the System page and in the Signal rules page's engine panel, only for what can cost a live signal. The 15 minutes keep it from coming and going: one notice when it starts, one when it ends.
  - **A period**, from an hour to the days kept (1 h, 6 h, 24 h, 3 days, 7 days as tabs that are links), for every rule or one (`?hours=`, `?rule=`):
    - each rule's runs, their time against its limit (median, 95 %, slowest), PineTS's share, its loads and failures, the ticks a shadow rule waited, its signals;
    - over time, the restarts marked: run time, bar close to signals done, tick time, how far the feed was behind, the memory of the container, the runners and the engine, the signals by what became of them;
    - the delivery of the live signals step by step, all on the server's clock: from the bar's close to the engine's tick, the run, the web server's answer, then the trader's order, Saxo's acceptance and the position; what the engine did with each signal (refusals by reason) and what the trader made of those sent;
    - the results: the positions of a live rule's signals, a shadow rule's days on paper.
  - The Signal rules page leads to it (its engine panel, each rule's last run time), and so does the TA engine's name on the System page.
- **The engine measures itself** (`src/ta_engine/monitoring.py`, tables `ta_engine_tick` and `ta_rule_run`):
  - each tick: its time, what it read and wrote, how far the feed had come, the shadow rules left for the next one, the memory of each runner, of the engine and of the container (read from Linux);
  - each run of a rule: the runner's time and PineTS's own, the bars given and the close of the newest, the series the script asked for, the signals it raised. A signal finds its run by its tick: its `raised_at` is the run's `tick_at`.
  - Written in the background once the tick's signals are sent: nothing waits for them, and a database too slow to take them makes the engine drop them, counted and said on the page, never a signal.
  - Kept `trade.config.ta_engine.monitoring_days` (7 by default, 1 to 30), the older ones removed at the engine's start and at each `restart_time`. The admin only reads them: no research export, no MCP tool.
- **A runner gone between two ticks no longer reads as a script that failed.** When a runner process ended while the engine waited (stopped by the kernel for memory, say), the next tick started it again but gave its new bars to the rules it no longer held: each failed with "The script failed on a new bar: No rule … is loaded", said on Telegram, before loading again. The rules now load again at once, with nothing said. The bars the ended runner never evaluated stay unsent, as before: a load's alerts are never signals.
- **Every backtest run shows "Made with other code" once.** The code a run rests on counted the whole of `src/ta_engine`, the live loop that no backtest runs included, so this release marks every run as made with other code (the verdict's code check goes to open; nothing is lost, and a run made again says whether it still holds). It now counts the files a replay runs (`replay`, `rules`, `runner`, `tickers`, `signals`, `settings`, `bars`, `source`): a later change to the live engine alone leaves runs as they are. A test fails on a file of `src/ta_engine` the backtest code imports that the list leaves out.
- The fake admin (`npm run dev:api`) has a live rule and one of Claude's in shadow on ten-second bars, a week of measures, and `FAKE_TA_TROUBLE=slow` for a slow live run.

**Deploying it:** nothing to set. The engine makes its two tables at its next start and keeps a week unless `monitoring_days` says otherwise; until it runs this version, the page says nothing was measured yet and shows the journals' delivery and results alone. `VERSION` goes from 0.27.0 to 0.29.0: v0.28.0, committed without a bump, comes with it.

**Verification status:**
- 2,355 Python tests pass, the TA engine's on the real Pine runner among them. 72 tests are new: the engine's measures and their writer (a store that refuses or hangs costs no signal), the tables and their SQL, the checks at their boundaries, the routes and health, the code digest, the fix (which fails without it). The 2 skipped need what this Mac's run lacks (the lab's real `asyncpg`, the TradingView checker's build).
- 912 interface tests pass, 27 of them new (the monitor's model and page, the menu, the Signal rules page's links); lint, type checks, the build and `build:runner` are clean.
- On a real PostgreSQL 16 (pgserver): the live tests of the TA engine's tables, a signal joined to its run to the microsecond among them, and the purge. With a week of measures of five rules, two on ten-second bars (60,481 ticks, 151,205 runs, 46 MB), the page reads a week in 0.55 s and the checks of the last 15 minutes take 2 ms; with a month (259,201 ticks, 648,005 runs, 195 MB), the 30 days take 2 to 2.5 s, which the page reads every 5 minutes (a day, every minute).
- In a browser against the fake admin, light and dark, at 1,440 px and on a phone's 375 px: every part of the page, the period tabs and the rule filter, the menu's entry and the links from Signal rules and System; with `FAKE_TA_TROUBLE=slow`, the warning of the System page and the failed check with the same sentence. On the phone, nothing scrolls sideways.

**Not seen:** the memory read on the server (`/proc` and cgroup v2, tested on files), the container's limit and the kernel's OOM kills, the real runners' memory; the real feed's lag; the trader's and Saxo's steps with real orders; the warning's notices with the real engine; the engine stopping in Docker. The thresholds may want tuning after a week on the server.

## v0.28.0 - Technical Analysis and Backtests: from a script to a live rule, and whether to believe a run

**Release date:** 2026-10-22, with v0.29.0

The first batch of a review of the Technical Analysis and Backtests pages, for the way a strategy goes: a script, its test, its backtests, a rule in shadow, its days on paper, live. The admin interface only, with one change to the pages' Pine worker: nothing the trading services or the TA engine run changes.

- **Whether a run can be believed, at the top of its page.** A run is checked against the bar Claude's research is held to (the MCP's instructions), each check saying what it found and leading to the part of the page that says more. The pages and Claude now judge a run alike.
  - For a run: at least 30 trades over 20 days, random entries beaten 95 % of the time, an edge in its entries (t of 2.6 at its strongest horizon), its result still above zero a step later and dearer (when it has been stressed), made-up turbos checked against recorded ones (for a run on them), today's code.
  - For a sweep: the choice held on the days held out, its probability of overfitting (0.5 at most), its deflated Sharpe ratio (95 %).
  - The checks say "Not to be believed yet", "Nothing against it so far" with what is still open, or that every check passes. The sentence on too few trades moves into the first check.
- **A run's page has an index of its parts** (Verdict, Trust, Edge, Stress, Day after day, Why, Where it comes from, the tables, What it ran), staying at its top from a tablet up. Each part has an address (`#edge`), which opens the page at it.
- **The steps of a strategy lead to each other.**
  - The script editor has "Backtest v5" (the form filled with that script and version) and "New signal rule" (the rule's dialog opened on it), beside "Open in Charts"; a row of the Indicators list has the backtest too.
  - A run of a Pine script has "Make a shadow rule": the rule's dialog filled with its script version, inputs, alerts, price and bars, made in shadow once reviewed. A run on Dukascopy's us100 becomes a rule on Capital.com's US100, its range counted in that tick: R6000 is R60.
  - The script a run traded is a link on the run's page.
  - The form takes `?script=<id>&version=<n>`, and the Signal rules page `?new=<script>` and `?from-run=<run>`: links like any other.
- **The editor's test draws what the script did**, under the editor across the page: the day's bars, its plots near the prices as lines (four at most, named in a legend; an oscillator off the prices' scale is listed apart), its alerts on their bars. A click on an alert in the list brings it into view. The pages' Pine worker now hands back the plots' numbers of the day it is asked for (`series` of a run); nothing else asks for them.
- **Going live says whether the rule is ready**, in the confirmation, and never stops the switch:
  - a week in shadow;
  - signals journaled;
  - its days on paper within the range of its reference run;
  - the engine running it without fault;
  - the latest version of its script.
- **The TradingView comparison goes from day to day** with ◀ and ▶, the weekends stepped over.
- **Every price chart follows a change of theme**: those of a backtest's day and of one of its trades, and the TradingView comparison's, are drawn again in the other theme, as an asset's chart already was. They kept the colours of the theme they were made in.
- **The list of runs narrows by state, by who made them, experiment, turbos and code**, in its address. Each experiment's runs count is a link to that list. The failed attempts of a request are folded under the run that came after them ("9 earlier failed attempts", each with what it said), and can be removed from there: on the server, 13 of the 32 runs were such attempts.

**Verification status:**
- 885 interface tests pass, 33 of them new, among them a PineTS run on the plots' numbers of a day and the comparison's first tests; each chart's test of another theme fails without its fix. Lint, type checks, the build, `build:runner` and `build:checker` are clean.
- The runner is built from code the change touches (`pineRun.ts`, whose summary it does not use): the TA engine's and the replay's 91 Python tests pass on it, none skipped.
- In a browser against the fake admin, in the light theme, at 1,440 px and on a phone's 375 px: a script tested (its two averages drawn over the day, an alert brought into view from the list), backtested from the editor's button, the run read from its checks down (three failed, the stress one open) and an index link followed to its part, below the index; the rule's dialog opened from that run (its script version, inputs, alerts and bars filled in; not saved), the live confirmation's checks, the comparison stepped back three weekdays, the list narrowed from its address. The list fits a 1,440 px screen. On the phone the index stays in the page's flow and nothing scrolls sideways.

**Not seen:** the dark theme of the new parts (their colours are the theme's tokens); failed attempts folded in a browser (the fake admin had none) and a stress check that passes; the server.

## v0.27.0 - The admin: links that open in another tab, charts in full screen, versions side by side

**Release date:** 2026-10-22, with v0.29.0

The admin interface only: nothing the trading services run changes.

- **What leads to another page is a link**, so that it can be opened in another tab (a right click, ⌘ or Ctrl and a click) as well as followed. Every entry of the menu was a button. So were New indicator, New strategy and New library, New backtest, Experiments and Compare, "Run again with changes", a backtest's day before and after, its trades and the purchases of its signals on a day (a plain click still opens a trade in place, and a second one closes it), Dukascopy's "Set up" in the provider catalog, and the way back to the dashboard from a page that does not exist. Tabs whose view has an address are links too: a run's Trades, Signals and Days (`?tab=`, new), the positions' Open, Closed and All (`?status=`, new), an asset's tabs. A click with a modifier is left to the browser and leaves the page as it was. The Configuration page's tabs stay buttons: they are the modes of one editor, whose unsaved changes another tab would not have.
- **The one-time code of the login page is six boxes** (EasyUI's `OTPInput`), drawn like the password field. Each digit moves on to the next box, Backspace goes back, a pasted code fills them all, and the sixth digit signs in once the password is there (Enter still does). A code filled in whole by a password manager or a phone fills every box: EasyUI's boxes kept one character, which would have cut such a code to its last digit.
- **Indicators**: the Libraries column lists one name a line, each a link to its script. A library's users made one long line there, which pushed the table past the page and the actions out of sight, behind a hidden scroll bar. The columns of short values take what they need, the scripts and their libraries share the rest, and a row's cells start at its top. The table fits without scrolling down to a 1,280 px screen with the sidebar open; a phone shows cards, as before.
- **A script's History is as wide as the screen**: the version chosen and the editor's text side by side, about 650 px each on a 1,440 px screen where they had 200, with line numbers, long lines wrapped, each side named above it. The dialog keeps its height from one version to the next.
- **Charts go full screen**: the chart and its controls over the whole window, and the window over the whole screen where the browser allows it. The page itself is made full screen, so Vela's menus and the admin's notices stay in sight. Escape leaves it, after closing an open menu of Vela's first. On an iPhone, which has no full screen for a page, the chart fills the window.

**Verification status:**
- 852 tests pass in the interface's suite (9 new: the menu's links and a click with Ctrl, the code boxes and the sign-in by the sixth digit, a whole code at once, the history dialog, full screen with and without the browser's, a run's tabs); lint, type checks and the build are clean.
- In a browser against the fake admin, light and dark, at 1,440 px and on a phone's 375 px, with four scripts shaped like the server's (their names, imports and description lengths, read from its list): the Indicators table, the history of a script saved twice, the chart in full screen and back with Vela's indicator menu opened over it, the sign-in with the code boxes (five digits arriving at once, then the sixth), the menu's links followed in place without reloading the page.

**Not seen:** a browser's own full screen (the browser used grants none to a page, so the chart filling the window is what was seen); "Open in new tab" from a right click (a ⌘-click was left to the browser, which opened the link's address); a phone's or a password manager's autofill of the code; the server.

## v0.26.3 - PineTS 0.12.0, Vela 0.8.5 and Vela-pinets 0.2.17

**Release date:** 2026-10-21, with v0.26.1 and v0.26.2

The three LuxAlgo packages of the Technical Analysis pages and the TA engine, released on 2026-10-19, pinned exactly as before (`admin_ui/package.json`). Nothing else changes in the lock file; their licences and Vela's NOTICE are the same.

- **PineTS 0.12.0** (from 0.11.1) mostly rebuilds how a `strategy()` fills its orders (TradingView's path inside a bar, margin, risk rules), which WATA does not use: only alerts make signals. Beside it come fixes that bring scripts closer to TradingView: `ta.cross*` inside a lazy `and` or `or`, `ta.*` inside local blocks and on sources that start with `na`, `request.security` in a function called from several places, `(expr)[n]` inside `and`, `or` and `?:`, `x := switch`, `math.round_to_mintick`, `varip`. They can change what another script raises; they did not change what the Kernel VWAP scripts raise (below).
- **Vela-pinets 0.2.17** (from 0.2.16) carries PineTS 0.12.0 in its worker, the one the Charts page runs scripts in, and now moves a `plot()` by its `offset`, which it drew unshifted.
- **Vela 0.8.5** (from 0.8.4): panes keep the height they were dragged to when an indicator is added, an overlay can be brought in front of the candles from its legend, built-in studies wait for the whole history before they compute instead of sliding while it loads, and a plot shifted forward draws past the last candle. Linear Regression becomes two studies, Least Squares Moving Average and Linear Regression Slope; a saved chart opens with the one it had.
- **WATA's three changes to the packages at build time still find their code**, unchanged: seconds on Vela's chart (`vite/velaSeconds.ts`), a formatter per time zone in each copy of PineTS (`vite/pinetsZoneFormat.ts`), Vela-pinets' worker as a file of its own (`vite/velaPineWorker.ts`). The runner's hello says `0.12.0+zone-format`.

**On the live path**: the TA engine's runner is built with PineTS 0.12.0 at the deploy, so every signal rule, live or shadow, runs on it from then on: on the scripts compared below its alerts are the same as 0.11.1's, and it is about 5 % slower. The trader, the web server and the position monitor are untouched, and TradingView's alerts never go through PineTS. Backtests made before are not marked stale (the code digest covers the Python code; each run names its PineTS in `engine.runner`), and the runner's day cache starts empty after the deploy: its keys hold the runner's build.

**Verification status:**
- The interface's 843 tests pass, the live strategy's among them (it reads `wata-tradingview-scripts`); lint, type checks, the build, `build:runner` and `build:checker` are clean, and `python -m src.ta_engine.runner` answers with PineTS 0.12.0. The Python suite passes with the new runner and checker built (2,285 tests; those that need a PostgreSQL, a RabbitMQ or `pgserver` skipped, as without them before).
- The same signals: `python -m src.ta_engine.replay` run with the runner built from 0.11.1 and from 0.12.0, on the three Dukascopy days of this Mac (2026-09-15, 09-17 and 09-22), for the lean and the live copies of the Kernel VWAP script (the live one's providers on the chart's own series) and `kernel-vwap.pine`, on R6000, 1-minute and 30-second bars: 21,157 alerts, every one and every signal the same. The same replay took 8.3 s with 0.12.0 and 7.9 s with 0.11.1.
- In a browser, against the fake admin built with them: Capital.com's US100 live on 10-second bars with the seconds on the crosshair, a Pine script and its alerts drawn through the new worker file, the editor's test, a built-in study of Vela drawn at once.

**Not seen:** the server; Capital.com's real prices and the recorded turbos with PineTS 0.12.0; Vela's new pane and layer controls.

## v0.26.2 - Long Pine backtests no longer run out of memory, and research gets more of the server

**Release date:** 2026-10-21, with v0.26.3

### The Pine runner

- **The Pine runner compiles a rule's script once while its source and inputs stay the same** (`load` in `admin_ui/src/features/ta/engine/runnerCore.ts`), where it compiled it at every load. Each PineTS compile leaves native memory behind that the process never gets back, about 60 MB for the Kernel VWAP script. That memory lies outside V8's heap, so `--max-old-space-size` does not bound it: with a heap of 512 MB instead of 1,024 the runner grew the same way, to 1.3 GB. A backtest loads its script again for each engine day. On the server the runner of the 90-day Kernel VWAP run was killed by the kernel every 10 to 20 engine days in the admin's 1,536 MB, and the run finished only on its tenth attempt, each one reading the days already done from the day cache. Each day also ran slower than the one before, 2.8 s on the first and 9.7 s on the thirtieth of the same size.
- Now, over 90 engine days of that script, the runner holds 405 to 450 MB and runs a day in about 1 s.
- **The TA engine's runner gains too**: the engine loads each rule again at its daily restart, with the same script and inputs.

### Memory limits for a server of 8 GB

WATA used about 1.25 GB of the server's 8 GB at rest, and the two limits that were reached were the admin's and PostgreSQL's (`deploy/docker-compose.yml`):

- **The admin container gets 3 GB** (1.5 GB before). A backtest, its Pine runner and one of Claude's Pine studies fit side by side. It keeps its one processor, so the services that trade keep the rest.
- **PostgreSQL gets 1.5 GB** (512 MB before) and settings of its own, where it ran on the image's defaults: `shared_buffers` 512 MB (128 MB), `effective_cache_size` 1 GB, `work_mem` 16 MB (4 MB), and a `/dev/shm` of 256 MB (64 MB) for the memory parallel queries share. More of the price tables that backtests and the Data pages read stays in memory.
- **A Pine study gets a heap of 768 MB** (`admin.mcp.study_memory_mb`, 384 before, still 128 to 1,024).
- **The free memory a study waits for leaves out the file cache the kernel takes back** (`inactive_file` of the container's `memory.stat`, `cgroup_memory` in `src/admin/studies.py`). The price files and the runner's day cache the admin reads filled its memory with cache, and a study could be refused with nothing really short. A run that dies says the admin's memory then on the same measure.
- The other containers keep their limits; the trader has none.

**On the live path**: the TA engine's runner keeps a rule's compiled script when the rule is loaded again unchanged; a rule with another version or other inputs is compiled again. The deploy recreates the PostgreSQL container with its new settings, so the database restarts: deploy while the market is closed. The trader, the web server and the position monitor are untouched. Backtests give the same signals and are not marked stale (the code digest covers the Python code), but the runner's day cache starts empty after the deploy: its keys hold the runner's build.

**Verification status:**
- A test of the runner core (`runnerCore.test.ts`): a rule loaded twice with the same script and inputs makes one indicator and answers what a fresh compile answers (alerts, plots, inputs); other inputs, or another rule, are compiled for themselves. It fails without the change. 843 tests pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- Measured on this Mac with the built runner and the Kernel VWAP scripts (the lean copy and the full one with `request.security`) on 30 to 90 made-up Capital.com days of 5,300 R60 bars: the same alerts on every day before and after the change, memory flat, a day in 0.9 to 1.7 s. Before it: 1.2 GB and 9.7 s by the thirtieth day.
- `tests/test_deploy_compose.py` holds both containers' limits and PostgreSQL's settings; a test reads the container's memory with and without its inactive file cache.

**Not seen:** the server. After the deploy:
- the limits: 3 GiB for `admin1` and 1.5 GiB for `postgres` (`docker stats`), `SHOW shared_buffers` 512MB in the database;
- a Pine backtest of more than three weeks finishing at its first attempt, the runner's peak (`runner_peak_mb` in its progress) under about 500 MB;
- `admin.mcp.study_memory_mb` in the server's `config.json`: where it says 384 (the example's value when the deploy of v0.23.0 added `admin.mcp`), it stays 384 until it is changed to 768 or removed. The MCP `status` tool shows the value in use (`limits`).

## v0.26.1 - certifi no longer trusts the compromised emSign root

**Release date:** 2026-10-21, deployed with v0.26.3

- **certifi 2026.10.19** (from 2024.7.4), for GitHub's critical advisory GHSA-6q48-6c7w-9gw2 (Dependabot alert 46 on `main`). The bundle `requests` and `httpx` verify every HTTPS connection with trusted "emSign ECC Root CA - G3", eight of whose intermediate certificates had their private keys compromised: whoever holds one, and sits on the network path, could pass for any site. WATA's connections that go through it are Saxo's REST API and login (the trader's balances and orders), Capital.com's API, Telegram and Dukascopy. The new bundle drops that root and keeps the three emSign roots not affected (120 certificates where there were 147).
- Nothing else changes: certifi needs no other package, and `requirements.txt` stays the whole environment (`tests/requirements_lock.py`).

**On the live path**: the image's library layer is rebuilt at the next deploy; no code changes.

**Verification status:**
- A fresh environment of `requirements.txt` is exactly what it lists, and 2,281 tests pass in it (1 skipped), on Python 3.12 with Node 24 on the PATH.
- With the new bundle, TLS verifies to every outside host WATA names, as with the old one: `gateway.saxobank.com`, `live.logonvalidation.net` and `live-streaming.saxobank.com` (DigiCert), `api-capital.backend-capital.com` (GlobalSign), `api-streaming-capital.backend-capital.com` (Amazon), `api.telegram.org` (GoDaddy), `datafeed.dukascopy.com` (ZeroSSL).

**Not seen:** the server. At the deploy:
- the image's certifi: 2026.10.19;
- the image's Debian store, which Saxo's and Capital.com's streams verify with (`websockets`, not certifi): whether `/etc/ssl/certs/ca-certificates.crt` still holds "emSign ECC Root CA - G3". The `python:3.14` base is pulled again at each build, so Debian's fix arrives with it;
- pip's own copy of certifi, used only while the image is built.

## v0.26.0 - Signal rules on seconds bars, model checks, and trials made elsewhere

**Release date:** 2026-10-21, with v0.24.0 and v0.25.0

The gaps marked "later" after the first research session through the MCP endpoint (2026-10-21).

- **Signal rules run on bars of any length**, live and shadow, from 10 seconds to a day: the Signal rules dialog offers 10, 20, 30 and 45 s and "Other length…", as the Backtests form does. One check for every rule, backtest and replay (`checked_bars`). The engine was already able to: it ticks every 10 s and builds bars of any length. A rule on 10-second bars runs its script over up to 8,300 bars a day at each new one, about what one on R60 does. A signal its run delays past `max_signal_age_seconds` (60 s) is not sent, journaled with why; Claude's shadow rules stay held to `admin.mcp.shadow_day_ms` an engine day. `rule_shadow_save` takes them (`bars="10S"`).
- **A model check**: a finished run on the recorded turbos made again on turbos made up from the index, without random entries, and its trades paired with the recorded run's by signal. It says how far apart their results are, the correlation, the same exit and both totals. "Check the turbo model" on the run's page (once the turbos are measured), MCP `backtest_derive` kind `model_check` (no trial counted) then `backtest_result` part `model_check`. The comparison is the lab's own (`compare_trades`); the check's folder keeps the recorded run's trades (`recorded_trades.json`) and writes `model_check.json`.
- **Trials made elsewhere count in the journal**: MCP `exp_trials` and `python -m lab exp trials <id> --count N --notes "…"` write down configurations tried outside the runs (a port screened on the Mac, by hand). They count in the family's trials, so in later sweeps' overfitting figures, and the Experiments page lists them with what they were. The 35 variants of the first session can now be counted. 39 tools.
- **The MCP's day budget counts trading days**: weekdays outside the live closed dates, where it counted every calendar day. 120 days with 100 random repetitions fit within `max_day_simulations` (about 86 × 101 = 8,686 of 10,000), where 81 repetitions did before.
- **Every backtest starts with 1,000 €** unless it says another (`DEFAULT_CAPITAL`, 10,000 € before, used when no balance was known): the live balance is no longer handed in, but offered beside it ("start from it" under the form's account; `backtest_options`). At 21.82 €, 34 entries of the session found no turbo to buy. Paper days start with it too; runs made before keep theirs.
- **Not done**, as decided: a second live price source for multi-provider scripts (`request.security` reads Capital.com only).

**On the live path**: the TA engine accepts rules on seconds bars, inert until one is made; the check of a rule's bars is shared by the admin and the engine. The trader, the web server and the position monitor are untouched. Every backtest made before shows as stale (the code digest); its results are unchanged at the same capital.

**Verification status:**
- 2,281 tests pass in Python's suite, 1 skipped, on Python 3.12 with Node 24 on the PATH. Not run on Python 3.14 here: CI runs both. 842 pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- **At the same capital, a run gives the same files as v0.25.0's code**: a run of the EMA cross on the fake admin's recorded days (10 trades, 10 random repetitions), replayed by both versions on the same files. Trades, signals, days, random entries and edge are identical; the summary differs only in `made_with` and the new `model_check` (empty).
- Through the MCP SDK's client, on the fake admin (`FAKE_MCP=1`):
  - `exp_trials` wrote down 35 trials made elsewhere, and the next sweep's `trials_before` was 36;
  - a run without a capital started with 1,000 €;
  - a model check derived from it ran and paired its 6 made-up trades with the recorded ones by signal (`backtest_result` part `model_check`), on a made-up measure;
  - 120 days of made-up turbos with 100 random repetitions were accepted;
  - a shadow rule on 10-second bars was saved after its first run, 8,250 bars in 83 ms.
- In the browser pane:
  - the recorded run's page shows the check with what its trades say, and the check's page its badge, its link and the same sentences;
  - the Experiments page shows "+35 tried elsewhere" and 39 in the family;
  - the Signal rules page lists the 10 s rule;
  - the form's options give 1,000 € with the live balance beside it (the "start from it" link is the interface tests'; the pane, hidden, held the form's steps).
- The engine's tests run a live rule on 10-second bars (sent at the tick after its bar, refused 75 s late) and refuse a rule edited by hand into a length WATA does not make.

**Not seen:** the server. After a deploy:
- a 10 s shadow rule's journal and paper days, and the engine's time per run with it;
- a model check through Claude Code on the server's recorded days, once the turbos are measured again on more days than the four the model rests on;
- `exp_trials` for the 35 variants of the first session, in an open experiment of the family `kernel-avg-vwap` (6a5787, where they were screened, is concluded and refuses them).

## v0.25.0 - No entry around US releases, backtests on WATA's own fills, and MCP calls that end in time

**Release date:** 2026-10-21, with v0.26.0

The gaps marked "soon" after the first research session through the MCP endpoint (2026-10-21).

- **No entry around US releases, once the rule is added** (`macro_news` in `trade.rules`, added and removed from the Configuration page's "US releases" section; absent, nothing changes). From 5 minutes before CPI, the jobs report (NFP), PCE or an FOMC statement to 15 minutes after, and to 45 after an FOMC statement, for its press conference: `{"events": ["FOMC", "CPI", "NFP", "PCE"], "minutes_before": 5, "minutes_after": 15, "minutes_after_fomc": 45}`, which `ConfigurationManager` checks (known releases, whole minutes from 0 to 120, 180 after FOMC).
  - The trader checks it right after the trading hours, before anything is sold or bought, and says why: "Breaking trading rule: no entry around CPI (14:30) from 14:25 to 14:45." A release is at New York's hour whichever side changed its clocks (CPI is at 13:30 in Paris on 11 March). Closes, the scheduler's close of the day and open positions are untouched.
  - The calendar moved to the trading side (`src/trade/macro_calendar.py`, `src/trade/data/macro_calendar.csv`; the backtests and the lab read it from there). The trader reads it once at its start, since it changes only with a deploy; one that cannot be read never stops it: it trades without a window and says so in its log.
  - Past the calendar's last day nothing is known, so the trader trades on. From 30 days before that day, while the rule is on, the scheduler sends a line to Telegram each morning at 09:00 and the admin's health has a "US releases calendar" warning: "The calendar of US releases ends on 2026-12-23, in 30 day(s): update it (python -m lab calendar check), then deploy". PCE is known to 23 December 2026, the others to December 2027.
  - The dashboard's Next entry tile says the window in force ("CPI at 14:30: no entry until 14:45") or the next one of the day (`news` in `/api/overview`), as the trader would (`entryState.ts`).
  - Backtests repeat it at the trader's place and with its words: `news_window` and the `news_*` settings, read from the rule as the services read it, so off by default and in every run made before. `tests/test_backtest_parity.py` holds the two to the same refusals: inside, both edges, FOMC's 45 minutes, outside, past the calendar's end.
- **A backtest may draw its orders from WATA's own** (`execution: measured`, "Order times and prices" in the Backtests form; `fixed`, the default, is unchanged). The service reads WATA's orders of the last three months with the execution measure's SQL and keeps them with the run (`execution.json`). Each order's time to its fill, and its price against the turbo's quote when it left, are drawn from them, seeded by seed, repetition and day as the random entries are: a run made again draws the same. The random entries draw the same way, and a stress step adds its delay and price on top. Fewer than 10 measures of a kind refuse the run, saying which. The summary says it (`made_with.execution`: the period, the orders), and so does the run page.
- **No MCP call lasts a minute.** Claude Code ends an HTTP call after 60 s when the server's entry sets no `timeout`, and the Mac's sets none.
  - The six tools that run a Pine study (`pine_check`, `pine_run`, `pine_signals`, `pine_compare`, `pine_save`, `rule_shadow_save`) answer within 45 s, or hand back a job while the study goes on: `{"job": ..., "state": "running", "next": "job_wait with this job, until it gives the tool's answer"}`. A new tool, `job_wait`, waits up to 45 s and gives that tool's own answer, or its refusal, once there is one (38 tools). A job is its token's, kept 15 minutes after it ends, 5 at most, and lost when the admin restarts. A study refused behind one still at work names it.
  - Every other tool answers within 50 s (100 before), and `backtest_wait` waits 45 s at most.
- **Answers that fit in Claude's context.** `pine_run` lists only the alerts of `alert_titles` when given, 100 at most by default (`max_alerts`, 0 for the counts alone, 500 at most), and fits what is still too long: its plots' points are thinned, then its alerts listed cut, as its `fitted` says (an every-bar script with two plots on two days of ten-second bars came to 34,649 characters with the defaults, now 31,943 with 258 points a plot); `pine_compare` lists 50 pairs by default (`rows`, 0 for the days and totals alone, 300 at most); `backtest_trades` leaves out the turbos each trade was chosen among unless asked (`candidates`).
- **A week of bars a call**: `data_bars` as CSV covers up to 7 days, 70,000 rows (a week of ten-second bars is about 60,000 rows and 6 MB, which Claude Code saves to a file). JSON keeps its 12,000 bars, Dukascopy its month per view.
- **Why a run or a study died is said.**
  - Before each request to the Pine runner, a backtest notes in its progress the engine day, its bars and the runner's peak memory.
  - A run that ends badly gets a `failure`, said first in its error (the run page, MCP `backtest_list` and `backtest_result`): what its exit code means (−9: stopped by the kernel, most likely for memory, the admin's children going first; −6: Node's heap was full; −15: stopped), the phase and the day it was on with that day's bars, the container's memory then, and whether the kernel's OOM killer acted during the run (`memory.events` read at its start and end).
  - The Pine runner keeps the last lines it wrote to stderr and says them when it ends, V8's "JavaScript heap out of memory" for one; a replay adds the engine day and its bars. A study that dies without an answer says the same.

- **The run page's list of changed settings wraps a long label** instead of pushing the page sideways ("Order times and prices: fixed, or drawn from WATA's own orders" did, by 38 pixels at 1280).
- **The fake admin has WATA's orders for the execution measure** (`execution_rows`, made up from its tracked orders): the form's measured values and a run on measured fills ended in an error 500 there.

**On the live path**: the trader's new check, inert until the rule is added; `ConfigurationManager`'s check of it; the scheduler's reminder, sent only while the rule is on. Every backtest made before shows as stale (the code digest); its results are unchanged with the new settings off.

**Verification status:**
- 2,264 tests pass in Python's suite, 1 skipped, on Python 3.12 with Node 24 on the PATH (the real-runner and checker tests ran). Not run on Python 3.14 here: CI runs both. 837 pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- **Fixed fills give the same files as v0.24.0's code.** A run of the EMA cross rule on the fake admin's five recorded days (5 trades, 100 random repetitions) and a sweep of it (four cells of 5 to 12 trades, stop-losses included), each replayed by both versions on the same files: trades, signals, days, random entries, edge and sweep identical; the summaries differ only in `made_with` (the code's digest, the runner's time, `execution: fixed`).
- The window: unit tests (both edges, FOMC's 45 minutes, 11 March when only New York had changed its clocks, past the calendar's end, a calendar that cannot be read) and the parity test hold the trader and the backtests to the same refusals; a backtest of 14 October (CPI) refuses an entry inside it with the trader's words.
- In the browser pane, on the fake admin (`npm run dev:api`): the "US releases" switch added the rule with its defaults (nine fields in the review, saved with a backup) and removed it. With the rule in the configuration and a one-row scratch calendar, the Next entry tile said "Blocked — CPI at 13:15: no entry until 13:30" inside the window and "Open … No entry around CPI (14:30) from 14:25 to 14:45" before it, and the Services card "US releases calendar" with its sentence. The Backtests form showed the rule's settings as live and "Order times and prices"; a run on measured fills drew from the fake's 101 orders, and its page says so.
- Through the MCP SDK's client, on the fake admin (`FAKE_MCP=1`): a Pine study of 57 s handed back as a job at 45.0 s and answered by `job_wait` 13 s later (the same answer when asked again, an unknown job refused with its sentence, both ends in the call log); `pine_run` of an every-bar script, 31,943 characters fitted, 21,560 with `alert_titles` and `max_alerts` 5; `data_bars` of seven days of Capital.com's ten-second bars, one CSV of 36,957 rows (2.8 MB) in 0.8 s, eight days refused.

**Not seen:** the server, its orders, Telegram. After a deploy:
- add the rule from the Configuration page when wanted, then restart the trader (and the admin, for the tile and the health): they read the configuration at their start;
- the journal's refusals inside the next windows, in Paris: FOMC on 28 October from 18:55 to 19:45, PCE on 29 October from 13:25 to 13:45, NFP on 6 November and CPI on 10 November from 14:25 to 14:45;
- the health's "US releases calendar" (known until 2026-12-23), then its warning and the scheduler's Telegram line from 23 November: the PCE releases after 23 December must be added from bea.gov (`python -m lab calendar check`) before then;
- a long `pine_signals` through Claude Code, answered as a job; a run on measured fills from the server's own orders (its `made_with.execution`).

## v0.24.0 - Range bars that say their size, faster time zones, and one trade a bar

**Release date:** 2026-10-21, with v0.26.0

The gaps marked "fix" after the first research session through the MCP endpoint (2026-10-21, the Kernel + Avg multi VWAP indicator).

- **A range is counted in the asset's own tick, and says so.** Dukascopy's files hold thousandths: R60 is 0.06 points there and 6 points are R6000, where Capital.com's US100 (a tick of 0.1) makes R60 6 points. Nothing said it, and the MCP's answers rounded 29143.543 to 29143.5, which hid it.
  - An engine day of more than 40,000 bars is refused before the Pine runner is given it, with what the range is on that asset: "This engine day has more than 40,000 bars of R600 on dukascopy/us100: a tick of this asset is 0.001, so R600 is 0.6 points; one point is R1000 here" (`too_large`, `built_bars` in `src/ta_engine/replay.py`, which builds the bars a few rows at a time so as to stop early). That was the "Dukascopy runs die" of the session: R600 made about 100,000 bars a day, and the runner holds 590 MB at 49,107. Studies answer the sentence, and backtests fail on their first day with it, instead of a runner killed after minutes.
  - Each asset says its `tick` (`src/admin/data.py`: Capital.com from its decimals, Saxo from its last quotes, Dukascopy from its last day), in MCP `data_assets` and under the range of the Backtests form ("R600 is 0.6 points on this asset, whose tick is 0.001 (a point is R1000).").
  - The MCP keeps six significant digits but three decimals at least (`compacted`).
  - A run on Capital.com's day files (`--data files:`) read the tick from the prices with Saxo's floor of two decimals, so R60 was 0.6 points of US100 there and 6 on the server: it now takes the decimals the feed's prices carry (`AssetRows._capital_decimals`), as the feed's table says them.
- **Scripts that read the time in a time zone run about 2.4 times faster.** PineTS 0.11.1 made a new `Intl.DateTimeFormat` at each `hour(time, "Europe/Paris")`, `minute()`... and `timestamp("Europe/Paris", ...)`: 58 % of a run of the Kernel script. `admin_ui/vite/pinetsZoneFormat.ts` keeps one per time zone, in the three copies of PineTS the admin ships (the TA engine's runner, the pages' Pine worker, Vela-pinets' worker), each change one exact piece of PineTS found once or the build stops. The runner says it: `"pinets": "0.11.1+zone-format"`, which a run's `made_with` keeps. Nothing a script computes changes.
- **A signal rule may have its own days of history** (0 to 30; empty, the engine's `history_days`): fewer for a script that needs little warm-up, which then costs less at each bar. The Kernel script gives the same signals with none, on 4,623 bars instead of 8,381 on 20 October. The engine keeps an epic's ten-second bars from the earliest any of its rules needs and starts each rule at its own; a change of it loads the rule again. The Signal rules page has the field; a run made from a rule takes it (the form's "Start from a signal rule", MCP `backtest_start` with `source.from_rule`), and so do `python -m src.ta_engine.replay --rule` and Claude's `pine_signals` and `rule_shadow_save` (`history_days`, -1 for the engine's again).
- **The time a day may cost one of Claude's shadow rules is a setting**: `admin.mcp.shadow_day_ms` (2,000 by default, 500 to 10,000), where it was 2 s in the code. Each rule is first run on its own history.
- **One bar, one trade: an entry wins over a close of the same bar.** The trader takes one signal after the other, and an entry sells the other side before it buys: a close after it sold the position just opened. With every exit of the Kernel indicator mapped, 31 of its 33 closes of 19 and 20 October fell on the bar of an entry, and E2-1 of the session had 38 instant losses. The TA engine sends the entry alone and journals the close as not sent, with why; backtests and studies list it as no signal (`settled` in `src/ta_engine/signals.py`, used by `Engine._signals` and `ReplaySession.window`). The web server does the same for TradingView's alerts: a `close-position` does not replace an entry of the same `signal_timestamp` (`SignalStore.upsert`), while an entry still replaces a close. The scheduler's close of the day is kept apart (indice `n/a`). An entry the trader refuses (one already open in its direction) leaves the position as it was.

**On the live path**, only the web server's signal store changes; the trader's and the position monitor's code is untouched. Every backtest made before shows as stale: the code of `src/ta_engine` is in their digest. Their results change only where a bar raised an entry and a close, or on Capital.com's day files.

**Verification status:**
- 2,212 tests pass in Python's suite, 2 skipped, on Python 3.12 with Node 24 on the PATH (the real-runner and checker tests ran). Not run on Python 3.14 here: this Mac has 3.12 only; CI runs both. 834 pass in the interface's suite (827 before); lint, type checks, the build, `build:runner` and `build:checker` are clean, and the dev server's prepared PineTS carries the change too.
- PineTS patched beside PineTS as it is (`vite/pinetsZoneFormat.test.ts`): every hour, minute, weekday and timestamp the same over half-hour bars around the four clock changes of 2026, 4 formatters made instead of 576 on 96 bars. Through the built runner, confined as the engine runs it: the day of 49,107 bars captured from the session in 8.1 s instead of 19.8 s, its answer identical; a real-runner test reads Paris's hours across 25 October.
- On a real Dukascopy day (17 September) with the Kernel v2 script: R600 refused in 0.8 s with the sentence above (it ran 43 s and reached 854 MB before); R6000 runs on 2,016 and 2,030 bars, its signals the same as before, 0.43 to 0.50 s of script a day instead of 0.88 to 0.92.
- On the Capital.com days of 19 and 20 October (the MCP's CSVs, as day files) with v1 and every exit mapped: 33 entries as with the exits ignored, 2 closes sent, none on the bar of an entry.
- In the browser pane, on the fake admin (`npm run dev:api`): the range hint for Capital.com's US100 and Dukascopy's history; the Signal rules dialog's history field (31 refused, 0 reviewed as "None: from the restart", the rule kept with 0) and its "One bar, one trade" line.

**Not seen:** the server. After a deploy: `pine_signals` of the Kernel script on Capital.com R60 for a day (its time per engine day); `rule_shadow_save` with `history_days` 0, refused at 2 s with its new time or accepted under a raised `shadow_day_ms`; `data_bars` of a Dukascopy day, with its three decimals. Backtests made before, to be run again where they matter.

## v0.23.0 - Claude does strategy research on the server

**Release date:** 2026-10-20, with v0.22.0

- **An MCP endpoint for Claude Code, in the admin** (`src/admin/mcp_server/`): `/mcp` on the admin's address, so over WireGuard only, off unless `admin.mcp_enabled` is true. It opens with a bearer token made on the server (`watamcptoken create <name>`, printed once, 90 days; the file keeps its sha256 only). It refuses any request that carries an `Origin`, has its own throttle, answers at most four calls of a token at once, and is paused from the System page without a restart. The official SDK (`mcp` 2.3.0) is loaded only when the endpoint is on, and speaks both the 2025-11-25 protocol and the stateless 2026-07-28 one, in JSON.
- **37 tools for the research loop**, with a `/wata:research` prompt and server instructions that carry the lab skill's rules (what not to believe, the lock box, the few recorded days):
  - the data: recorded days, bars of 10 s to a day (or CSV, which Claude Code saves to a file on the Mac), the signals WATA received, its closed trades without account keys, measured execution;
  - Pine: list, read, **check** (PineTS's compile with file and line, and TradingView's v6 rules through a confined checker, `src/ta_engine/checker/check.mjs`), **run** on recorded bars (every plot summarised and sampled, alerts, PineTS's runtime warnings), **signals** as a rule would raise them, **compare** with TradingView's, save (checked first, Claude's own scripts only) and delete (its own, unused);
  - backtests: options, start (the Backtests form's body, with an idempotency key), derive (stress, a sweep's cell, neighbours), wait, list, result by part, trades, days, signals, compare, remove (its own), the turbo model;
  - the experiment journal and the lock box, kept on the server: trials counted per family and given to sweeps, the lock box refused to every study and run but a candidate's final check, widened only;
  - rules: every rule with its paper days, a rule's signals beside TradingView's, and **Claude's own shadow rules** (make, change, switch off, remove, set a run made from one as its reference), behind `admin.mcp.shadow_rules`, off by default. A rule is first run as the engine would on the last finished day of its epic, and refused if it fails, takes over 2 s an engine day or has over 20,000 bars a day.
- **What Claude can never do, enforced and tested**: trade, pause entries, publish to RabbitMQ or reach the web server; edit the configuration or read a credential; take a rule live or change one that is live or the operator's; run free SQL. The tools reach the admin through `ResearchServices` alone (named queries, no trading service); a test calls every tool with tripwires in place of everything that trades, another holds the endpoint's imports to an allowlist, and the only change of mode in its code is between shadow and off.
- **Claude's share of a small server**: its runs wait behind the operator's and the paper runs (at most 3 waiting, 60 a day, 10,000 day-simulations a request, 5 GB of disk kept free); Pine studies are child processes, one at a time, nice 10, 384 MB of Node heap, 90 s, 20 minutes an hour, refused when the container's memory is short; every child process of the admin is the first the kernel stops when memory runs out (`oom_score_adj` 1000). The runner's answers are shared by studies and backtests (`admin.backtests_signal_cache_mb`, oldest pruned). The admin container gets 1536M of memory instead of 1024M (`deploy/docker-compose.yml`), so that a study fits beside a backtest more often.
- **The TA engine runs live and shadow rules on two runners.** Live rules go first in each tick; shadow rules get `shadow_seconds_per_tick` (5 s) of it, the one given its bars longest ago first, and keep their bars when they wait. A shadow rule that fails, hangs or ends its runner reloads the shadow rules alone, never a live one; the shadow runner (256 MB, nice 10) is the first process stopped when memory runs short, and is stopped while no shadow rule is on. A Claude-made shadow rule's trouble is said on Telegram once a day at most.
- **Watching it**: System › Claude (the endpoint's state, its tokens, revoked from there, Claude's share against its limits, its last calls and refusals, the pause); Backtests › Experiments (the journal, and the lock box, which the operator moves or clears); a "Claude" badge on its runs, scripts, rules and experiments. Every call is logged in `wata-admin/mcp.jsonl`; only a revocation and the pause are audited (each audit entry goes to Telegram).
- **Shared code**: the backtest form's request (`backtest_requests.py`) and the bars of an asset (`asset_bars.py`) serve the routes and the tools alike; the lab's journal and studies moved to `src/research/` (the lab's commands are unchanged). The Pine runner can answer every bar's plots and runtime warnings (`detail`), its default answer unchanged. The study child reads recorded day files with `--data files:<folder>`, as backtests do.
- **The lab skill** says the server path, and no longer lists `close-long`/`close-short`, which are not actions.

**Verification status:**
- 2,184 tests pass in Python's suite, 16 skipped (live infrastructure and the real-database tests), on Python 3.14 and 3.12, with Node 24 on the PATH (the tests that run the real Pine runner and the rules checker ran); 827 in the interface's suite (815 before); lint, type checks, the build, `build:runner` and `build:checker` are clean.
- The TA engine: 72 tests (62 before; the 62 unchanged), with stand-in runners that end, time out or fail a load: a shadow runner's end never reloads a live rule, whose signal is still sent; live rules go first; shadow rules take turns and lose no bar; a rule moved to live is loaded on the live runner; the shadow runner stops with the last shadow rule.
- End to end on the fake admin with the endpoint on (`FAKE_MCP=1 npm run dev:api`, which prints a development token), through the SDK's client, in both protocol revisions (31 calls each):
  - `status`, the coverage, `pine_save` of the lab's EMA cross, `pine_check` (and a broken draft's line and column), `pine_run`, `pine_signals`, `pine_compare`;
  - an experiment, a backtest sent twice with one idempotency key (one run), its result and edge, its trades as CSV, its days, a stress run, a day of bars as CSV (107 kB);
  - a shadow rule, run first as the engine would (2,431 bars, 17 ms a day), its switch to live refused, a run made from it set as its reference;
  - the conclusion (2 trials counted), the removals.
- A headless Claude Code session (`claude -p`, the endpoint given in a scratch `--mcp-config` with a headers helper) made five calls through it: `status`, `pine_list`, `exp_new`, `pine_check`, `exp_conclude`. Each was logged in `mcp.jsonl`; nothing was audited or sent to Telegram.
- In the browser pane, on the same fake admin: System › Claude in dark at 1440 and in light at 390; the Claude badge on the runs and on Signal rules (on the scripts' list in the tests only); Experiments, where a lock box set from the page was then refused to Claude's `pine_run` and to a narrower `lockbox_set`, and cleared; no horizontal scroll at 390, no console error.

**Not seen:** the server's data and the path over WireGuard; Claude Code against the real server (the first check is the operator's, after a deploy: `/mcp` shows `wata` connected, and `status` the server's days). The TA engine's two runners on the server's feed.

## v0.22.0 - Bars of seconds on the Charts page and in backtests

**Release date:** 2026-10-20, with v0.23.0

- **Bars of 10, 20, 30 and 45 seconds, and of any length typed.** WATA records ten-second bars, and now makes bars of any whole number of seconds from 10 to a day of them, named as Pine names a timeframe (`10S`, `45S`, `90S`; `1`, `60` for minutes):
  - Technical Analysis › Charts: Vela's timeframe menu offers 10, 20, 30 and 45 seconds, 1, 5 and 15 minutes and an hour; the **Length** field above the chart takes any other (`90s`, `2m`, `4h`; a plain number is minutes) and says why it refuses one (under ten seconds, over a day). The address carries the length (`tf=45S`), so a chart of seconds can be shared and reloaded. Capital.com moves live at these lengths, Saxo every ten seconds;
  - the editor's test offers the same lengths;
  - a backtest takes the menu's lengths, or "Other length…" and one typed; its review, its run and its days say the length, and a day's chart is drawn at it.
- **Vela's chart learns seconds.** Vela 0.8.4 lists seconds in its menus, but its chart read `10S` as an hour: the history it loads, the gaps it heals and the bar a quote moves are counted from that. Its time axis, crosshair and data window also stopped at the minute. `admin_ui/vite/velaSeconds.ts` patches the pinned package when the interface is built (and in the dev server). Each change replaces one exact piece of Vela's code, and the build stops if one is no longer there, so a new version of Vela is looked at before it ships (`vite/velaSeconds.test.ts` runs Vela's own functions, patched).
- **A length that is no multiple of ten seconds.** A bar of 45 seconds holds the ten-second bars that begin in it, 40 or 50 seconds of prices by turns, and closes when the last of them does: in a backtest the alert of the bar of 14:00:00 counts at 14:00:50, never before its prices are known. The Charts page and the backtest form say so when such a length is chosen. Multiples of ten seconds are exact.
- **What serves them:**
  - the admin's API serves bars of any whole number of seconds from 10 to a day (it took 10, 30, 60, 300, 900 and 3600 only: `interval_served` in `src/admin/prices.py`), from the recorder's and the feed's tables and from Dukascopy's days alike;
  - backtests and the TA replay (`--bars 45S`) take any length (`checked_bars(..., any_length=True)` in `src/admin/ta_rules.py`; `bucket_end` in `src/ta_engine/bars.py`);
  - a script's `request.security` of seconds is read as WATA makes it, on WATA's grid, rather than built by PineTS from shorter bars (`engine/provider.ts`).
- **Signal rules keep whole minutes** (1, 5, 15 or 60, or range bars). The TA engine runs a rule's script over all its bars at each new one, which bars of seconds would make several times a minute on the server. Nothing changes for the engine in production.

**Verification status:**
- 812 tests pass in the interface's suite (776 before), 1,921 in Python's with 16 skipped (live infrastructure and the two real-database tests); lint, type checks and both builds are clean. The tests that pinned the old lengths (45 seconds refused) were changed to the new ones.
- Through the real Pine runner, a script on bars of 45 seconds: `timeframe.in_seconds()` 45, `timeframe.isseconds` true, `time_close` 45 or 50 seconds after `time`, and `request.security(..., "90S", close)` asked of WATA as 90-second bars.
- In a browser, on the admin application over made-up data (`npm run dev:api`):
  - Capital.com's US100 opened at `tf=45S`: 45-second bars, the note, the crosshair at 14:07:30, an axis of 14:42:30, 14:43, 14:43:45; live, a new bar began at 14:44:15, 45 seconds after the one before;
  - `90s` typed in the Length field: the chart and the address moved to 90 seconds; `5s` refused with its reason, the chart left as it was;
  - 10 seconds, with the EMA cross script on it: its lines, its crosses and an alert of the 10-second chart;
  - a Saxo turbo at 20 seconds;
  - a backtest on 45-second bars typed in the form, run to its end: its request kept `45S`, each of its 338 signals of the script came from a bar closed 45 or 50 seconds after it opened, on a whole ten seconds, and reached the trader 3 seconds later; the chart of a day drawn at 45 seconds, its crosshair at 13:06:45;
  - the Charts page in dark at a phone's width.

**Not seen:** Capital.com's real stream and the server's recorded prices at these lengths; a Dukascopy chart (no history in the checkout tried). A length chosen in Vela's own menu: the browser used passes no click to Vela's menus (seen before, in v0.21.2's checks), so the menu was seen listing the lengths and the Length field was used instead.

## v0.21.2 - The Charts page moves live

**Release date:** 2026-10-20

- **The Vela chart of Technical Analysis › Charts stood still.** It drew the bars read when it opened, then nothing more, whatever the asset: Capital.com's US100 was frozen there while its chart on the Assets View moved. Vela draws a chart as history alone unless it is told the chart is live (`live`, off by default), and only a live chart asks its data provider for what moves its last bar. The workspace was built without it, and the tests' stand-in for Vela never looked. It is now built live (`TaChart.tsx`, tested):
  - a Capital.com chart moves at each quote of the feed's stream, as the Assets View does, and its range bars at each ten seconds closed;
  - a Saxo chart, of an index or a turbo, moves every ten seconds with the recorder's bars;
  - Dukascopy stays history.
- **The scripts on a chart are run in full again when its bars change.** On a live chart Vela would run them in PineTS's live mode, which in PineTS 0.11.1 raises none of the alerts of the bars it adds and leaves `request.security` behind; the TA engine does not use it either. The chart draws what the editor's test and the engine compute from the same bars (`chart/pineEngine.ts`, tested):
  - while quotes come, a script runs again a second apart at most, the last quote included;
  - an alert of the bar in progress, raised again by each run, is shown once;
  - an error that every run repeats is shown once, and again after a run that works or new inputs.
- **A Saxo chart opened before the day's first bar, or left open overnight, now reads today's bars as they are recorded.** It asked for bars up to the asset's last day as it was when the chart opened, and stayed on the day before. An asset the recorder still records is now read up to today, as Capital.com's already was (`data/wataBars.ts`, tested).
- **The range bars a live chart missed are all read back.** When a chart comes back (its tab shown again, its stream connected again), Vela asks for the bars since its last one, as many as its timeframe would hold. Range bars can come faster: only the newest that many were served, which left a hole. Since a time, a range ticker's bars are now all served (`chart/wataProvider.ts`, tested).

**Verification status:**
- 776 tests pass in the interface's suite; lint, type checks and build are clean. The new tests fail without the fix.
- In a browser, on the admin application over made-up data (`npm run dev:api`):
  - Capital.com's US100 at one minute, before the fix: no stream opened, the last price unchanged for half a minute;
  - after it: the last bar moves at each quote, and a new one begins on the minute;
  - with the EMA cross script on it: one whole run a second (501 bars each), its lines moving with the close;
  - the chart moved to five minutes in place: the stream of the one-minute chart closed, another opened;
  - its range bars (R60): the last one moves at each ten seconds closed, one read of range bars each time;
  - a Saxo turbo and the Saxo index, their charts opened at 07:31 and 07:46 before the made-up recorder's 08:00 open: each read today's bars every ten seconds, drew the 08:00 bar once recorded, then the 08:01 one, as the API served them.

**Not seen:** Capital.com's real stream, the server's recorded Saxo prices, a Dukascopy chart (no history in the checkout tried).

## v0.21.1 - The Capital.com feed's log no longer floods

**Release date:** 2026-10-20

- **The feed's live prices are no longer sent back by RabbitMQ.** While no chart follows them, no queue is bound to `wata.prices`, and aio-pika asks by default for such a message to come back. Nothing read them: the feed logged `aiormq.channel - Unhandled message ... Basic.Return` as an error several times a second, already so before v0.21.0, and its log rotated every 20 minutes, keeping about ten hours. A price no chart listens to is now dropped by RabbitMQ, as intended (`mandatory=False` in `RabbitExchange.publish`, `src/capital_feed/live.py`, tested).

## v0.21.0 - A research lab, and backtests that say how far to trust them

**Release date:** 2026-10-20

Finding a strategy means trying many, and the best of many tries looks good by chance. This release gives the backtests what tells a found edge from luck, a place on a Mac where Claude can try strategies by the hundred over a copy of the server's data, and long histories on turbos made up from the index.

- **A research lab** (`lab/`, on a Mac, never on the server):
  - **The server's days**: the admin can write each finished day as files while the market is closed (`src/admin/research_export.py`: the recorder's and the Capital.com feed's bars, the signals, positions, orders, a snapshot of the settings; never `config.json` itself, an order's bodies or a token). "Write them" on the Backtests page switches it on; the last 30 days are kept there.
  - **Each night**, `lab/sync.sh` pulls them over SSH (yours to run, or `lab/launchd/install.sh` to have the Mac run it) into a PostgreSQL of its own.
  - **The real admin app** over that copy, on 127.0.0.1:8083 (`python -m lab serve`), running four backtests at once, two years at most.
  - **A command line** for Claude, `python -m lab`: runs and sweeps from a spec, an experiment journal that counts every trial, a lock box of last days kept for a candidate's final check, reports as pages, SQL over the files, and every reading below. `.claude/skills/wata-lab/SKILL.md` is the method it follows.
  - **A script's signals are kept** per day (`_signal_cache`): running the same script again on the same days costs nothing.
- **How far to trust a run**:
  - **Risk figures** on its days: Sharpe, Sortino and Calmar ratios (from 20 days), the longest time under a former high, the worst 5 % of days, a win for a loss, and the share of its best a winning trade kept.
  - **The same days otherwise**: the worst drawdown when they are drawn again in another order, and the total without the best one, two, three days.
  - **Later and dearer fills**: "Stress this run" makes it again with each order up to 15 s later and up to 0.05 € a turbo dearer.
  - **The edge of the entries**: how the index moved 1 to 60 minutes after each entry signal, taken or not, against moments drawn at random in the same hours, and where it lives (hour, direction, signals taken or not, kind of day).
  - **Kinds of day**: each day's gap, range, volatility, trend, US open and macro news (FOMC, CPI, jobs report, PCE, from the Fed's, BLS's and BEA's own calendars); the trades broken down by them and by their place against the US open.
- **Sweeps say whether choosing worked**: a walk-forward (the best combination chosen again window after window on the days before, kept on the days after, beside the best one known only afterwards), the probability that choosing the best was fitting, and the deflated Sharpe ratio with every combination tried counted.
- **Long histories on made-up turbos**: the recorded turbos are measured against Dukascopy's Nasdaq (euros a point, the level beside the knock-out one, the spacing of the levels, the spread by hour), and a run can trade turbos made up from the index on any day it has, up to two years. "Measure them" on the Backtests page; every page says when a run's turbos were made up. `python -m lab model validate` checks the model on the recorded days.
- **Measured, not assumed**: what WATA's own orders took and cost (from a signal to the trader, from an order to Saxo, the price paid against the quote recorded, the refusals), with "Use the measured values" in the form.
- **Against real trades**: a replay of the signals WATA received is set beside WATA's real positions of the same signals, trade by trade.
- **Shadow rules on paper**: each finished day, a rule in shadow is traded on the signals it raised, and the Signal rules page sets those days beside the daily range of the run chosen as the rule's reference ("Set as the rule's reference" on a run made from it).

## v0.20.0 - Technical Analysis: Pine scripts, Vela charts, WATA's own signals, and backtests on the recorded turbos

**Release date:** 2026-10-20, deployed together with v0.21.0

WATA's signals came from TradingView alone: an indicator written in Pine Script on TradingView's charts, its alerts posted to the webhook. The admin gains a **Technical Analysis** section that does the same on WATA's own data, and lets WATA make its own signals from it, in shadow until a rule is switched to live.

- **Indicators** (`/ta/indicators`): Pine Script v5 and v6, written in the browser with the language highlighted and checked as it is typed (TradingView's own rules, then a compile by PineTS that marks the line of each error, then WATA's own checks: an unknown library, a ticker WATA does not have). Each save is a new version, kept as a plain `.pine` file under `var/lib/admin/ta/`, with its history, a diff, and a version put back. Libraries are kept like scripts, and `import user/Name/1 as alias` imports the WATA library of that name, inlined before the script runs. A `.pine` file can be imported, and a script downloaded.
- **A test beside the editor**: the script run on a day of one of WATA's assets (Capital.com, Dukascopy, Saxo), at 1 to 60 minutes or as range bars, with its plots and every alert of the day. Its bars, and the script as it ran (libraries inlined), can be downloaded for pinets-cli or an AI agent.
- **Charts** (`/ta/charts`): the Vela SuperChart on WATA's assets, Capital.com live, with WATA's scripts in Vela's own indicator list and their inputs in its settings. Range bars are a ticker of their own (`CAPITAL_US100.R60`). The layout is kept by the admin, shared between devices. An asset's page and the editor open their asset or script there.
- **Signal rules** (`/ta/signals`): a script run on each bar of a Capital.com asset as the bar closes. A rule pins the script's version and each library's, sets the inputs (read from the script by PineTS) and maps each alert to nothing, `long`, `short`, `close-position`, or what its message says (WATA's webhook JSON). A new rule is in shadow: its signals are journaled, never sent. Live is an operator action: allowed only when actions are, confirmed with a slide that says TradingView's alerts for the indice must be off, one live rule per indice, audited and announced on Telegram, as is any change to a live rule. The page shows the engine's state and each rule's, the journal of signals with what the trader made of each one sent, and a day of a rule's signals beside TradingView's, paired, listed and marked on the day's prices.
- **The TA engine** (`src/ta_engine/`, compose profile `ta`, `trade.config.ta_engine`, off by default): reads the Capital.com feed's bars from PostgreSQL, builds the rules' bars as the charts do, runs each new bar through PineTS, journals each signal in `ta_signal` before anything is sent, and sends a live rule's signals to the web server's `/internal/signal`. It never sends:
  - for a shadow rule;
  - for the history loaded at a start;
  - for a bar evaluated again;
  - for a bar that closed more than a minute ago;
  - while the feed is stale;
  - an action other than `long`, `short` or `close-position`.

  The scripts run in a Node process of their own, confined: no file but its own, no environment, no import once started, no network functions, its memory capped, and a time limit per bar. "TA engine" is on the System page once switched on, its log on the Logs page.
- **A replay command**, `python -m src.ta_engine.replay`: what a rule or a script would have raised on recorded days (Capital.com's from the database, Dukascopy's from the day files), through the same runner and the engine's daily restart, as JSON. Nothing is sent or written. `python -m src.ta_engine.runner` checks the runner where it is installed.
- **Licences**: PineTS and Vela-pinets are under the AGPL-3.0; the admin and the engine built with them are offered with their whole source, the public repository ("Licences and source" on the System page, and `third-party-notices.txt` served with the admin). WATA's own files stay under the MIT licence.

**Backtests, rebuilt from scratch.** The former backtester traded one strategy, written in Python, on a model of the turbo. The new one trades either a Pine script of the Technical Analysis section or the signals WATA received. It trades them on the turbos the price recorder kept, as WATA would have.

- **What is replayed**:
  - the trader's checks on an entry, in its order;
  - its choice of turbo, made by its own helpers among the turbos recorded and quoted at that moment;
  - its sizing;
  - the position monitor's exits, on the turbo's recorded bid every ten seconds;
  - the close of the day.

  An entry with no turbo recorded at that moment is shown, not traded. `tests/test_backtest_parity.py` holds the copies of those rules to the live code.
- **New backtest**, in five steps:
  1. the asset and its bars: Capital.com, Dukascopy or the index Saxo quotes, as time bars or range bars, or as a signal rule has them;
  2. the strategy: a script with its inputs and what its alerts become, or the journal;
  3. the period, with what was recorded each day;
  4. WATA's settings for that run only, each beside its live value, and what a backtest assumes: the account, the delays, how a stop fills, slippage and commissions;
  5. a review.
- **A run** shows:
  - its figures, and how far to trust them: random entries traded on the same turbos, the range of its total, room for a stop and a target;
  - its curve, in percent or in money;
  - why signals were refused and why trades were sold;
  - breakdowns, as the Exits page makes them;
  - every day, trade and signal, as tables with CSV export.
- **A day** shows the asset as the strategy read it, with every signal and trade marked.
- **A trade, clicked**, opens:
  - the chart of the turbo it bought: its bid every ten seconds, with the price paid, the stop, the day's target and the trailing stop as it moved;
  - the turbos the trader looked at;
  - its size, cost and result;
  - the signals that bought it and sold it.
- **Sweeps and Compare are rebuilt on it.** A sweep tries one or two settings or script inputs, holds the last days out, and can make a combination a run of its own. Compare sets up to four runs side by side: their figures, what they did not share, and their curves.
- **On the server**, a run is a child process of the admin:
  - one at a time, at the lowest priority;
  - it reads the recorded turbos through a read-only connection, a day at a time;
  - it writes files under `var/lib/backtests/runs/`;
  - 92 days, 10 runs waiting and 3 h per run at most.
- **Dukascopy's history moved to the Data section.** Its downloads, "Keep the prices up to date" and its coverage are on Dukascopy's provider page (`/api/data/providers/dukascopy/...`). The code is in `src/price_history/` and `src/admin/downloads.py`.
- **Removed**:
  - `src/strategy/`: the Python strategy, its turbo model and its calibration, the comparison with real trades;
  - its commands and its pages.

  The former runs can be deleted from the Backtests page ("Remove them").

**Verification status:**
- **Tests:**
  - 1,778 pass in the Python suite, in an environment built from `requirements.txt`; the 14 skipped are the live database and queue tests, which need their servers. One test runs the built runner in its confinement on Node 24.
  - Of those, 57 run the trader's and the position monitor's own code beside the backtest's copies of their rules (`tests/test_backtest_parity.py`), and one runs a backtest through a real child process.
  - 730 in the interface's suite; lint, type checks and build are clean.
- **Real PostgreSQL 16:**
  - the engine's tables, its writes and the admin's reads, joined to the trader's journal, before and after the engine first ran;
  - the backtest's reads of the recorded turbos (`DatabaseData`, on a read-only connection) and the coverage queries of the form.
- **In a browser** (the admin application over made-up data; Chromium dark at 1,440 pixels, WebKit light at a phone's width):
  - Indicators, editor, test, history, Charts, range bars, Vela's indicator list;
  - a rule made, switched off and back, the live confirmation cancelled, and the comparison.

  No policy violation in the console: PineTS compiles in the worker files only, in WebKit too.
- **Backtests, in a browser** (the same server, its made-up recorded turbos written as the files the command reads, the real Pine runner):
  - three runs asked for through the form: the EMA cross with the stop changed, a replay of the journal, and a sweep of the stop and the day's target;
  - their pages, a day, a trade opened on its turbo, the comparison, in Chromium dark and light at 1,440 pixels and WebKit light at a phone's width, with a clean console.

  These checks found three faults, now fixed and tested:
  - a replay of the journal failed: its timestamps, as the database gives them, could not be written;
  - the folder of that failed run broke the list of runs;
  - a page opened from a scrolled one kept its scroll, so a trade opened from a run showed the bottom of its day.
- **The user's Kernel multi-VWAP script** (with its two libraries, kept out of the repository) compiled and ran through the replay on the three Dukascopy days kept locally:
  - **2026-09-17:** neither the replay nor the Python port of the strategy (`src/strategy/kernel_vwap.py`, removed since with the former backtester) raised anything.
  - **2026-09-15 and 2026-09-22:** the afternoon entries and the exits at the end of the session agree within one to two minutes; the morning entries differ. The port builds its range bars from mid prices rounded to 0.1, weights its VWAP by each ten seconds and simplifies the script; the replay runs the script itself on bid bars.
- **pinets-cli 0.1.15** ran a downloaded script, its library inlined, on its downloaded bars.

**Not run against the server's data, Capital.com's real stream, a real signal reaching the trader, or Docker:** the image's Node and the runner inside it are checked by CI's image job. No backtest has run on the server's recorded turbos yet.

### Upgrade Notes

- Nothing changes until the `ta` profile is added and `trade.config.ta_engine.enabled` set: see the README, "Technical Analysis". The engine needs the Capital.com feed for its bars.
- `package.sh` now also builds the engine's runner (`npm run build:runner`), and the image carries Node 24's `node` binary.
- The admin keeps the scripts and rules under `var/lib/admin/ta/`, inside its existing writable folder; the engine mounts that folder read-only.
- **The admin container gets 1024M of memory instead of 512M** (`deploy/docker-compose.yml`): a backtest's child and its Pine runner run inside it.
- **The former backtester's runs stay on disk** until "Remove them" on the Backtests page. Dukascopy's downloaded prices are kept. The state of "Keep the prices up to date" carries over: it is copied once to `var/lib/price_history/dukascopy/_fetch/`.
- **`python -m src.strategy.*` no longer exists.** Dukascopy's history is fetched with `python -m src.price_history.dukascopy`. Backtests are asked for from the admin; `python -m src.backtest.run` is the command it runs.

### Known Limits

- **PineTS is not TradingView.** It runs most of Pine v5 and v6, not all of it, and not always identically:
  - a symbol has no exchange time zone (its days are UTC days);
  - `request.security` reads WATA's assets only, live only Capital.com's;
  - PineTS 0.11.1 passes the named arguments of `alertcondition()` in the wrong place, which WATA puts right.
- **Each new bar runs the script over all of a rule's bars**: PineTS's own live update raised none of the user's script's alerts. The user's script took 0.5 s per thousand one-minute bars here, and 1.2 s while reading two other series; `history_days` bounds it.
- A rule runs on Capital.com's assets only: Saxo and Dukascopy have no live bars in WATA.
- The comparison with TradingView takes every signal the trader received for the indice from elsewhere than the engine: a manual signal from the admin counts as TradingView's.
- Vela 0.8.4 warns in the console about an indicator added on range bars ("rendering unanchored"); it draws it.
- **Backtests only cover the days the price recorder ran** (on the server since about 2026-10-13). For a while, runs will say they rest on too few trades and days.
- **A backtest sees ten seconds at a time**, against a monitor that looks about every second. The order of prices inside ten seconds is assumed, and how a stop is filled there is a setting.
- **The turbos the trader looked at are worked out again** from the recorded ones: a new issue is seen up to five minutes late.
- **Not modelled in backtests**:
  - a knocked-out turbo's residual value;
  - Saxo's commissions (settings, 0 by default);
  - the web server keeping only the newest signal of each indice;
  - the protective stop.

---

## v0.19.0 - Orders: every request to Saxo, and why Saxo refused one

**Release date:** deployed in the package numbered 0.18.0

On 2026-10-14 Saxo refused a market sell during a volatile market: "À ce stade, seuls des ordres limites sont autorisés" (at this stage only limit orders are allowed). The admin showed nothing of it. An order Saxo refuses has no id, and only the orders Saxo accepted were kept. The only traces were a log line and a Telegram message, and there Saxo's message was read in the wrong place: "Bad Request", then Saxo's whole answer, twice.

- **Every request to Saxo's order endpoint is kept with Saxo's answer** (table `order_request`): orders placed (buys, sells, the opt-in protective stop) and cancellations, accepted or not. Each one keeps:
  - what was sent, without the account's keys;
  - why it was sent: the exit rule, the explicit close, or the signal behind a buy;
  - what came back: the HTTP status, Saxo's code and message, and its answer as received.

  A request that got no answer (a timeout) is kept as such: Saxo may have taken the order all the same. The trader and the position monitor write these rows in the background, as they do the orders' moments: nothing on the way to an order waits for them, and a failure to write changes nothing of a trade (tested). How WATA trades is unchanged.
- **Orders page: what Saxo did not take is a row among the orders.** All the attempts made with one intent (selling one position, buying for one signal) form one row, placed at the first attempt: "Refused by Saxo ×37", how long it lasted, and the position.
  - Opened, the row shows:
    - what happened, in a sentence;
    - Saxo's answers, each with how many times it was given;
    - the order that went through afterwards, or "The position is still open";
    - every attempt, with what was sent and what came back.
  - The Result filter shows these rows alone, and the search finds them by the position's id or by a word of Saxo's message. A fourth figure above the table counts them, and the CSV export carries Saxo's code, message and status.
  - The sale that finally went through shows the refusals as the first step of its timeline.
- **Positions page.** An open position whose sell Saxo does not take says so under its name ("Sell refused ×37 · 14:04:30"), with Saxo's message on hover. Its details list every request about it, preceded by a sentence while it is still open.
- **A live notice** when Saxo refuses to sell an open position: at most once a minute per position, with the count since the last notice, and a browser notification when the tab is hidden. A refused buy was already announced, as its signal failing.
- **Saxo's answer said once, where Saxo put it**, in the logs, on Telegram and in the signal journal. The line of 2026-10-14 now reads: `ERROR closing 7779123400: API error placing order (Status: 400) (Saxo: À ce stade, seuls des ordres limites sont autorisés [OtherError]) (Order: Sell 6.0 units of 61252187)`. The Telegram message of a refused buy gives Saxo's code and message (it said "N/A"), and no longer prints the account key inside the order it shows.

- **The server's ports for HTTP and HTTPS can be set in `.env`** (`HTTP_PORT`, `HTTPS_PORT`, 80 and 443 when not set: nothing changes for a server that does not set them). HTTP is redirected to `HTTPS_PORT`, and the install script opens both in the firewall. TradingView and Let's Encrypt still only use 80 and 443, so another port needs something in front that forwards those (see the README, "Other ports than 80 and 443"). The admin interface's second port was already `ADMIN_PORT`; the development compose file reads `DEV_WEB_SERVER_PORT` and `DEV_RABBITMQ_PORT`.

**Verification status:** covered by tests: 1,552 in the Python suite, in an environment built from `requirements.txt`, and 642 in the interface's. One of them sends the refusal of 2026-10-14 through the real client: the record says 400, OtherError and Saxo's message, the error's text gives that message once, and the position can still be sold. The new table and the admin's reads were run on a real PostgreSQL 16, through the live tests and a new one in which refused requests written by the real manager are read back as a run, as an order's requests and as an open position's refused sells. The pages were driven in a browser against the admin application over made-up data, in both themes, at phone width and at 1,280 pixels:
- the run of 37 refusals, opened, filtered, and found by a word of Saxo's message;
- the marker and the details of an open position;
- the live notice.

**Not run against Saxo, the server's data or Docker**: the first refusal recorded on the server will be the first real one. Run on Python 3.12 only: CI runs 3.12 and 3.14.

### Upgrade Notes

- Nothing to configure. The trader and the position monitor create `order_request` at their next start; until then the admin reads it as empty.
- The Orders page reads the period's requests at each refresh: at most the 20,000 most recent.

### Known Limits

- **Not changed, on purpose:**
  - A sell decided by an exit rule that Saxo refuses is sent again at each price update, about once a second, for as long as the rule holds, with a Telegram message each time.
  - On a stop-loss, the start of the cooldown moves forward with each attempt.
  - A protective stop cancelled before a refused sell is not placed again.
  - WATA sends market orders only: while Saxo accepts nothing but limit orders, every attempt is refused.
- Which exception a refusal raises is unchanged. The client still reads Saxo's code at the top level to decide it, so an `InsufficientFunds` that Saxo puts under `ErrorInfo` is not recognised as one (it was not before either), and the client's order-endpoint branch still never runs.
- A request is written once its answer is known: a service stopped in between leaves no row for it.
- What is shown is bounded:
  - a run of refusals ends after five minutes without an attempt;
  - a run shows its 50 most recent attempts, and a position's details its 500 most recent requests.
- The Open tab's marker counts every refused sell of the position, including any from before an accepted sell that did not close it.

---

## v0.18.0 - Data section: Providers and Assets View

**Release date:** deployed as 0.18.0, with v0.19.0's orders

The Prices page showed what the Saxo price recorder kept, one instrument at a time, and nothing else: the Dukascopy history the backtests read was fetched and counted on the Backtests page but never shown, and the recorder's settings could only be changed in the JSON of the Configuration page. The page gives way to a **Data** section in the sidebar, between Analysis and Operations, with two pages.

- **Assets View** (`/data/assets`) lists every asset WATA keeps prices of: the index and the turbos recorded from Saxo, and each Dukascopy history. It can be filtered by type, provider and status, searched, and narrowed to the assets seen in the last 30 days, 7 days or today. A click on a row opens it to its last day with data: what the list of instruments said of it, and the chart of that day. The turbos Saxo left without a quote today are marked, as on the Prices page. Addresses of the Prices page still work: they lead here, or to the asset they showed.
- **Each asset has a page** (`/data/assets/<provider>/<key>`), reached from its name. It opens on the asset's last day with data, not on today: a turbo knocked out last week has nothing today. Five tabs: the chart of the Prices page, with its intervals, range bars and export; statistics, day by day (open, high, low, close, change, range, spread, quote changes, share of the trading time covered, Saxo's volume when recorded) and the spread through the day by half hour; the quality of the data (what Saxo left without a quote, the recorder's gaps, the coverage of each day; for Dukascopy, the days fetched); WATA's trades on the turbo, or on the turbos of the index; and every detail kept, with how it is recorded or where its files are.
- **Providers** (`/data/providers`) lists Saxo's price recorder and Dukascopy: the status of each (for the recorder: switched on in the configuration, running, writing), its assets, the span of its data, and a page of its own. "Add provider" lists what WATA has code for, Capital.com being shown as to come: a provider is code, not a setting.
- **The price recorder's settings can be changed from its provider**: switched on or off, the volume, the number of instruments, the refresh, the rate, the retention, the alert, and the instruments recorded besides the turbos (an index, by its UIC). The change is shown before it is saved, with what it costs (months of bars dropped by a shorter retention, prices that can never be fetched again once it is switched off), and saved as the Configuration page saves: validated, the previous file kept, audited. The recorder applies it at its next start, which the admin cannot do: the page says so.
- **Every page says where it is, in the top bar**: Home, the section, the page, and on a deeper page what it shows (an asset, a provider, a backtest, a day of it), each step a link back. On a phone, the step before the page alone. It takes the place of the backtest pages' links back to the list and to their run.
- **Other Dukascopy indices can be fetched**: the US 500 (`USA500IDXUSD`) and the Germany 40 (`DEUIDXEUR`), each under a label and in a folder of its own, from the Dukascopy page, still one download at a time. Keeping the history up to date stays for the Nasdaq history, the one backtests read. The downloader is unchanged, so no backtest is marked stale.

**Verification status:** covered by tests (1,495 in the Python suite, in an environment built from `requirements.txt`; 610 in the interface's). The query that lists the assets was run on a real PostgreSQL 16: about 45 ms for 3,041 instruments over four monthly partitions and 894,000 bars, the partitions pruned at run time; an instrument whose months the retention had dropped is not listed. A live test of the list was added to `tests/test_infrastructure_live.py`; the live database tests pass against a PostgreSQL 16. The pages were driven in a browser against the admin application over made-up prices, in both themes, at phone width and at 1,280 pixels: the recorder's settings saved to the development server's own configuration, a Dukascopy instrument added and removed. Dukascopy's symbols were fetched to be checked: the US 500 on two weekdays, at 7,730 to 7,800, and the Germany 40 on one, at about 25,380, each written as a day of bars. The US 30 (`USA30IDXUSD`) decoded at its level, about 51,000, but Dukascopy answered some of its hours with "503 Service Unavailable" at every try, so that none of its days could be written: it is not offered. **Not run against Saxo or the server's data, nor in Docker.**

### Upgrade Notes

- Nothing to configure. The admin writes one more file, `dukascopy/instruments.json` in the price history folder, where it could already write: no new mount.
- A setting of the recorder saved from its page applies at the recorder's next start (`docker restart price_recorder1`); switching it on for the first time also needs `recorder` in `COMPOSE_PROFILES`, then `watastop` and `watastart`.
- The admin's `/api/prices/*` routes are now under `/api/data/`.

### Known Limits

- The share of trading time covered counts WATA's trading hours, those of the Nasdaq session, for every asset, the Germany 40 included.
- A trade on a turbo the recorder never saw is not attributed to its index on the Trades tab.
- Dukascopy's files are days of UTC: a Dukascopy asset's days are those of its files, and it opens on the last one, whose last two hours fall on the next day in Paris.
- The admin never asks Saxo whether it quotes an instrument added by its UIC, sharing Saxo's limits with the trader: `wataprices probe --uic <n>`, on the server, says so before it is added.
- A Dukascopy history is read a month at a time at most; range bars, statistics and the recorder's gaps 31 days at most, what Saxo left without a quote 7.

---

## v0.17.0 - Prices page: missing quotes, details of an instrument, signal marks, Saxo's volume

**Release date:** 2026-10-13

Some turbos have stretches without a bar although the recorder was running and other turbos were quoted: Saxo sent no quote for them. Nothing on the Prices page said so, and "Recording gaps" answered "Recorded without interruption" under a chart with holes.

- **Turbos with missing quotes are marked in the list of instruments.** Two things are looked for, in trading hours and outside the recorder's own gaps: a hole of 30 seconds or more between two bars of a day, and a turbo with no more bar 15 minutes or more before the other turbos of its indice stopped. A turbo whose bid had fallen under a quarter of its highest of the day is taken for knocked out and is not marked. The count is above the list, and "Recording gaps" lists the times of the instrument shown beside the recorder's own. Worked out for periods of up to 7 days.
- **An instrument opens in the list** (the arrow beside its name) to its last bid and ask, the volume Saxo counted, its quote changes, its first and last bar, the share of the time it had a quote, its holes, its stop or probable knock-out.
- **The signals can be left off the chart**: "Signal marks", beside the legend, kept in the browser. The trades stay.
- **Saxo's volume can be recorded** (`trade.config.price_recorder.record_volume`, off by default): the total of the day of each instrument, in a table of its own (`price_volume`), one row each time it changes. `wataprices probe --uic <a turbo>` says whether Saxo has one for an instrument: to be asked before switching it on. The bars, their table and the code that builds them are unchanged, so no backtest is marked stale.

**Verification status:** the rule, the queries' text, the routes, the recorder and the pages are covered by tests (Python suite and interface suite pass, in an environment built from `requirements.txt`). The new SQL was run on a real PostgreSQL 16 with bars placed by hand (a hole, a stop, a knock-out, a volume over two days, a retention) and on a made-up week of two million bars: 0.25 s for a day, 1.2 s for the week, on a development machine. The page was driven in a browser against the admin application over made-up prices, in both themes and at phone width. **Not run against Saxo or the server's data**: whether Saxo sends a volume for the turbos, and whether it takes a subscription that asks for one, is unknown until `wataprices probe` and then `record_volume` are tried there; the thresholds of the rule (30 seconds, 15 minutes, a quarter) have only seen made-up prices.

---

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
