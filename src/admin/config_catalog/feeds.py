"""The services that record prices and make WATA's own signals: the price recorder, the Capital.com feed, the TA engine."""

from src.admin.config_catalog.model import NONE, SERVICE, TRADING, Column, Param

RECORDER = "trade.config.price_recorder"
CAPITAL = "trade.config.capital_feed"
TA = "trade.config.ta_engine"
ADMIN = "admin"

RECORDER_PROFILE = "recorder in COMPOSE_PROFILES of /app/wata/.env (watastart then creates the container)."
CAPITAL_PROFILE = "capital in COMPOSE_PROFILES of /app/wata/.env (watastart then creates the container)."
TA_PROFILE = "ta in COMPOSE_PROFILES of /app/wata/.env (watastart then creates the container)."
IDLE_ON_A_MISTAKE = "A value the service refuses leaves it idle with a message, never the trader stopped: it checks its own section."

RECORDER_PARAMS = (
    Param(
        path=f"{RECORDER}.enabled", group="recorder", label="Record Saxo's prices", kind="bool", optional=True,
        default=False,
        help="Ten-second bars of the turbos of each indice traded and of their index, kept for backtests.",
        readers=("price_recorder", ADMIN), risk=SERVICE,
        effects=("The recorder subscribes to Saxo's prices with the services' login: it adds to what Saxo serves the "
                 "account.",),
        also_needs=(RECORDER_PROFILE,),
        caveats=(IDLE_ON_A_MISTAKE, "Bars missed while it is off cannot be fetched from Saxo afterwards."),
    ),
    Param(
        path=f"{RECORDER}.refresh_interval_seconds", group="recorder", label="Turbos looked for every", kind="int",
        unit="s", minimum=30, optional=True, default=300,
        help="How often the recorder searches Saxo for the turbos to record.",
        readers=("price_recorder",), risk=SERVICE,
    ),
    Param(
        path=f"{RECORDER}.max_instruments", group="recorder", label="Instruments recorded at most", kind="int",
        minimum=1, optional=True, default=150,
        help="The turbos recorded at once, nearest to the index first.",
        readers=("price_recorder",), risk=SERVICE,
    ),
    Param(
        path=f"{RECORDER}.refresh_rate_ms", group="recorder", label="Prices refreshed every", kind="int", unit="ms",
        minimum=100, optional=True, default=1000,
        help="The refresh rate asked of Saxo's stream for the recorded prices.",
        readers=("price_recorder",), risk=SERVICE,
    ),
    Param(
        path=f"{RECORDER}.instruments", group="recorder", label="Instruments recorded besides the turbos", kind="rows",
        optional=True, default=[],
        help="Other instruments of Saxo to record, such as the one quoted for an index.",
        readers=("price_recorder", ADMIN), risk=SERVICE,
        columns=(
            Column("label", "Name", "text", help="Its name in WATA; an indice's name records it as that index."),
            Column("uic", "Uic", "int", minimum=0, help="Saxo's number of the instrument."),
            Column("asset_type", "Asset type", "text", help="Saxo's asset type, e.g. CfdOnIndex."),
        ),
    ),
    Param(
        path=f"{RECORDER}.retention_months", group="recorder", label="Months kept", kind="int", unit="months",
        minimum=0, optional=True, default=0,
        help="Older months of bars are removed. 0: everything is kept.",
        readers=("price_recorder",), consumers=("backtests",), risk=SERVICE,
        caveats=("Backtests can only run on the days kept.",),
    ),
    Param(
        path=f"{RECORDER}.alert_after_seconds", group="recorder", label="Say silence after", kind="int", unit="s",
        minimum=30, optional=True, default=300,
        help="In trading hours, no price for this long is said on Telegram.",
        readers=("price_recorder",), risk=SERVICE,
    ),
    Param(
        path=f"{RECORDER}.record_volume", group="recorder", label="Record Saxo's volume", kind="bool", optional=True,
        default=False,
        help="Also asks Saxo for the day's traded volume of each instrument, kept beside the bars.",
        readers=("price_recorder",), risk=SERVICE,
        caveats=("If Saxo does not take the subscriptions with the volume within two minutes, the recorder asks for the "
                 "quotes alone again: the volume never costs the prices.",),
    ),
)

CAPITAL_PARAMS = (
    Param(
        path=f"{CAPITAL}.enabled", group="capital", label="Record Capital.com's prices", kind="bool", optional=True,
        default=False,
        help="The Nasdaq 100 streamed from Capital.com, recorded every ten seconds, sent to the admin's live charts and "
             "kept as a file a day.",
        readers=("capital_feed", ADMIN), consumers=("backtests",), risk=SERVICE,
        effects=("The TA engine runs the signal rules on the bars it records: without it, no signal of WATA's own.",),
        also_needs=(CAPITAL_PROFILE, "The Capital.com login (CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_PASSWORD in "
                                     "/app/wata/.env, until it moves to PostgreSQL)."),
        caveats=(IDLE_ON_A_MISTAKE,),
    ),
    Param(
        path=f"{CAPITAL}.environment", group="capital", label="Capital.com account", kind="enum",
        choices=("demo", "live"), optional=True, default="demo",
        help="Which of Capital.com's accounts the login opens: its prices are the same.",
        readers=("capital_feed",), risk=SERVICE,
        caveats=("The login must be one of that account: a demo key does not open the live account.",),
    ),
    Param(
        path=f"{CAPITAL}.instruments", group="capital", label="Instruments", kind="rows", optional=True,
        default=[{"epic": "US100", "label": "us100"}],
        help="What is recorded: Capital.com's name of each instrument and WATA's.",
        readers=("capital_feed", ADMIN, "ta_engine"), consumers=("backtests",), risk=SERVICE,
        columns=(
            Column("epic", "Epic", "text", help="Capital.com's name, e.g. US100."),
            Column("label", "Name in WATA", "text", help="Lower-case letters, digits, '-' or '_': an indice's name "
                                                         "records it as that index, and names the folder of its files."),
        ),
        caveats=("At most 40, Capital.com's limit for one stream.",),
    ),
    Param(
        path=f"{CAPITAL}.minute_volume", group="capital", label="Capital.com's minute candles", kind="bool",
        optional=True, default=True,
        help="Also keeps Capital.com's own candle of each minute and the volume it gives it.",
        readers=("capital_feed",), risk=NONE,
    ),
    Param(
        path=f"{CAPITAL}.day_files", group="capital", label="A file a day", kind="bool", optional=True, default=True,
        help="Each finished day of bars written as a file, which backtests run on files read.",
        readers=("capital_feed",), consumers=("backtests",), risk=NONE,
    ),
    Param(
        path=f"{CAPITAL}.retention_months", group="capital", label="Months kept", kind="int", unit="months",
        minimum=0, optional=True, default=0,
        help="Older months of bars are removed from the database. 0: everything is kept.",
        readers=("capital_feed",), consumers=("backtests",), risk=SERVICE,
    ),
    Param(
        path=f"{CAPITAL}.alert_after_seconds", group="capital", label="Say silence after", kind="int", unit="s",
        minimum=30, optional=True, default=300,
        help="While the market is open, no price for this long is said on Telegram.",
        readers=("capital_feed",), risk=SERVICE,
    ),
)

