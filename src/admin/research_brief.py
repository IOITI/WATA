"""
The research brief: what a research task of the AI agent is told at its start, so that it does not start blank.

From the experiment journal, the forward checks and the research queue: the family it works on with its trials, its
latest experiments, how its kept conclusions held on later days and what a skeptic's review said of them; the most
tried families and their record; the forward checks running, and each against the live configuration on the same
days (src/admin/challengers.py); what the last task of its routine concluded and left to try; the queue's next
hypotheses; the lock box. At most ``MOST_CHARACTERS``, the least useful cut first. It goes into the task's first request, after the
memory's index, never into the system prompt (its cache and its thinking blocks are bound to it). Pure.
"""

MOST_CHARACTERS = 4000
LATEST_EXPERIMENTS = 5
BUSIEST_FAMILIES = 5
QUEUE_SHOWN = 5


def _record_said(group: dict) -> str:
    forward = group.get("forward") or {}
    said = f"{group.get('trials', 0)} trials, {group.get('kept', 0)} kept of {group.get('experiments', 0)}"
    if group.get("kept_against_bar"):
        said += f" ({group['kept_against_bar']} against the bar)"
    judged = forward.get("held", 0) + forward.get("watch", 0) + forward.get("broken", 0)
    if judged or forward.get("too_early"):
        said += (f"; on later days {forward.get('held', 0)} held, {forward.get('watch', 0)} on watch, "
                 f"{forward.get('broken', 0)} broken, {forward.get('too_early', 0)} too early")
    return said


BOARD_SHOWN = 5
# How the challenger board's verdicts read.
BOARD_SAID = {"ahead": "ahead of", "behind": "behind", "even": "even with", "too_few": "too few days to say against"}


def brief(*, experiments: list[dict], checks: dict[str, dict], record: dict, queue: list[dict], family: str | None = None,
          last_task: dict | None = None, lockbox: dict | None = None, board: list[dict] | None = None) -> str:
    """The brief in words (see the module's docstring); empty when the journal holds nothing yet."""
    if not experiments and not queue and not last_task:
        return ""
    families = {entry.get("family"): entry for entry in record.get("families") or []}
    parts: list[str] = ["What the research has done so far (the experiment journal on the server):"]
    if family:
        group = families.get(family)
        if group:
            parts.append(f"- Its family, {family}: {_record_said(group)}.")
            latest = [entry for entry in reversed(experiments) if entry.get("family") == family][:LATEST_EXPERIMENTS]
            for entry in latest:
                verdict = entry.get("verdict") or "open"
                held = (checks.get(entry.get("id")) or {}).get("verdict")
                review = (entry.get("review") or {}).get("tone")
                parts.append(f"  · {entry.get('title')} ({entry.get('id')}): {verdict}"
                             + (f", later days: {held.replace('_', ' ')}" if held else "")
                             + (f", the skeptic's review: {review}" if review else "") + ".")
            if group.get("trials", 0) >= 100 and not (group.get("forward") or {}).get("held"):
                parts.append("  Many trials and no kept conclusion that held on later days: say what is new before "
                             "trying it again, or try another idea.")
        else:
            parts.append(f"- Its family, {family}: new, no trial yet.")
    busiest = [entry for entry in record.get("families") or [] if entry.get("family") != family][:BUSIEST_FAMILIES]
    if busiest:
        parts.append("- The most tried families: " + "; ".join(f"{entry.get('family')} ({_record_said(entry)})" for entry in busiest) + ".")
    running = [(entry, checks[entry["id"]]) for entry in experiments if (checks.get(entry.get("id")) or {}).get("state") == "active"]
    if running:
        parts.append("- Forward checks running: " + "; ".join(
            f"{entry.get('title')}: {(check.get('verdict') or 'nothing judged yet').replace('_', ' ')} after "
            f"{check.get('trades', 0)} trades" for entry, check in running) + ".")
    lines = [line for line in board or [] if line.get("days")][:BOARD_SHOWN]
    if lines:
        parts.append("- Against the live configuration on the same days: " + "; ".join(
            f"{line.get('title')}: {BOARD_SAID.get(line.get('verdict'), line.get('verdict'))} it over {line['days']} days "
            f"(better on {line.get('better_days', 0)}, worse on {line.get('worse_days', 0)})" for line in lines) + ".")
    if last_task:
        said = f"- The last task of this routine concluded: {str(last_task.get('conclusion') or 'nothing').strip()[:600]}"
        if last_task.get("next_steps"):
            said += f" Its next steps: {str(last_task['next_steps']).strip()[:400]}"
        parts.append(said)
    waiting = [item for item in queue if item.get("state") == "waiting"][:QUEUE_SHOWN]
    if waiting:
        parts.append("- The research queue's next: " + "; ".join(
            f"{item.get('title')} ({item.get('family')}, {'the operator' if item.get('by') == 'operator' else 'filed by the agent'})"
            for item in waiting) + ".")
    if lockbox:
        until = lockbox.get("to") or "every day after"
        parts.append(f"- The lock box holds {lockbox.get('from')} to {until}: never studied before a candidate's final check.")
    else:
        parts.append("- No lock box: forward checks judge kept conclusions on later days.")
    text = "\n".join(parts)
    while len(text) > MOST_CHARACTERS and len(parts) > 2:
        # The busiest families go first, then the oldest of the family's experiments.
        cut = next((index for index, part in enumerate(parts) if part.startswith("- The most tried")), None)
        parts.pop(cut if cut is not None else 2)
        text = "\n".join(parts)
    return text[:MOST_CHARACTERS]
