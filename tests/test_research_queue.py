"""The research queue (src/admin/research_queue.py): hypotheses waiting to be tried, in the order they are taken."""

from datetime import date

import pytest

from src.admin.errors import InvalidRequest, NotPossible
from src.admin.research_queue import AGENT_WAITING, OPERATOR, PER_CONVERSATION, QueueConflict, ResearchQueue, tired_families

TODAY = date(2026, 11, 20)


def hypothesis(title="Open block", family="kernel", **more):
    return {"title": title, "hypothesis": f"{title} holds", "family": family, **more}


@pytest.fixture
def queue(tmp_path):
    moments = iter(1_790_000_000.0 + step for step in range(1000))
    return ResearchQueue(str(tmp_path), clock=lambda: next(moments))


def test_the_operators_hypotheses_come_first_then_the_agents_oldest_first(queue):
    queue.add(hypothesis("Agent's first"), "agent:abc", today=TODAY, conversation="abc")
    queue.add(hypothesis("Mine"), OPERATOR, today=TODAY)
    queue.add(hypothesis("Agent's second", family="tired"), "agent:abc", today=TODAY, conversation="abc")
    queue.add(hypothesis("Agent's third"), "agent:def", today=TODAY, conversation="def")

    titles = [item["title"] for item in queue.load({"tired"})["items"]]

    assert titles == ["Mine", "Agent's first", "Agent's third", "Agent's second"]
    assert queue.next({"tired"})["title"] == "Mine"


def test_a_tired_family_is_one_tried_long_without_a_conclusion_that_held():
    record = {"families": [{"family": "kernel", "trials": 225, "forward": {"held": 0}},
                           {"family": "fresh", "trials": 12, "forward": {"held": 0}},
                           {"family": "good", "trials": 300, "forward": {"held": 1}}]}

    assert tired_families(record) == {"kernel"}


def test_the_agent_files_within_its_caps(queue):
    for number in range(PER_CONVERSATION):
        queue.add(hypothesis(f"H{number}"), "agent:abc", today=TODAY, conversation="abc")

    with pytest.raises(NotPossible, match="3 hypotheses at most"):
        queue.add(hypothesis("One more"), "agent:abc", today=TODAY, conversation="abc")
    for number in range(AGENT_WAITING - PER_CONVERSATION):
        queue.add(hypothesis(f"G{number}"), "agent:x", today=TODAY, conversation=f"c{number}")
    with pytest.raises(NotPossible, match="20 of the agent's"):
        queue.add(hypothesis("Too many"), "agent:y", today=TODAY, conversation="y")
    # The operator's are never held back.
    assert queue.add(hypothesis("Mine"), OPERATOR, today=TODAY)["item"]["by"] == OPERATOR


def test_a_hypothesis_is_checked_as_an_experiment_is(queue):
    with pytest.raises(InvalidRequest, match="title"):
        queue.add({"title": "", "hypothesis": "h", "family": "f"}, OPERATOR, today=TODAY)
    with pytest.raises(InvalidRequest, match="still to come"):
        queue.add(hypothesis(motivated_by=["2027-01-01"]), OPERATOR, today=TODAY)
    item = queue.add(hypothesis(criteria=[{"figure": "edge_t", "bound": "at_least", "value": 2.6}],
                                motivated_by=["2026-11-11"]), OPERATOR, today=TODAY)["item"]
    assert item["criteria"][0]["figure"] == "edge_t" and item["motivated_by"] == ["2026-11-11"]


def test_the_operator_reorders_drops_and_edits_from_a_revision(queue):
    first = queue.add(hypothesis("A"), OPERATOR, today=TODAY)["item"]["id"]
    second = queue.add(hypothesis("B"), OPERATOR, today=TODAY)["item"]["id"]
    agents = queue.add(hypothesis("C"), "agent:abc", today=TODAY, conversation="abc")["item"]["id"]
    revision = queue.load()["revision"]

    moved = queue.edit(agents, revision, move=0)
    assert [item["title"] for item in queue.load()["items"][:3]] == ["C", "A", "B"]
    with pytest.raises(QueueConflict):
        queue.edit(first, revision, state="dropped")
    queue.edit(first, moved["revision"], state="dropped")
    assert [item["title"] for item in queue.load()["items"] if item["state"] == "waiting"] == ["C", "B"]
    edited = queue.edit(second, queue.load()["revision"], fields={"title": "B, better said"}, today=TODAY)
    assert edited["item"]["title"] == "B, better said"


