"""
What the operator attaches to a conversation with the AI agent
(src/admin/agent/uploads.py, src/admin/agent/service.py): files of their
computer, staged until the request that sends them, then kept with the
conversation and sent with each of its requests as the Claude API takes them;
and WATA's own things, checked and shared as a conversation's subject is. Five
at most in a conversation, none twice, never one of the admin's own subjects;
a model that reads no image is sent none.
"""

import asyncio
import base64
import json

import pytest

from src.admin.agent import transcript
from src.admin.agent.service import MAX_ATTACHED, AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent.uploads import (
    MAX_BYTES,
    MAX_STAGED,
    STAGED_SECONDS,
    UnknownFile,
    Uploads,
    checked,
    kind_of,
    said_name,
)
from src.admin.errors import InvalidRequest
from tests.test_agent_context import CountingProvider
from tests.test_agent_loop import (
    NOW,
    FakeBacktests,
    FakeProvider,
    FakeScripts,
    FakeTools,
    Published,
    answer,
    settled,
    text,
)
from tests.test_agent_subjects import Queries

RUN = "20261027-101010-abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
PDF = b"%PDF-1.7\n" + b"0" * 64
CSV = "day,result\n2026-10-30,1.2\n".encode()


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def make_agent(tmp_path, provider=None, clock=None):
    async def the_key():
        return "sk-ant-x"

    return AgentService(settings=AgentSettings(enabled=True), store=ConversationStore(str(tmp_path / "agent")),
                        provider=provider or FakeProvider(), tools=FakeTools(), proposals=None,
                        backtests=FakeBacktests(), scripts=FakeScripts(), key=the_key, publish=Published(),
                        clock=clock or (lambda: NOW), queries=Queries())


# ── a file chosen: what it is, what it may weigh, a text cleaned ──


