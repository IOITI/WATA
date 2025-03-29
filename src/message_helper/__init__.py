import logging


def append_performance_message(p_message, title, percentages):
    """Helper function to append performance data to the message."""
    p_message += f"\n--- {title} ---\n"
    for day, percentage in percentages.items():
        # Handle cases where percentage is None
        percentage_value = percentage if percentage is not None else "N/A"
        p_message += f"{day}: {percentage_value}%\n"
    return p_message


def format_general_stats(general_stats):
    """Formats the general stats section of the message."""
    if not general_stats:
        return "No daily stats available for today"

    general_message = ""
    for general in general_stats:
        # Handle NoneType values in the stats (default to "N/A" or 0 as needed)
        general_message +=  f"""
--- Stats of the day {general.get("day_date", "N/A")} ---

Position count : {general.get("position_count", "N/A")}
Average %: {general.get("avg_percent", "N/A")}
Max %: {general.get("max_percent", "N/A")}
Min %: {general.get("min_percent", "N/A")}
Sum profit : {general.get("sum_profit", "N/A")} €
"""
    return general_message


def format_detail_stats(detail_stats):
    """Formats the detailed stats section of the message."""
    if not detail_stats:
        return ""

    details_message = "--- Detail stats ---\n"
    for detail in detail_stats:
        # Handle NoneType values in the detail stats
        details_message += f"""
Type : {detail.get("action", "N/A")}
Position count : {detail.get("position_count", "N/A")}
Average %: {detail.get("avg_percent", "N/A")}
Max %: {detail.get("max_percent", "N/A")}
Min %: {detail.get("min_percent", "N/A")}
-------
"""
    return details_message


def generate_daily_stats_message(stats_of_the_day):
    """Generates the daily stats part of the message."""
    # Safely access 'general' and 'detail_stats'
    general_stats = stats_of_the_day.get("general", [])
    detail_stats = stats_of_the_day.get("detail_stats", [])

    general_stats_message = format_general_stats(general_stats)
    detail_stats_message = format_detail_stats(detail_stats)

    return general_stats_message + detail_stats_message


def generate_performance_stats_message(message, days, last_days_percentages, last_best_days_percentages,
                                       last_days_percentages_on_max, last_best_days_percentages_on_max):
    """Appends performance statistics for the last 'days' to the message."""
    message = append_performance_message(message, f"Last {days} Days Performance real", last_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance best", last_best_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance, on max", last_days_percentages_on_max)
    message = append_performance_message(message, f"Last {days} Days Performance, best on max", last_best_days_percentages_on_max)

    return message