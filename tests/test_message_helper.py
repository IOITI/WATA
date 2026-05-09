from src.message_helper import TelegramMessageComposer


def test_add_text_section_accepts_execution_timing_dict():
    composer = TelegramMessageComposer({
        "action": "long",
        "signal_id": "signal-123",
        "signal_timestamp": "2026-04-30T18:46:44Z",
    })

    composer.add_text_section("Execution Timing", {
        "Scale Calc": "0ms",
        "Find Turbo": "458ms",
        "TOTAL": "1088ms",
    })

    message = composer.get_message()

    assert "--- EXECUTION TIMING ---" in message
    assert "```json" in message
    assert '"TOTAL": "1088ms"' in message