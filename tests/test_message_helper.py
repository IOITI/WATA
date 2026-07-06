from datetime import date

from src.message_helper import (
    TelegramMessageComposer,
    build_daily_trading_report_message,
    calculate_winning_streak,
    calculate_average_period_return,
    calculate_milestone_projections,
    merge_daily_performance_series,
)


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


def test_calculate_winning_streak_tracks_current_and_last_win_day():
    streak = calculate_winning_streak([
        {"day_date": "2026/06/01", "sum_profit": 1.44},
        {"day_date": "2026/06/02", "sum_profit": 1.20},
        {"day_date": "2026/06/03", "sum_profit": 1.95},
        {"day_date": "2026/06/04", "sum_profit": -2.44},
    ])

    assert streak["current_streak"] == 0
    assert streak["best_streak"] == 3
    assert streak["last_win_date"] == date(2026, 6, 3)


def test_merge_daily_performance_series_aligns_real_best_and_max_rows():
    merged = merge_daily_performance_series(
        {
            "2026/06/04": -2.45,
            "2026/06/03": 1.95,
            "2026/06/02": 1.20,
        },
        {
            "2026/06/04": 0.72,
            "2026/06/03": 1.95,
            "2026/06/02": 1.20,
        },
        {
            "2026/06/04": 4.23,
            "2026/06/03": 5.10,
            "2026/06/02": 2.07,
        },
        report_date=date(2026, 6, 4),
        days=3,
    )

    assert merged[0]["day_date"] == date(2026, 6, 4)
    assert merged[0]["real"] == -2.45
    assert merged[0]["best"] == 0.72
    assert merged[0]["max"] == 4.23
    assert merged[2]["day_date"] == date(2026, 6, 2)


def test_build_daily_trading_report_message_formats_modern_mobile_report():
    message = build_daily_trading_report_message(
        report_date=date(2026, 6, 4),
        closed_trades=[
            {
                "action": "long",
                "position_id": "y2022",
                "performance_percent": 0.80,
                "max_performance_percent": 1.10,
                "profit_loss": 2.89,
                "execution_time_close": "2022-08-10T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "y2023",
                "performance_percent": 1.10,
                "max_performance_percent": 1.40,
                "profit_loss": 4.56,
                "execution_time_close": "2023-10-10T10:00:00Z",
            },
            {
                "action": "short",
                "position_id": "y2024",
                "performance_percent": 0.95,
                "max_performance_percent": 1.20,
                "profit_loss": 3.78,
                "execution_time_close": "2024-12-10T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "y2025",
                "performance_percent": 1.05,
                "max_performance_percent": 1.30,
                "profit_loss": 4.12,
                "execution_time_close": "2025-11-10T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "w1",
                "performance_percent": 1.12,
                "max_performance_percent": 1.12,
                "profit_loss": 5.50,
                "execution_time_close": "2026-05-29T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "w2",
                "performance_percent": 1.44,
                "max_performance_percent": 1.44,
                "profit_loss": 6.20,
                "execution_time_close": "2026-06-01T10:00:00Z",
            },
            {
                "action": "short",
                "position_id": "w3",
                "performance_percent": 1.20,
                "max_performance_percent": 2.07,
                "profit_loss": 4.80,
                "execution_time_close": "2026-06-02T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "w4",
                "performance_percent": 1.95,
                "max_performance_percent": 5.10,
                "profit_loss": 8.00,
                "execution_time_close": "2026-06-03T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "d1",
                "performance_percent": 0.72,
                "max_performance_percent": 4.23,
                "profit_loss": 0.72,
                "execution_time_close": "2026-06-04T10:00:00Z",
            },
            {
                "action": "long",
                "position_id": "d2",
                "performance_percent": -0.34,
                "max_performance_percent": 0.50,
                "profit_loss": -0.34,
                "execution_time_close": "2026-06-04T10:05:00Z",
            },
            {
                "action": "short",
                "position_id": "d3",
                "performance_percent": -2.82,
                "max_performance_percent": 1.30,
                "profit_loss": -2.82,
                "execution_time_close": "2026-06-04T10:10:00Z",
            },
        ],
        daily_profit_history=[
            {"day_date": "2026/06/01", "sum_profit": 6.20},
            {"day_date": "2026/06/02", "sum_profit": 4.80},
            {"day_date": "2026/06/03", "sum_profit": 8.00},
            {"day_date": "2026/06/04", "sum_profit": -2.44},
        ],
        daily_real={
            "2026/06/04": -2.45,
            "2026/06/03": 1.95,
            "2026/06/02": 1.20,
            "2026/06/01": 1.44,
            "2026/05/31": 0.00,
            "2026/05/30": 0.00,
            "2026/05/29": 1.12,
        },
        daily_best={
            "2026/06/04": 0.72,
            "2026/06/03": 1.95,
            "2026/06/02": 1.20,
            "2026/06/01": 1.44,
            "2026/05/31": 0.00,
            "2026/05/30": 0.00,
            "2026/05/29": 1.12,
        },
        daily_max={
            "2026/06/04": 4.23,
            "2026/06/03": 5.10,
            "2026/06/02": 2.07,
            "2026/06/01": 1.34,
            "2026/05/31": 0.00,
            "2026/05/30": 0.00,
            "2026/05/29": 0.57,
        },
    )

    assert "📊 Trading Report | 2026/06/04" in message
    assert "🔥 Winning Streak: 0 Days (Last win: 2026/06/03)" in message
    assert "🎯 Win Rate: 33.33% (1W | 2L | 0BE)" in message
    assert "🟢 LONGS (2 trades | 1W - 1L)" in message
    assert "🔴 SHORTS (1 trade | 0W - 1L)" in message
    assert "--- 🗓 CUMULATIVE P/L ---" in message
    assert "⏱  7 Days:" in message
    assert "--- 📊 LAST 7 DAYS (Real | Best | Max) ---" in message
    assert "06/04:" in message
    assert "--- 📅 LAST 10 WEEKS ---" in message
    assert "--- 📅 LAST 12 MONTHS ---" in message
    assert "June:" in message
    assert "May:" in message
    assert "--- 📅 LAST 5 YEARS ---" in message
    assert "2026:" in message
    assert "2025:" in message
    assert "2024:" in message
    assert "2023:" in message
    assert "2022:" in message


