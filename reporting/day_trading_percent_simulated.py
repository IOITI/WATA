import sys
from datetime import datetime, timedelta
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa

# Default config data
default_config_data = {
    "trade": {
        "rules": [
            {
                "rule_name": "market_closed_dates",
                "rule_type": "market_closed_dates",
                "rule_config": {
                    "market_closed_dates": [
                        "04/07/2024", "02/09/2024", "28/11/2024", "25/12/2024",
                        "03/07/2024", "29/11/2024", "24/12/2024", "01/01/2025", "09/01/2025",
                        "20/01/2025", "17/02/2025", "18/04/2025", "26/05/2025",
                        "19/06/2025", "04/07/2025", "01/09/2025", "27/11/2025",
                        "25/12/2025", "03/07/2025", "28/11/2025", "24/12/2025",
                        "01/01/2026"
                    ]
                }
            },
            {
                "rule_name": "day_trading",
                "rule_type": "day_trading",
                "rule_config": {
                    "percent_profit_wanted_per_days": 1.2
                }
            }
        ]
    },
    "reporting": {
        "money_expectation_indicator": {
            "trading_start_date": "25/05/2026",
            "initial_money": 1000.00,
            "weekday_without_trading": ["saturday", "sunday"],
            "trading_date_to_generate": 1000
        }
    }
}

def format_currency(amount):
    return f"${amount:,.2f}"


def format_milestone(target, milestone_info):
    status = "Reached" if milestone_info["within_simulation"] else "Projected"
    extra_days_text = ""
    if milestone_info["extra_trading_days"] > 0:
        extra_days_text = f", +{milestone_info['extra_trading_days']} more trading days"

    return (
        f"{status} {format_currency(target)} on {milestone_info['date']:%Y-%m-%d} "
        f"(trading day {milestone_info['trading_day']:,}, "
        f"{milestone_info['calendar_days']:,} calendar days from start{extra_days_text})"
    )


def format_growth_event(label, milestone_info):
    status = "Reached" if milestone_info["within_simulation"] else "Projected"
    extra_days_text = ""
    if milestone_info["extra_trading_days"] > 0:
        extra_days_text = f", +{milestone_info['extra_trading_days']} more trading days"

    return (
        f"{status} {label} on {milestone_info['date']:%Y-%m-%d} "
        f"(trading day {milestone_info['trading_day']:,}, "
        f"{milestone_info['calendar_days']:,} calendar days from start{extra_days_text})"
    )


def find_milestone(df, target_balance, start_date, percent_profit_per_day, excluded_weekdays, market_closed_dates):
    milestone_rows = df[df["balance"] >= target_balance]
    if not milestone_rows.empty:
        milestone_row = milestone_rows.iloc[0]
        milestone_date = milestone_row["date"].to_pydatetime()
        return {
            "date": milestone_date,
            "trading_day": int(milestone_row["trading_day"]),
            "calendar_days": (milestone_date - start_date).days,
            "within_simulation": True,
            "extra_trading_days": 0,
        }

    projected_date = df.iloc[-1]["date"].to_pydatetime()
    projected_balance = float(df.iloc[-1]["balance"])
    projected_trading_day = int(df.iloc[-1]["trading_day"])

    while projected_balance < target_balance:
        projected_date += timedelta(days=1)
        if projected_date.weekday() in excluded_weekdays or projected_date in market_closed_dates:
            continue

        projected_balance *= (1 + percent_profit_per_day)
        projected_trading_day += 1

    return {
        "date": projected_date,
        "trading_day": projected_trading_day,
        "calendar_days": (projected_date - start_date).days,
        "within_simulation": False,
        "extra_trading_days": projected_trading_day - len(df),
    }


def calculate_average_period_pl_percent(df, frequency):
    period_returns = (
        df.assign(
            opening_balance=df["balance"] - df["daily_gain"],
            period=df["date"].dt.to_period(frequency),
        )
        .groupby("period")
        .agg(period_open=("opening_balance", "first"), period_close=("balance", "last"))
    )
    period_returns["return_percent"] = (
        (period_returns["period_close"] - period_returns["period_open"])
        / period_returns["period_open"]
        * 100
    )
    return float(period_returns["return_percent"].mean())


