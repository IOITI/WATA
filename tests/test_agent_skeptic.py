"""
A kept conclusion read again by a skeptic (src/admin/agent_skeptic.py): asked once for each experiment concluded kept
with a candidate, the market closed, as a review that only reads and starts no backtest; once its conversation is
over, the verdict it set on the candidate is written beside the experiment in the journal, and written again when a
later request sets one.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import UnknownConversation
from src.admin.agent.verdicts import VerdictStore
from src.admin.agent_skeptic import SKEPTIC, SkepticReviews
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun
from src.research.experiments import ExperimentJournal

# Friday 20 November 2026, 22:30 in Paris: the market of the config below (9 to 10) closed.
NOW = datetime(2026, 11, 20, 21, 30, tzinfo=timezone.utc).timestamp()
CANDIDATE = "20261114-113341-aa2d8f"


class FakeAgent:
    def __init__(self, **settings):
        self.settings = AgentSettings(**{"enabled": True, "skeptic_review": True, **settings})
        self.ready = True
        self.created = []
        self.refusal = None
        # The runs gone, and what asking about each raises.
        self.gone = {}
        self.statuses = {}

    async def create(self, subject, prompt, by, model=None, effort=None, *, task=None, debrief=None, job=None):
        if self.refusal:
            raise self.refusal
        if subject["id"] in self.gone:
            raise self.gone[subject["id"]]
        conversation = f"20261120-2130{len(self.created):02d}-abcdef"
        self.created.append({"subject": subject, "prompt": prompt, "by": by, "model": model, "effort": effort,
                             "job": job, "id": conversation})
        self.statuses[conversation] = "queued"
        return {"id": conversation}

    def conversation(self, conversation_id):
        if conversation_id not in self.statuses:
            raise UnknownConversation(conversation_id)
        return {"id": conversation_id, "status": self.statuses[conversation_id]}


@pytest.fixture
def journal(tmp_path):
    journal = ExperimentJournal(tmp_path / "experiments.jsonl", tmp_path / "lockbox.json", clock=lambda: NOW - 3600)
    kept = journal.new("Open block", "The open's whipsaw loses", "kernel", motivated_by=["2026-11-11"])["experiment"]
    journal.conclude(kept, "kept", "Rests on 22 days", candidate=CANDIDATE, checks={"failed": ["edge"], "open": []})
    rejected = journal.new("Slower line", "A slower line holds", "kernel")["experiment"]
    journal.conclude(rejected, "rejected", "No edge")
    journal.kept_id = kept
    return journal


def reviews_of(tmp_path, agent, journal, open_market=False):
    config = tmp_path / "config.json"
    hours = (0, 24) if open_market else (9, 10)
    config.write_text(json.dumps({"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
        {"rule_type": "market_hours", "rule_name": "market_hours",
         "rule_config": {"trading_start_hour": hours[0], "trading_end_hour": hours[1]}}]}}))
    verdicts = VerdictStore(str(tmp_path / "agent"))
    reviews = SkepticReviews(agent=agent, journal=journal, verdicts=verdicts, config_path=str(config),
                             path=str(tmp_path / "agent" / "skeptic.json"), clock=lambda: NOW)
    return reviews, verdicts


def test_a_kept_conclusion_is_reviewed_once_by_a_reading_that_starts_nothing(tmp_path, journal):
    agent = FakeAgent()
    reviews, _ = reviews_of(tmp_path, agent, journal)

    first = asyncio.run(reviews.catch_up())
    again = asyncio.run(reviews.catch_up())

    assert first and again is None and len(agent.created) == 1
    [asked] = agent.created
    assert asked["subject"] == {"kind": "run", "id": CANDIDATE} and asked["by"] == SKEPTIC
    assert (asked["model"], asked["effort"]) == ("claude-sonnet-5-5", "medium")
    assert asked["job"] == {"kind": SKEPTIC, "experiment": journal.kept_id, "read_only": True, "backtests": 0,
                            "max_usd": 0.5, "title": "Skeptic's review of Open block"}
    assert "kept with checks of the bar failed (edge)" in asked["prompt"] and "came from 2026-11-11" in asked["prompt"]


@pytest.mark.parametrize("why", ["off", "market", "held"])
def test_no_review_switched_off_with_the_market_open_or_while_the_agent_cannot_start_one(tmp_path, journal, why):
    agent = FakeAgent(skeptic_review=why != "off")
    if why == "held":
        agent.refusal = NotPossible("The AI agent is paused")
    reviews, _ = reviews_of(tmp_path, agent, journal, open_market=why == "market")

    assert asyncio.run(reviews.catch_up()) is None
    assert reviews.asked() == {}


@pytest.mark.parametrize("gone", [InvalidRequest(f"No backtest {CANDIDATE}"), UnknownRun(CANDIDATE)])
def test_a_candidate_gone_is_never_asked_again_and_holds_no_other_back(tmp_path, journal, gone):
    agent = FakeAgent()
    agent.gone[CANDIDATE] = gone
    later = journal.new("Late entries", "h", "opens")["experiment"]
    journal.conclude(later, "kept", "n", candidate="20261119-100000-cccccc")
    reviews, _ = reviews_of(tmp_path, agent, journal)

    asked = asyncio.run(reviews.catch_up())

    assert reviews.asked()[journal.kept_id]["refused"] == f"No backtest {CANDIDATE}"
    assert asked and [entry["subject"]["id"] for entry in agent.created] == ["20261119-100000-cccccc"]


def test_its_verdict_is_written_in_the_journal_once_its_conversation_is_over(tmp_path, journal):
    agent = FakeAgent()
    reviews, verdicts = reviews_of(tmp_path, agent, journal)
    conversation = asyncio.run(reviews.catch_up())

    # Under way: nothing yet.
    assert reviews.settle() == 0 and journal.get(journal.kept_id)["review"] is None
    agent.statuses[conversation] = "idle"
    verdicts.save(f"run:{CANDIDATE}", "negative", "Two days make it.", conversation=conversation, request=0,
                  at="2026-11-20T21:40:00Z")

    assert reviews.settle() == 1 and reviews.settle() == 0
    review = journal.get(journal.kept_id)["review"]
    assert (review["conversation"], review["tone"], review["line"]) == (conversation, "negative", "Two days make it.")


def test_a_review_without_a_verdict_is_written_so_then_again_once_one_is_set(tmp_path, journal):
    agent = FakeAgent()
    reviews, verdicts = reviews_of(tmp_path, agent, journal)
    conversation = asyncio.run(reviews.catch_up())
    agent.statuses[conversation] = "failed"
    # Another conversation's verdict on the same run is not this review's.
    verdicts.save(f"run:{CANDIDATE}", "positive", "Looks fine.", conversation="20261119-100000-ffffff", request=0,
                  at="2026-11-19T10:00:00Z")

    assert reviews.settle() == 1 and journal.get(journal.kept_id)["review"]["tone"] is None
    assert reviews.settle() == 0
    verdicts.save(f"run:{CANDIDATE}", "mixed", "Rests on little.", conversation=conversation, request=1,
                  at="2026-11-21T09:00:00Z")
    assert reviews.settle() == 1 and journal.get(journal.kept_id)["review"]["tone"] == "mixed"


def test_a_conclusion_reviewed_already_is_not_asked_again(tmp_path, journal):
    journal.record_review(journal.kept_id, conversation="20261119-100000-ffffff", tone="positive", line="Holds.")
    agent = FakeAgent()
    reviews, _ = reviews_of(tmp_path, agent, journal)

    assert asyncio.run(reviews.catch_up()) is None and agent.created == []