def test_build_daily_trading_report_message_omits_monthly_and_yearly_sections_without_data():
    message = build_daily_trading_report_message(
        report_date=date(2026, 6, 4),
        closed_trades=[],
        daily_profit_history=[],
        daily_real={},
        daily_best={},
        daily_max={},
    )

    assert "--- 📅 LAST 12 MONTHS ---" not in message
    assert "--- 📅 LAST 5 YEARS ---" not in message
    assert "--- 🚀 AVERAGES & MILESTONES ---" not in message


def test_calculate_average_period_return_includes_zero_return_periods():
    trades = [
        {"action": "long", "position_id": "w1", "performance_percent": 10.0, "execution_time_close": "2026-06-01T10:00:00Z"},
        {"action": "long", "position_id": "w3", "performance_percent": 5.0, "execution_time_close": "2026-06-15T10:00:00Z"},
    ]

    # Weeks of 06/01 and 06/15 have trades, the week in between has none (0%).
    avg_weekly = calculate_average_period_return(trades, date(2026, 6, 15), "week")

    assert avg_weekly == round((10.0 + 0.0 + 5.0) / 3, 2)


def test_calculate_milestone_projections_skips_already_surpassed_milestones():
    projections = calculate_milestone_projections(
        current_balance=600_000,
        avg_period_percent=2.0,
        milestones=[100_000, 500_000, 1_000_000],
        period_kind="week",
        report_date=date(2026, 6, 4),
        horizon_periods=260,
    )

    milestones_shown = [row["milestone"] for row in projections]
    assert 100_000 not in milestones_shown
    assert 500_000 not in milestones_shown
    assert milestones_shown == [1_000_000]


def test_calculate_milestone_projections_labels_beyond_horizon_as_not_within_horizon():
    projections = calculate_milestone_projections(
        current_balance=100.0,
        avg_period_percent=0.5,
        milestones=[1_000_000],
        period_kind="week",
        report_date=date(2026, 6, 4),
        horizon_periods=10,
    )

    assert len(projections) == 1
    assert projections[0]["within_horizon"] is False


def test_calculate_milestone_projections_returns_empty_without_positive_growth():
    projections = calculate_milestone_projections(
        current_balance=1000.0,
        avg_period_percent=0.0,
        milestones=[100_000],
        period_kind="week",
        report_date=date(2026, 6, 4),
        horizon_periods=260,
    )

    assert projections == []


def test_build_daily_trading_report_message_includes_balance_and_milestones():
    closed_trades = [
        {
            "action": "long",
            "position_id": "m1",
            "performance_percent": 5.0,
            "max_performance_percent": 5.0,
            "profit_loss": 2.0,
            "execution_time_close": "2026-06-01T10:00:00Z",
        },
        {
            "action": "long",
            "position_id": "m2",
            "performance_percent": 3.0,
            "max_performance_percent": 3.0,
            "profit_loss": 1.5,
            "execution_time_close": "2026-06-04T10:00:00Z",
        },
    ]

    message = build_daily_trading_report_message(
        report_date=date(2026, 6, 4),
        closed_trades=closed_trades,
        daily_profit_history=[{"day_date": "2026/06/04", "sum_profit": 1.5}],
        daily_real={},
        daily_best={},
        daily_max={},
        current_balance=27.54,
        milestones_eur=[100_000, 500_000],
    )

    assert "💵 Current Account Balance: 27.54 €" in message
    assert "--- 🚀 AVERAGES & MILESTONES ---" in message
    assert "📈 Avg P/L per Week:" in message
    assert "Milestones (simu per week) :" in message
    assert "100,000 €:" in message
    assert "500,000 €:" in message