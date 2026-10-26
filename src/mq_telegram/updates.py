"""
The bot's commands, read from Telegram (getUpdates) in a thread of the telegram
service beside the queue's messages, while ``reporting.telegram_commands`` is
true. Its answers are ``commands.py``'s, which only read.

Only the chat the bot sends to (``telegram.chat_id``) is answered; a message of
any other is passed over without a word, which would tell a stranger the bot
listens. Each command is answered once, in one message: a command older than
``MAX_COMMAND_AGE_SECONDS`` (one sent while the service was stopped, given again
after a restart) is not, nor are those over ``ANSWERS_PER_MINUTE``. Only
getUpdates and sendMessage are asked of Telegram: never a webhook, never the
bot's settings.
"""

import json
import logging
import threading
import time
from collections import deque

import requests

from src.mq_telegram.commands import Calendars, Commands, CommandSettings, command_of

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
# Telegram holds a getUpdates this long while nothing comes; the request waits a little longer.
POLL_SECONDS = 50
HTTP_TIMEOUT_SECONDS = POLL_SECONDS + 10
SEND_TIMEOUT_SECONDS = 10
MAX_COMMAND_AGE_SECONDS = 120
ANSWERS_PER_MINUTE = 6
# Telegram takes 4,096 characters a message.
MAX_ANSWER_CHARS = 4000
# Waits after a failure, growing to the last; a refusal of the bot itself waits the longest.
FAILURE_WAITS = (5, 15, 60)
WRONG_BOT_WAIT_SECONDS = 600
SAID_AGAIN_AFTER_SECONDS = 600


def fitted(text: str) -> str:
    return text if len(text) <= MAX_ANSWER_CHARS else text[: MAX_ANSWER_CHARS - 1] + "…"


class Throttle:
    """At most ``per_minute`` answers in any sixty seconds."""

    def __init__(self, per_minute: int = ANSWERS_PER_MINUTE):
        self._per_minute = per_minute
        self._answered: deque = deque()

    def allows(self, now: float) -> bool:
        while self._answered and now - self._answered[0] >= 60:
            self._answered.popleft()
        if len(self._answered) >= self._per_minute:
            return False
        self._answered.append(now)
        return True

    def free_in(self, now: float) -> int:
        return max(1, int(60 - (now - self._answered[0]))) if self._answered else 0


class CommandPoller:
    """
    One getUpdates after the other, each command answered as ``answer(command)``
    says. ``post`` is ``requests.post`` (a fake in the tests), ``now`` and
    ``sleep`` the clock.
    """

    def __init__(self, token: str, chat_id, answer, *, post=requests.post, now=time.time, sleep=time.sleep):
        self._token = token
        self._chat_id = str(chat_id)
        self._answer = answer
        self._post = post
        self._now = now
        self._sleep = sleep
        self._offset = None
        self._failures = 0
        self._throttle = Throttle()
        self._throttled_said = False
        self._said_at: dict[str, float] = {}

    def _say_now_and_then(self, key: str, message: str, *args) -> None:
        """A log line said once every ``SAID_AGAIN_AFTER_SECONDS``: a failure repeated is not repeated in the log."""
        now = self._now()
        if now - self._said_at.get(key, float("-inf")) >= SAID_AGAIN_AFTER_SECONDS:
            self._said_at[key] = now
            logger.warning(message, *args)

    def _call(self, method: str, payload: dict, timeout: float):
        """Telegram's answer to ``method``, or None after a failure said; its address names the token, never said."""
        try:
            response = self._post(API.format(token=self._token, method=method), data=json.dumps(payload),
                                  headers={"Content-Type": "application/json"}, timeout=timeout)
        except requests.RequestException as e:
            self._say_now_and_then("unreachable", "Telegram could not be reached for the commands: %s", type(e).__name__)
            return None, None
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    def _wait_after(self, status, body) -> float:
        """How long to wait after getUpdates failed, and why it is said."""
        description = str(body.get("description") or "")[:200]
        if status == 409:
            self._say_now_and_then("conflict", "The bot's commands are not read: %s. Another program reads this "
                                   "bot's updates, or it has a webhook", description)
            return FAILURE_WAITS[-1]
        if status in (401, 403, 404):
            self._say_now_and_then("wrong_bot", "Telegram refuses the bot (%s): no command is read", status)
            return WRONG_BOT_WAIT_SECONDS
        if status == 429:
            retry_after = (body.get("parameters") or {}).get("retry_after")
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                return float(retry_after)
        if status is not None:
            self._say_now_and_then("refused", "Telegram refused getUpdates (%s): %s", status, description)
        wait = FAILURE_WAITS[min(self._failures, len(FAILURE_WAITS) - 1)]
        self._failures += 1
        return wait

    def poll_once(self) -> None:
        """One getUpdates and the commands it brought."""
        payload = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        status, body = self._call("getUpdates", payload, HTTP_TIMEOUT_SECONDS)
        if status != 200 or not body.get("ok"):
            self._sleep(self._wait_after(status, body or {}))
            return
        self._failures = 0
        for update in body.get("result") or []:
            if isinstance(update, dict) and isinstance(update.get("update_id"), int):
                # Past it whatever becomes of it: Telegram gives it again until the offset says it was read.
                self._offset = update["update_id"] + 1
                self._handle(update)

    def _handle(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        if str(chat.get("id")) != self._chat_id:
            self._say_now_and_then("stranger", "A message to the bot from another chat was passed over")
            return
        command = command_of(message.get("text") or "")
        if command is None:
            return
        sent_at = message.get("date")
        now = self._now()
        if isinstance(sent_at, (int, float)) and now - sent_at > MAX_COMMAND_AGE_SECONDS:
            logger.info("A command of %d s ago was not answered: %s", int(now - sent_at), command[:40])
            return
        if not self._throttle.allows(now):
            if not self._throttled_said:
                self._throttled_said = True
                self._send(f"Too many commands: the next ones are answered again in {self._throttle.free_in(now)} s.")
            return
        self._throttled_said = False
        logger.info("Answering the command %s", command[:40])
        self._send(self._answer(command))

    def _send(self, text: str) -> None:
        status, body = self._call("sendMessage", {"chat_id": self._chat_id, "text": fitted(text)}, SEND_TIMEOUT_SECONDS)
        if status != 200:
            description = str((body or {}).get("description") or "")[:200]
            self._say_now_and_then("send", "Telegram did not take a command's answer (%s): %s", status, description)

    def run_forever(self) -> None:
        logger.info("Reading the bot's commands (reporting.telegram_commands)")
        while True:
            try:
                self.poll_once()
            except Exception as e:  # a thread that died would answer nothing again until the next restart
                logger.exception("The bot's commands failed: %s", type(e).__name__)
                self._sleep(FAILURE_WAITS[-1])


def start_commands(config_manager, token: str, chat_id) -> threading.Thread:
    """The poller in a thread of its own, which ends with the service: the queue's consumer keeps the main one."""
    commands = Commands(CommandSettings.from_config(config_manager), Calendars.read())
    poller = CommandPoller(token, chat_id, commands.answer)
    thread = threading.Thread(target=poller.run_forever, name="telegram-commands", daemon=True)
    thread.start()
    return thread
