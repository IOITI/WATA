import json
import sys
from datetime import datetime, timedelta
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa

# Check if output path was provided as command-line argument
if len(sys.argv) > 1:
    output_path = sys.argv[1]
else:
    output_path = '.'  # Default to current directory

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
                "rule_name": "profit_per_days",
                "rule_type": "profit_per_days",
                "rule_config": {
                    "percent_profit_wanted_per_days": 1.7
                }
            }
        ]
    },
    "reporting": {
        "money_expectation_indicator": {
            "trading_start_date": "30/03/2025",
            "initial_money": 200.00,
            "weekday_without_trading": ["saturday", "sunday"],
            "trading_date_to_generate": 1000
        }
    }
}

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

# Loop through the specified number of trading dates
generated_days = 0
while generated_days < days_to_generate:
    if (current_date.weekday() not in excluded_weekdays) and (current_date not in market_closed_dates):
        # Calculate new money amount for this trading day
        current_money *= (1 + percent_profit_per_day)
        trading_data.append({"date": current_date, "money": current_money - initial_money})
        generated_days += 1  # Only count valid trading days
    # Move to the next day
    current_date += timedelta(days=1)

# Create a DataFrame and save as Parquet file
df = pd.DataFrame(trading_data)
table = pa.Table.from_pandas(df)

# Save to specified output path
output_file = f"{output_path}/trading_simulation_data.parquet"
pq.write_table(table, output_file)

print(f"Data saved to {output_file}")