TA_PARAMS = (
    Param(
        path=f"{TA}.enabled", group="ta", label="Run the signal rules", kind="bool", optional=True, default=False,
        help="WATA's own signals, from the Pine scripts of the signal rules run on Capital.com's bars.",
        readers=("ta_engine", ADMIN), risk=TRADING,
        effects=("A live rule's signals go to the web server like TradingView's; a shadow rule's are only journaled.",),
        also_needs=(TA_PROFILE, "The Capital.com feed recording the rules' instruments."),
        caveats=(IDLE_ON_A_MISTAKE,),
    ),
    Param(
        path=f"{TA}.history_days", group="ta", label="Days of history", kind="int", unit="days", minimum=0, maximum=30,
        optional=True, default=1,
        help="A rule's run starts this many days before the daily restart, unless the rule has its own.",
        readers=("ta_engine",), consumers=("backtests",), risk=TRADING,
        effects=("More days warm up a script longer, and each run over all of them costs more.",),
    ),
    Param(
        path=f"{TA}.restart_time", group="ta", label="Daily restart at", kind="time",
        optional=True, default="21:15",
        help="When every rule starts again from its history: best while the market is closed.",
        readers=("ta_engine", ADMIN), consumers=("backtests",), risk=TRADING,
        caveats=("In UTC, not in the trading timezone.",),
    ),
    Param(
        path=f"{TA}.max_signal_age_seconds", group="ta", label="Oldest signal sent", kind="int", unit="s", minimum=5,
        maximum=3600, optional=True, default=60,
        help="A signal whose bar closed longer ago is journaled, never sent.",
        readers=("ta_engine", ADMIN), risk=TRADING,
        related=("trade.rules[signal_validation].rule_config.max_signal_age_seconds",),
    ),
    Param(
        path=f"{TA}.stale_feed_seconds", group="ta", label="Feed too old after", kind="int", unit="s", minimum=20,
        maximum=3600, optional=True, default=90,
        help="Without ten seconds recorded by the Capital.com feed for this long, nothing is sent.",
        readers=("ta_engine", ADMIN), risk=TRADING,
    ),
    Param(
        path=f"{TA}.run_timeout_seconds", group="ta", label="A run may last", kind="int", unit="s", minimum=5,
        maximum=600, optional=True, default=30,
        help="A run that takes longer stops the runner, which starts again for the next bar.",
        readers=("ta_engine", ADMIN), risk=SERVICE,
    ),
    Param(
        path=f"{TA}.runner_memory_mb", group="ta", label="Live rules' runner heap", kind="int", unit="MB", minimum=128,
        maximum=4096, optional=True, default=512,
        help="The Node heap of the runner of the live rules.",
        readers=("ta_engine",), risk=SERVICE,
        caveats=("The container has 2,560 MB: a runner holds more than its heap.",),
    ),
    Param(
        path=f"{TA}.shadow_runner_memory_mb", group="ta", label="Shadow rules' runner heap", kind="int", unit="MB",
        minimum=128, maximum=4096, optional=True, default=256,
        help="The Node heap of the runner of the shadow rules, which never reloads a live rule.",
        readers=("ta_engine",), risk=SERVICE,
    ),
    Param(
        path=f"{TA}.shadow_seconds_per_tick", group="ta", label="Shadow rules' time per tick", kind="int", unit="s",
        minimum=1, maximum=8, optional=True, default=5,
        help="At most this long of each ten-second tick goes to the shadow rules, after the live ones.",
        readers=("ta_engine",), risk=SERVICE,
    ),
    Param(
        path=f"{TA}.request_timeout_seconds", group="ta", label="Web server answers within", kind="int", unit="s",
        minimum=1, maximum=30, optional=True, default=3,
        help="How long the engine waits for the web server to take a signal.",
        readers=("ta_engine",), risk=TRADING,
    ),
    Param(
        path=f"{TA}.monitoring_days", group="ta", label="Days of measures kept", kind="int", unit="days", minimum=1,
        maximum=30, optional=True, default=7,
        help="How long the engine keeps what it measures of itself, for the Engine monitor.",
        readers=("ta_engine",), risk=NONE,
    ),
)

PARAMS = RECORDER_PARAMS + CAPITAL_PARAMS + TA_PARAMS