@pytest.mark.parametrize("name, data, kind, media_type", [
    ("chart.png", PNG, "image", "image/png"),
    ("photo", b"\xff\xd8\xff\xe0" + b"0" * 16, "image", "image/jpeg"),
    ("loop.gif", b"GIF89a" + b"0" * 16, "image", "image/gif"),
    ("shot.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
    # What a file is comes from its first bytes, whatever its name says.
    ("report.txt", PDF, "pdf", "application/pdf"),
    ("export.CSV", CSV, "text", "text/plain"),
    ("cross.pine", b"//@version=6\nindicator('x')", "text", "text/plain"),
])
def test_a_file_is_known_by_its_first_bytes_or_as_a_text_by_its_name(name, data, kind, media_type):
    assert kind_of(name, data) == (kind, media_type)


@pytest.mark.parametrize("name, data, said", [
    ("tool.exe", b"MZ\x90\x00" + b"0" * 32, "Attach an image"),
    ("page.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "Attach an image"),
    ("export.csv", "day;résultat".encode("latin-1"), "UTF-8"),
    ("export.csv", b"day\x00result", "NUL"),
    ("empty.png", b"", "empty"),
])
def test_what_is_not_an_image_a_pdf_or_a_text_is_refused(name, data, said):
    with pytest.raises(InvalidRequest, match=said):
        checked(name, data)


def test_each_kind_of_file_has_its_limit():
    with pytest.raises(InvalidRequest, match="5,120 kB at most"):
        checked("chart.png", PNG + b"0" * MAX_BYTES["image"])
    with pytest.raises(InvalidRequest, match="text file is 200 kB at most"):
        checked("export.csv", b"a" * (MAX_BYTES["text"] + 1))
    assert checked("chart.png", PNG)["data"] == PNG


def test_a_text_is_cleaned_of_what_looks_like_a_secret_and_says_how_much_was_masked():
    kept = checked("calls.txt", b"GET /latest-signals?token=0123456789abcdef0123 HTTP/1.1\nAuthorization: Bearer "
                                b"abcdefghijklmnopqrstuvwxyz012345\n")

    assert b"0123456789abcdef0123" not in kept["data"] and b"abcdefghijklmnop" not in kept["data"]
    assert kept["masked"] == 2


def test_a_file_s_name_is_only_ever_said_never_a_path():
    assert said_name("../../etc/passwd") == "passwd"
    assert said_name("C:\\Users\\me\\chart\x00.png") == "chart.png"
    assert said_name("") == "file"
    long = said_name("a" * 300 + ".csv")
    assert len(long) == 120 and long.endswith("….csv")


def test_a_staged_file_waits_an_hour_for_its_request_and_ten_wait_at_most(tmp_path):
    clock = Clock()
    uploads = Uploads(str(tmp_path / "uploads"), clock=clock)
    staged = uploads.stage("chart.png", PNG)

    assert staged == {"id": staged["id"], "name": "chart.png", "type": "image", "media_type": "image/png",
                      "size": len(PNG), "masked": 0}
    assert uploads.describe(staged["id"]) == staged
    with pytest.raises(UnknownFile):
        uploads.describe("../" + staged["id"])
    for _ in range(MAX_STAGED - 1):
        uploads.stage("export.csv", CSV)
    with pytest.raises(InvalidRequest, match="10 files wait"):
        uploads.stage("export.csv", CSV)

    clock.now += STAGED_SECONDS + 1
    with pytest.raises(UnknownFile):
        uploads.describe(staged["id"])
    # Past their hour, the next one staged finds room: those before are pruned.
    assert uploads.stage("export.csv", CSV)["type"] == "text"
    assert len(list((tmp_path / "uploads").iterdir())) == 2


def test_a_file_taken_into_a_conversation_or_removed_is_no_longer_staged(tmp_path):
    uploads = Uploads(str(tmp_path / "uploads"), clock=Clock())
    first, second = uploads.stage("chart.png", PNG), uploads.stage("report.pdf", PDF)

    said, data = uploads.take(first["id"])
    uploads.remove(second["id"])

    assert said["name"] == "chart.png" and data == PNG
    for file_id in (first["id"], second["id"]):
        with pytest.raises(UnknownFile):
            uploads.describe(file_id)
    with pytest.raises(UnknownFile):
        uploads.remove(second["id"])


# ── attached to a conversation ──


def test_a_new_conversation_holds_what_was_attached_and_is_about_the_first_thing(tmp_path):
    agent = make_agent(tmp_path)
    image = agent.uploads.stage("chart.png", PNG)

    meta = asyncio.run(agent.create(None, "What do they show?", "10.8.0.2", attach=[
        {"kind": "file", "id": image["id"]}, {"kind": "run", "id": RUN},
        {"kind": "script", "id": "0d4fd1117e2f", "version": 3}]))

    run = {"kind": "run", "id": RUN, "label": "Cross R60"}
    assert meta["subject"] == run
    assert [(entry["kind"], entry["request"]) for entry in meta["attached"]] == [
        ("file", 0), ("run", 0), ("script", 0)]
    assert agent.store.read_attachment(meta["id"], image["id"]) == PNG
    with pytest.raises(UnknownFile):
        agent.uploads.describe(image["id"])
    asked = next(event for event in agent.store.read(meta["id"], "activity") if event["event"] == "request")
    assert asked["shared"] == run and [entry["kind"] for entry in asked["attached"]] == ["file", "run", "script"]


def test_the_page_s_subject_comes_first_and_counts_among_the_five(tmp_path):
    agent = make_agent(tmp_path)
    days = [{"kind": "day", "id": f"2026-10-{day}"} for day in (26, 27, 28, 29)]

    meta = asyncio.run(agent.create({"kind": "position", "id": "5001"}, "Why?", "10.8.0.2", attach=days))

    assert meta["subject"]["kind"] == "position" and len(meta["attached"]) == MAX_ATTACHED
    with pytest.raises(InvalidRequest, match="5 things attached at most"):
        asyncio.run(agent.create({"kind": "position", "id": "5001"}, "Why?", "10.8.0.2",
                                 attach=[*days, {"kind": "day", "id": "2026-10-30"}]))


@pytest.mark.parametrize("attach, said", [
    ([{"kind": "run", "id": RUN}, {"kind": "run", "id": RUN}], "attached already"),
    ([{"kind": "briefing", "id": "2026-10-30"}], "Attach a run"),
    ([{"kind": "change", "id": "0a1b2c3d"}], "Attach a run"),
    ([{"kind": "draft", "id": "0a1b2c3d"}], "Attach a run"),
    ([{"kind": "file", "id": "f0123456789"}], "no longer waiting"),
    ([{"kind": "position", "id": "9"}], "no position 9"),
])
def test_what_cannot_be_attached_is_refused_before_anything_is_kept(tmp_path, attach, said):
    agent = make_agent(tmp_path)

    with pytest.raises(InvalidRequest, match=said):
        asyncio.run(agent.create(None, "Why?", "10.8.0.2", attach=attach))
    assert agent.store.metas() == []


def test_a_script_s_versions_and_an_incident_s_moments_are_each_their_own(tmp_path):
    agent = make_agent(tmp_path)

    meta = asyncio.run(agent.create(None, "Which is better?", "10.8.0.2", attach=[
        {"kind": "script", "id": "0d4fd1117e2f", "version": 3}, {"kind": "script", "id": "0d4fd1117e2f", "version": 4},
        {"kind": "incident", "id": "trader", "at": "2026-10-30 14:01"},
        {"kind": "incident", "id": "trader", "at": "2026-10-30 15:12"}]))

    assert len(meta["attached"]) == 4


def test_a_later_request_attaches_more_and_the_conversation_is_listed_beside_each(tmp_path):
    agent = make_agent(tmp_path)
    # A conversation begun from a page, before anything could be attached: its subject alone.
    made = asyncio.run(agent.create({"kind": "position", "id": "5001"}, "Why?", "10.8.0.2"))
    assert "attached" not in made
    agent.store.update_meta(made["id"], status="idle")
    sheet = agent.uploads.stage("export.csv", CSV)

    meta = asyncio.run(agent.ask(made["id"], "And with these?", "10.8.0.2", attach=[
        {"kind": "run", "id": RUN}, {"kind": "file", "id": sheet["id"]}]))

    assert [(entry["kind"], entry["request"]) for entry in meta["attached"]] == [
        ("position", 0), ("run", 1), ("file", 1)]
    assert [item["id"] for item in agent.conversations(f"run:{RUN}")] == [made["id"]]
    assert [item["id"] for item in agent.conversations("position:5001")] == [made["id"]]
    assert agent.conversations(f"run:{RUN}")[0]["attached"] == 3
    entry, data = agent.attachment(made["id"], sheet["id"])
    assert entry["name"] == "export.csv" and data == CSV
    with pytest.raises(UnknownFile):
        agent.attachment(made["id"], "f0123456789")
    agent.store.update_meta(made["id"], status="idle")
    with pytest.raises(InvalidRequest, match="attached already"):
        asyncio.run(agent.ask(made["id"], "Again?", "10.8.0.2", attach=[{"kind": "position", "id": "5001"}]))


def test_a_model_that_reads_no_image_is_sent_none(tmp_path):
    agent = make_agent(tmp_path)
    agent._models["claude-haiku-5-5"] = {"available": True, "images": False}
    image = agent.uploads.stage("chart.png", PNG)

    with pytest.raises(InvalidRequest, match="Claude Haiku 5.5 does not read images"):
        asyncio.run(agent.create(None, "What is it?", "10.8.0.2", "claude-haiku-5-5", "low",
                                 attach=[{"kind": "file", "id": image["id"]}]))
    # Nothing was kept, and the file still waits for a model that reads it.
    assert agent.store.metas() == [] and agent.uploads.describe(image["id"])["name"] == "chart.png"
    assert agent.state()["models"][2]["images"] is False and agent.state()["models"][0]["images"] is None


def test_what_is_attached_is_shared_in_order_then_the_files_as_the_api_takes_them(tmp_path):
    provider = FakeProvider(answer(text("Read.")))
    agent = make_agent(tmp_path, provider)
    image, sheet, paper = (agent.uploads.stage("chart.png", PNG), agent.uploads.stage("export.csv", CSV),
                           agent.uploads.stage("report.pdf", PDF))

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "What do they show?", "10.8.0.2", attach=[
            {"kind": "run", "id": RUN}, {"kind": "file", "id": image["id"]}, {"kind": "file", "id": sheet["id"]},
            {"kind": "file", "id": paper["id"]}, {"kind": "position", "id": "5001"}])
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())

    sent = provider.asked[0]["messages"][0]["content"]
    kinds = [block["type"] for block in sent]
    assert kinds == ["text", "text", "text", "text", "image", "document", "document", "text"][-len(kinds):]
    shared = [block["text"] for block in sent if block["type"] == "text"]
    assert shared[-4].startswith("The operator shares the backtest run Cross R60")
    assert shared[-3].startswith("The operator shares the position long Turbo long US100 21000")
    assert "1. chart.png, an image\n2. export.csv, a text file\n3. report.pdf, a PDF" in shared[-2]
    assert shared[-1] == "What do they show?"
    assert sent[-4] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(PNG).decode("ascii")}}
    assert sent[-3] == {"type": "document", "title": "export.csv",
                        "source": {"type": "text", "media_type": "text/plain", "data": CSV.decode()}}
    assert sent[-2]["source"] == {"type": "base64", "media_type": "application/pdf",
                                  "data": base64.standard_b64encode(PDF).decode("ascii")}
    # The transcript keeps where each file is, never its bytes: the pages never read one.
    kept = agent.store.read(meta["id"], "messages")[0]["content"]
    assert [block["type"] for block in kept if block["type"] == transcript.FILE_BLOCK] == [transcript.FILE_BLOCK] * 3
    assert "base64" not in json.dumps(kept)
    said = agent.conversation(meta["id"])["requests"][0]["attached"]
    assert [entry.get("label") or entry.get("name") for entry in said] == [
        "Cross R60", "chart.png", "export.csv", "report.pdf", "long Turbo long US100 21000"]