def print_money_lover_stats(df, start_date, initial_money, percent_profit_per_day, excluded_weekdays, market_closed_dates):
    final_row = df.iloc[-1]
    final_balance = float(final_row["balance"])
    total_profit = float(final_row["profit"])
    last_daily_gain = float(final_row["daily_gain"])
    total_return_percent = (total_profit / initial_money) * 100
    multiplier = final_balance / initial_money

    doubling_info = find_milestone(
        df,
        initial_money * 2,
        start_date,
        percent_profit_per_day,
        excluded_weekdays,
        market_closed_dates,
    )

    ten_bagger_info = find_milestone(
        df,
        initial_money * 10,
        start_date,
        percent_profit_per_day,
        excluded_weekdays,
        market_closed_dates,
    )

    single_day_initial_break = df[df["daily_gain"] >= initial_money]
    single_day_initial_break_text = "Not reached during simulation."
    if not single_day_initial_break.empty:
        first_break_row = single_day_initial_break.iloc[0]
        first_break_date = first_break_row["date"].to_pydatetime()
        single_day_initial_break_text = (
            f"One trading day profit first beat the starting bankroll on {first_break_date:%Y-%m-%d} "
            f"(trading day {int(first_break_row['trading_day']):,})"
        )

    monthly_profit = (
        df.assign(month=df["date"].dt.to_period("M"))
        .groupby("month")["daily_gain"]
        .sum()
    )
    average_weekly_pl_percent = calculate_average_period_pl_percent(df, "W-FRI")
    average_monthly_pl_percent = calculate_average_period_pl_percent(df, "M")
    average_yearly_pl_percent = calculate_average_period_pl_percent(df, "Y")
    best_month = monthly_profit.idxmax()
    best_month_profit = float(monthly_profit.loc[best_month])

    milestone_targets = [100000, 500000, 1000000]
    milestone_summaries = []
    for target in milestone_targets:
        milestone_info = find_milestone(
            df,
            target,
            start_date,
            percent_profit_per_day,
            excluded_weekdays,
            market_closed_dates,
        )
        milestone_summaries.append(format_milestone(target, milestone_info))

    print("\nMoney lover stats:")
    print(f"Simulation window: {start_date:%Y-%m-%d} -> {final_row['date']:%Y-%m-%d}")
    print(f"Trading days generated: {len(df):,}")
    print(f"Final bankroll: {format_currency(final_balance)}")
    print(f"Net profit: {format_currency(total_profit)} ({total_return_percent:,.2f}% total return)")
    print(f"Bankroll multiplier: {multiplier:,.2f}x")
    print(format_growth_event("money doubled", doubling_info))
    print(format_growth_event("10x bankroll", ten_bagger_info))
    print(single_day_initial_break_text)
    print(f"Average gain per trading day: {format_currency(df['daily_gain'].mean())}")
    print(f"Average P/L per week: {average_weekly_pl_percent:,.2f}%")
    print(f"Average P/L per month: {average_monthly_pl_percent:,.2f}%")
    print(f"Average P/L per year: {average_yearly_pl_percent:,.2f}%")
    print(f"Last simulated trading day gain: {format_currency(last_daily_gain)}")
    print(f"Best month: {best_month} with {format_currency(best_month_profit)} in gains")
    print("Big milestones:")
    for summary in milestone_summaries:
        print(f"- {summary}")

# Check if output path was provided as command-line argument
if len(sys.argv) > 1:
    output_path = sys.argv[1]
else:
    output_path = '.'  # Default to current directory

# Ask the user if they want to use the default configuration
print("Current configuration:")
print(f"Trading start date: {default_config_data['reporting']['money_expectation_indicator']['trading_start_date']}")
print(f"Initial money: ${default_config_data['reporting']['money_expectation_indicator']['initial_money']}")
print(f"Profit percent per day: {default_config_data['trade']['rules'][1]['rule_config']['percent_profit_wanted_per_days']}%")
print(f"Trading dates to generate: {default_config_data['reporting']['money_expectation_indicator']['trading_date_to_generate']}")
print(f"Weekend days without trading: {', '.join(default_config_data['reporting']['money_expectation_indicator']['weekday_without_trading'])}")
print(f"Number of market closed dates: {len(default_config_data['trade']['rules'][0]['rule_config']['market_closed_dates'])}")

user_response = input("Do you want to use these configuration values? (yes/no): ").strip().lower()

if user_response == "yes":
    config_data = default_config_data
    print("Using provided configuration values.")
else:
    print("Using default configuration values.")
    config_data = default_config_data

# Extract necessary values from the config
start_date = datetime.strptime(config_data["reporting"]["money_expectation_indicator"]["trading_start_date"], "%d/%m/%Y")
initial_money = config_data["reporting"]["money_expectation_indicator"]["initial_money"]
percent_profit_per_day = config_data["trade"]["rules"][1]["rule_config"]["percent_profit_wanted_per_days"] / 100
days_to_generate = config_data["reporting"]["money_expectation_indicator"]["trading_date_to_generate"]
weekend_days = config_data["reporting"]["money_expectation_indicator"]["weekday_without_trading"]

# Convert market closed dates into datetime objects
market_closed_dates = {
    datetime.strptime(date, "%d/%m/%Y") for date in config_data["trade"]["rules"][0]["rule_config"]["market_closed_dates"]
}

# Map weekend days to integers (e.g., Monday=0, Sunday=6)
weekend_days_map = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6
}
excluded_weekdays = {weekend_days_map[day.lower()] for day in weekend_days}

# Generate trading data
trading_data = []
current_date = start_date
current_money = initial_money
previous_money = initial_money

# Loop through the specified number of trading dates
generated_days = 0
while generated_days < days_to_generate:
    if (current_date.weekday() not in excluded_weekdays) and (current_date not in market_closed_dates):
        # Calculate new money amount for this trading day
        current_money *= (1 + percent_profit_per_day)
        daily_gain = current_money - previous_money
        trading_data.append(
            {
                "date": current_date,
                "trading_day": generated_days + 1,
                "balance": current_money,
                "profit": current_money - initial_money,
                "daily_gain": daily_gain,
                "money": current_money - initial_money,
            }
        )
        previous_money = current_money
        generated_days += 1  # Only count valid trading days
    # Move to the next day
    current_date += timedelta(days=1)

# Create a DataFrame and save as Parquet file
df = pd.DataFrame(trading_data)
if df.empty:
    print("No trading days were generated with the current configuration.")
    sys.exit(1)

print_money_lover_stats(
    df,
    start_date,
    initial_money,
    percent_profit_per_day,
    excluded_weekdays,
    market_closed_dates,
)

table = pa.Table.from_pandas(df)

# Save to specified output path
output_file = f"{output_path}/trading_simulation_data.parquet"
pq.write_table(table, output_file)

print(f"Data saved to {output_file}")