def test_a_task_takes_one_and_settles_it(queue):
    item = queue.add(hypothesis(), OPERATOR, today=TODAY)["item"]
    queue.take(item["id"], "20261120-100000-aaaaaa")

    with pytest.raises(NotPossible, match="being tried"):
        queue.edit(item["id"], queue.load()["revision"], state="dropped")
    assert queue.next() is None and [entry["id"] for entry in queue.taken()] == [item["id"]]
    settled = queue.settle("20261120-100000-aaaaaa", done=True, experiment="a4b020", conclusion="Inconclusive: 22 days")
    assert (settled["state"], settled["experiment"]) == ("done", "a4b020")
    assert queue.settle("nothing", done=True) is None


def test_a_task_that_could_not_work_puts_its_hypothesis_back(queue):
    item = queue.add(hypothesis(), OPERATOR, today=TODAY)["item"]
    queue.take(item["id"], "20261120-100000-aaaaaa")

    queue.settle("20261120-100000-aaaaaa", done=False)

    assert queue.next()["id"] == item["id"]


def test_the_autopilot_takes_the_next_at_once_and_names_its_task_once_started(queue):
    first = queue.add(hypothesis("First"), OPERATOR, today=TODAY)["item"]
    queue.add(hypothesis("Second"), OPERATOR, today=TODAY)

    taken = queue.take_next()
    # Taken before its task starts: a second look never takes it again.
    assert taken["id"] == first["id"] and taken["conversation"] is None
    assert queue.take_next()["title"] == "Second"
    queue.started(first["id"], "20261120-100000-aaaaaa")
    assert {item["id"]: item["conversation"] for item in queue.taken()}[first["id"]] == "20261120-100000-aaaaaa"


def test_a_task_refused_puts_its_hypothesis_back_in_its_place(queue):
    first = queue.add(hypothesis("First"), OPERATOR, today=TODAY)["item"]
    queue.add(hypothesis("Second"), OPERATOR, today=TODAY)

    queue.put_back(queue.take_next()["id"])

    assert queue.next()["id"] == first["id"]


def test_a_take_whose_task_never_started_waits_again(tmp_path):
    now = [1_790_000_000.0]
    queue = ResearchQueue(str(tmp_path), clock=lambda: now[0])
    item = queue.add(hypothesis(), OPERATOR, today=TODAY)["item"]
    queue.take_next()

    assert queue.stale_starts() == []
    now[0] += 11 * 60
    assert queue.stale_starts() == [item["id"]] and queue.next()["id"] == item["id"]


def test_a_conversation_files_a_hypothesis_once(queue):
    once = queue.add(hypothesis("Gap fill"), "agent:abc", today=TODAY, conversation="abc")["item"]
    again = queue.add(hypothesis("Gap fill"), "agent:abc", today=TODAY, conversation="abc")["item"]

    assert again["id"] == once["id"] and len(queue.load()["items"]) == 1


def test_a_criterion_names_a_figure_the_bar_knows(queue):
    with pytest.raises(InvalidRequest, match="No figure 'luck'"):
        queue.add(hypothesis(criteria=[{"figure": "luck", "bound": "at_least", "value": 1}]), OPERATOR, today=TODAY)


def test_the_operators_hypothesis_says_what_it_came_from(queue):
    item = queue.add(hypothesis(), OPERATOR, today=TODAY, source="autopsy:20261114-113341-aa2d8f")["item"]

    assert item["source"] == "autopsy:20261114-113341-aa2d8f" and item["by"] == OPERATOR