def test_a_file_is_sent_the_same_at_each_answer_and_said_gone_once_it_is():
    lines = [{"role": "user", "content": [
        text("Look."), transcript.file_block({"id": "f0123456789", "type": "image", "media_type": "image/png",
                                              "name": "chart.png"})]}]

    first = transcript.api_messages(lines, {"f0123456789": PNG}.get)
    second = transcript.api_messages(lines, {"f0123456789": PNG}.get)
    gone = transcript.api_messages(lines, {}.get)

    assert first == second and first[0]["content"][1]["type"] == "image"
    assert gone[0]["content"][1] == {"type": "text", "text": "(The file chart.png is no longer kept.)"}
    assert transcript.files_in(lines) == 1


def test_the_context_window_says_what_the_files_attached_hold(tmp_path):
    provider = CountingProvider()
    agent = make_agent(tmp_path, provider)
    image = agent.uploads.stage("chart.png", PNG * 40)
    made = asyncio.run(agent.create(None, "What is it?", "10.8.0.2", attach=[{"kind": "file", "id": image["id"]}]))
    agent.store.update_meta(made["id"], status="idle")
    agent.store.append(made["id"], "messages", {"role": "user", "request": 0, "content": [
        text("The operator attached these files"),
        transcript.file_block({"id": image["id"], "type": "image", "media_type": "image/png", "name": "chart.png"}),
        text("What is it?")]})

    view = asyncio.run(agent.context(made["id"]))

    files = next(part for part in view["parts"] if part["id"] == "files")
    encoded = len(json.dumps({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                          "data": base64.standard_b64encode(PNG * 40).decode()}}))
    assert files == {"id": "files", "tokens": encoded // 4, "count": 1}
    assert view["total"] == sum(part["tokens"] for part in view["parts"])


def test_a_conversation_s_files_go_with_it(tmp_path):
    agent = make_agent(tmp_path)
    image = agent.uploads.stage("chart.png", PNG)
    made = asyncio.run(agent.create(None, "What is it?", "10.8.0.2", attach=[{"kind": "file", "id": image["id"]}]))
    agent.store.update_meta(made["id"], status="idle")

    agent.remove(made["id"])

    assert not (tmp_path / "agent" / "conversations" / made["id"]).exists()


class _Found:
    def __init__(self, said):
        self.said = said

    def model_dump(self, mode):
        return self.said


class _Models:
    def __init__(self, said):
        self.said = said

    async def retrieve(self, model_id):
        return _Found(self.said)


class _Client:
    def __init__(self, said):
        self.models = _Models(said)

    def with_options(self, **_):
        return self


def test_the_models_api_says_whether_a_model_reads_images_and_pdfs():
    from src.admin.agent import provider as claude

    async def read(capabilities):
        provider = claude.ClaudeProvider(lambda: None)

        async def current():
            return _Client({"display_name": "Claude Haiku 5.5", "max_input_tokens": 1_000_000, "max_tokens": 128_000,
                            "capabilities": capabilities})

        provider._current = current
        return await provider.model_info("claude-haiku-5-5")

    said = asyncio.run(read({"image_input": {"supported": True}, "pdf_input": {"supported": False}}))
    unsaid = asyncio.run(read({}))

    assert (said["images"], said["pdfs"]) == (True, False) and (unsaid["images"], unsaid["pdfs"]) == (None, None)
