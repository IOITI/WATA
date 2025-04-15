from datetime import datetime, timedelta

import duckdb
import os

from src.configuration import ConfigurationManager
import logging


class TradingDataDB:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.db_file_path = self.config_manager.get_config_value("duckdb.persistant.db_path")
        self.db_dir = os.path.dirname(self.db_file_path)
        if self.is_database_corrupted():
            logging.critical(
                f"Database is corrupted, cannot initialize. Please look at {self.db_dir}/database_corrupted.txt"
            )
            raise ValueError(
                f"Database is corrupted, cannot initialize. Please look at {self.db_dir}/database_corrupted.txt"
            )
        self.conn = duckdb.connect(
            self.db_file_path
        )
        self.configure_db()

    def configure_db(self):
        """
        Configures the database settings.
        """
        self.conn.execute("SET memory_limit = '1GB';")
        self.conn.execute("SET threads TO 2;")
        self.conn.execute("SET enable_progress_bar = true;")
        self.conn.execute("SET default_null_order = 'nulls_last';")

    # Function to check if the database is corrupted
    def is_database_corrupted(self):
        return os.path.exists(
            f"{self.db_dir}/database_corrupted.txt"
        )

    # Function to set the database as corrupted
    def mark_database_as_corrupted(self, why):
        with open(
                f"{self.db_dir}/database_corrupted.txt",
                "w",
        ) as file:
            file.write(f"Database is corrupted because {why}")


class DbOrderManager(TradingDataDB):
    def __init__(self, config_manager):
        super().__init__(config_manager)
        self.create_schema_turbo_data_order()

    def create_schema_turbo_data_order(self):
        """
        Creates the table schema if it doesn't exist.
        """
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turbo_data_order (
                action VARCHAR,
                buy_sell VARCHAR,
                order_id VARCHAR PRIMARY KEY,
                order_amount INTEGER,
                order_type VARCHAR,
                order_kind VARCHAR,
                order_time TIMESTAMP,
                related_order_id VARCHAR[],
                position_id VARCHAR,
                instrument_name VARCHAR,
                instrument_symbol VARCHAR,
                instrument_uic INTEGER,
                instrument_price FLOAT,
                instrument_currency VARCHAR,
                order_cost FLOAT
            )
            """
        )

    def insert_turbo_order_data(self, data):
        """
        Inserts turbo data into the database.
        """
        # Serialize related_order_id as a JSON string
        # related_order_id_json = json.dumps(data["related_order_id"])

        self.conn.execute(
            """
            INSERT INTO turbo_data_order (action, buy_sell, order_id, order_amount, order_type, order_kind, order_time, related_order_id, position_id, instrument_name, instrument_symbol, instrument_uic, instrument_price, instrument_currency, order_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["action"],
                data["buy_sell"],
                data["order_id"],
                data["order_amount"],
                data["order_type"],
                data["order_kind"],
                data["order_submit_time"],
                data["related_order_id"],
                # related_order_id_json,
                data["position_id"],
                data["instrument_name"],
                data["instrument_symbol"],
                data["instrument_uic"],
                data["instrument_price"],
                data["instrument_currency"],
                data["order_cost"],
            ),
        )


class DbPositionManager(TradingDataDB):
    def __init__(self, config_manager):
        super().__init__(config_manager)
        self.create_schema_turbo_data_position()

    def create_schema_turbo_data_position(self):
        """
        Creates the table schema for turbo_data_position if it doesn't exist.
        """
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turbo_data_position (
                action VARCHAR,
                position_id VARCHAR PRIMARY KEY,
                position_amount INTEGER,
                position_open_price FLOAT,
                position_close_price FLOAT,
                position_close_reason VARCHAR,
                position_profit_loss FLOAT,
                position_total_open_price FLOAT,
                position_total_close_price FLOAT,
                position_total_performance_percent FLOAT,
                position_max_performance_percent FLOAT,
                position_status VARCHAR,
                position_kind VARCHAR,
                execution_time_open TIMESTAMP,
                execution_time_close TIMESTAMP,
                order_id VARCHAR,
                related_order_id VARCHAR[],
                instrument_name VARCHAR,
                instrument_symbol VARCHAR,
                instrument_uic INTEGER,
                instrument_currency VARCHAR
            )
            """
        )

    def insert_turbo_open_position_data(self, data):
        """
        Inserts turbo position data into the database.
        """
        self.conn.execute(
            """
            INSERT INTO turbo_data_position (action, position_id, position_amount, position_open_price, position_total_open_price, position_status, position_kind, execution_time_open, order_id, related_order_id, instrument_name, instrument_symbol, instrument_uic, instrument_currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["action"],
                data["position_id"],
                data["position_amount"],
                data["position_open_price"],
                data["position_total_open_price"],
                data["position_status"],
                data["position_kind"],
                data["execution_time_open"],
                data["order_id"],
                data["related_order_id"],
                data["instrument_name"],
                data["instrument_symbol"],
                data["instrument_uic"],
                data["instrument_currency"],
            ),
        )

    def update_turbo_position_data(self, position_id, update_data):
        """
        Updates turbo position data in the database.
        """
        # Construct the SET part of the UPDATE statement
        set_clause = ", ".join(f"{key} = ?" for key in update_data.keys())

        # Construct the WHERE clause
        where_clause = f"position_id = '{position_id}'"

        # Combine the SET and WHERE clauses
        update_query = (
            f"UPDATE turbo_data_position SET {set_clause} WHERE {where_clause}"
        )

        # Prepare the values to be updated
        values = tuple(update_data.values())

        # Execute the update query
        self.conn.execute(update_query, values)

    def check_position_ids_exist(self, position_ids):
        """
        Checks if a list of position_ids exist in the database and returns detailed information.

        Args:
            position_ids (list): A list of position_ids to check.

        Returns:
            dict: A dictionary with two keys: 'position_ids_in_db' and 'position_ids_not_found'.
        """
        # Initialize the result dictionary
        result = {"position_ids_in_db": [], "position_ids_not_found": []}

        # Query the database for each position_id
        for position_id in position_ids:
            # Check if the position_id exists in the database and fetch additional information
            position_info = self.conn.execute(
                """
                SELECT position_id, order_id, action
                FROM turbo_data_position
                WHERE position_id = ?
                """,
                (position_id,),
            ).fetchone()

            if position_info:
                # If the position_id exists, add it to the 'position_ids_in_db' list with additional information
                result["position_ids_in_db"].append(
                    {
                        "position_id": position_info[0],
                        "order_id": position_info[1],
                        "action": position_info[2],
                    }
                )
            else:
                # If the position_id does not exist, add it to the 'position_ids_not_found' list
                result["position_ids_not_found"].append(position_id)

        return result

    def get_open_positions_ids(self):
        """
        Retrieves a list of all position_id's with a position_status of "Open".

        Returns:
            list: A list of position_id's that have a position_status of "Open".
        """
        # Execute the SQL query to select all position_id's with a position_status of "Open"
        open_positions = self.conn.execute(
            """
            SELECT position_id
            FROM turbo_data_position
            WHERE position_status = 'Open'
            """
        ).fetchall()

        # Extract the position_id's from the result and return them as a list
        return [position[0] for position in open_positions]

    def get_open_positions_ids_actions(self):
        # Execute the SQL query to select all position_id's with a position_status of "Open"
        open_positions = self.conn.execute(
            """
            SELECT position_id, action
            FROM turbo_data_position
            WHERE position_status = 'Open'
            """
        ).fetchall()

        result_list = []

        for position in open_positions:
            result_schema = {
                "position_id": position[0],
                "action": position[1]
            }
            result_list.append(result_schema)
        return result_list

    def get_max_position_percent(self, position_id):
        """
        Retrieves the maximum position percentage for a position.
        """
        # Execute the SQL query to select all position_id's with a position_status of "Open"
        resp_max_position_percent = self.conn.execute(
            """
            SELECT position_max_performance_percent
            FROM turbo_data_position
            WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()

        # Check if the result is None, which means no rows matched
        if resp_max_position_percent is None or resp_max_position_percent[0] is None:
            max_position_percent = 0.0
        else:
            max_position_percent = resp_max_position_percent[0]

        return max_position_percent

    def get_stats_of_the_day(self):
        """
        Returns statistics for the current day in the desired JSON format.

        [{"day_date":"2024/05/17","position_count":40,"avg_percent":-0.07549999048933387,"max_percent":6.59,"min_percent":-1.84,"sum_profit":-0.16700000083073974}]
        """

        # Format the date as 'YYYY/MM/DD'
        formatted_date = datetime.now().strftime('%Y/%m/%d')

        # Execute the SQL query
        general_stats = self.conn.execute(
            """
            SELECT strftime(execution_time_close, '%Y/%m/%d') AS day_date, COUNT(*) AS position_count, AVG(position_total_performance_percent) AS avg_percent, MAX(position_total_performance_percent) AS max_percent, MIN(position_total_performance_percent) AS min_percent, SUM(position_profit_loss) AS sum_profit
            FROM turbo_data_position
            WHERE position_status = 'Closed' AND day_date = ?
            GROUP BY day_date,
            ORDER BY day_date DESC;
            """,
            (formatted_date,),
        ).fetchall()

        # Convert each row to a dictionary and append to a list
        stats_list = []
        for row in general_stats:
            stats_dict = {
                "day_date": row[0],
                "position_count": row[1],
                "avg_percent": row[2],
                "max_percent": row[3],
                "min_percent": row[4],
                "sum_profit": row[5]
            }
            stats_list.append(stats_dict)

        # Execute the SQL query
        detail_stats = self.conn.execute(
            """
            SELECT strftime(execution_time_close, '%Y/%m/%d') AS day_date, action, COUNT(*) AS position_count, AVG(position_total_performance_percent) AS avg_percent, MAX(position_total_performance_percent) AS max_percent, MIN(position_total_performance_percent) AS min_percent, SUM(position_profit_loss) AS sum_profit
            FROM turbo_data_position
            WHERE position_status = 'Closed' AND day_date = ?
            GROUP BY day_date, action,
            ORDER BY day_date DESC, action ASC;
            """,
            (formatted_date,),
        ).fetchall()

        # Convert each row to a dictionary and append to a list
        detail_stats_list = []
        for row in detail_stats:
            detail_stats_dict = {
                "day_date": row[0],
                "action": row[1],
                "position_count": row[2],
                "avg_percent": row[3],
                "max_percent": row[4],
                "min_percent": row[5],

            }
            detail_stats_list.append(detail_stats_dict)

        final = {"general": stats_list, "detail_stats": detail_stats_list}
        return final

    def _apply_percentage_change(self, value, p_percentage):
        """
        Apply a percentage change to a given value.

        :param value: Initial value
        :param p_percentage: Percentage change to apply
        :return: New value after applying the percentage change
        """
        return value * (1 + p_percentage / 100)

    def _fetch_percentage_data(self, date, column_name):
        """
        Fetch percentage data for a given date from the database.

        :param date: The date to query data for.
        :param column_name: The column to select for percentage changes.
        :return: A list of percentage changes.
        """
        query = f"""
        SELECT {column_name}
        FROM turbo_data_position
        WHERE position_status = 'Closed' AND strftime(execution_time_close, '%Y/%m/%d') = ?
        """
        return self.conn.execute(query, (date,)).fetchall()

    def _calculate_final_percentage(self, percent_list):
        """
        Calculate the final percentage by applying percentage changes sequentially.

        :param percent_list: A list of percentage changes.
        :return: The final percentage change, rounded to two decimal places.
        """
        initial_value = 1.00
        for percentage_tuple in percent_list:
            for percentage in percentage_tuple:
                # If percentage is None, treat it as 0
                percentage = 0 if percentage is None else percentage
                initial_value = self._apply_percentage_change(initial_value, percentage)
        return round((initial_value - 1) * 100, 2)

    def _calculate_best_percentage(self, percent_list):
        """
        Calculate the maximum percentage by applying percentage changes and storing intermediates.

        :param percent_list: A list of percentage changes.
        :return: The maximum percentage change, rounded to two decimal places.
        """
        initial_value = 1.00
        intermediate_values = []
        for percentage_tuple in percent_list:
            for percentage in percentage_tuple:
                # If percentage is None, treat it as 0
                percentage = 0 if percentage is None else percentage
                initial_value = self._apply_percentage_change(initial_value, percentage)
                intermediate_values.append(initial_value)

        return round((max(intermediate_values) - 1) * 100, 2) if intermediate_values else 0.0

    def _get_percentages_for_n_days(self, n, column_name, calculation_function):
        """
        Generic method to get percentage changes over the last N days using a specified calculation function.

        :param n: Number of days.
        :param column_name: The column to query for percentage data.
        :param calculation_function: The function to apply for calculating percentage (final/best).
        :return: A dictionary of dates and their percentage changes.
        """
        results = {}
        for i in range(n):
            current_date = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
            percent_list = self._fetch_percentage_data(current_date, column_name)

            if percent_list:
                results[current_date] = calculation_function(percent_list)
            else:
                results[current_date] = 0.0

        return results

    def get_percent_of_the_day(self):
        """
        Returns the percentage change of the current day's positions.

        :returns:
        float: The percentage change of the current day's positions as a number rounded to two decimal places.
        """
        formatted_date = datetime.now().strftime('%Y/%m/%d')
        percent_list = self._fetch_percentage_data(formatted_date, 'position_total_performance_percent')

        # Use the same final percentage calculation method
        return self._calculate_final_percentage(percent_list)

    def get_percent_of_last_n_days(self, n):
        """
        Returns the percentage change of the day's positions for the last N days.

        :param n: Number of days to calculate.
        :return: Dictionary of dates and percentage changes.
        """
        return self._get_percentages_for_n_days(n, 'position_total_performance_percent',
                                                self._calculate_final_percentage)

    def get_best_percent_of_last_n_days(self, n):
        """
        Returns the maximum percentage change of the day's positions for the last N days.

        :param n: Number of days to calculate.
        :return: Dictionary of dates and maximum percentage changes.
        """
        return self._get_percentages_for_n_days(n, 'position_total_performance_percent',
                                                self._calculate_best_percentage)

    def get_theoretical_percent_of_last_n_days_on_max(self, n):
        """
        Returns the theoretical percentage change based on the max performance for the last N days.

        :param n: Number of days to calculate.
        :return: Dictionary of dates and percentage changes based on max performance.
        """
        return self._get_percentages_for_n_days(n, 'position_max_performance_percent',
                                                self._calculate_final_percentage)

    def get_best_theoretical_percent_of_last_n_days_on_max(self, n):
        """
        Returns the maximum theoretical percentage change based on the max performance for the last N days.

        :param n: Number of days to calculate.
        :return: Dictionary of dates and maximum theoretical percentage changes.
        """
        return self._get_percentages_for_n_days(n, 'position_max_performance_percent',
                                                self._calculate_best_percentage)


class DbTradePerformanceManager(TradingDataDB):
    def __init__(self, config_manager):
        super().__init__(config_manager)
        self.create_schema_trade_performance()
        if self.check_trade_performance_table_empty():
            self.create_old_trade_performance_data()

    def create_schema_trade_performance(self):
        """
        Creates the table schema for trade_performance if it doesn't exist.
        """
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_performance (
                date_day TIMESTAMP PRIMARY KEY,
                perf_day_real FLOAT,
                money_made_real FLOAT,
                trade_number_real INTEGER,
                max_perf_day_simulated FLOAT
            )
            """
        )

    def check_trade_performance_table_empty(self):
        """
        Check if trade_performance table is empty in the database.
        """
        query = f"SELECT COUNT(*) FROM trade_performance"
        result = self.conn.execute(query).fetchone()
        return result[0] == 0

    def create_old_trade_performance_data(self):
        """
        Populate trade_performance data based on existing data in the turbo_data_position table.
        """
        # Determine the number of days of data we have in the turbo_data_position table
        query = """
        SELECT MIN(CAST(execution_time_close AS DATE)), MAX(CAST(execution_time_close AS DATE))
        FROM turbo_data_position
        WHERE position_status = 'Closed'
        """
        min_date, max_date = self.conn.execute(query).fetchone()
        if min_date and max_date:
            # Calculate the difference in days between min_date and max_date
            n_days = (max_date - min_date).days

            # Generate trade performance data for each day
            trade_performance_data = self._get_trade_performance_for_n_days(n_days)

            for date, stats in trade_performance_data.items():
                # Insert calculated data into trade_performance table
                self.insert_trade_performance_data({
                    "date_day": date,
                    "perf_day_real": stats["perf_day_real"],
                    "money_made_real": stats["money_made_real"],
                    "trade_number_real": stats["trade_number_real"],
                    "max_perf_day_simulated": stats["max_perf_day_simulated"]
                })

    def create_last_day_trade_performance_data(self):
        """
        Populate trade_performance data based on last day in the turbo_data_position table.
        """
        trade_performance_data = self._get_trade_performance_for_n_days(1)
        for date, stats in trade_performance_data.items():
            # Insert calculated data into trade_performance table
            self.insert_trade_performance_data({
                "date_day": date,
                "perf_day_real": stats["perf_day_real"],
                "money_made_real": stats["money_made_real"],
                "trade_number_real": stats["trade_number_real"],
                "max_perf_day_simulated": stats["max_perf_day_simulated"]
            })


    def _fetch_percentage_data(self, date, column_name):
        """
        Fetch percentage data for a given date from the database.

        :param date: The date to query data for.
        :param column_name: The column to select for percentage changes.
        :return: A list of percentage changes.
        """
        query = f"""
        SELECT {column_name}
        FROM turbo_data_position
        WHERE position_status = 'Closed' AND strftime(execution_time_close, '%Y/%m/%d') = ?
        """
        return self.conn.execute(query, (date,)).fetchall()

    def _calculate_final_percentage(self, percent_list):
        """
        Calculate the final percentage by applying percentage changes sequentially.

        :param percent_list: A list of percentage changes.
        :return: The final percentage change, rounded to two decimal places.
        """
        initial_value = 1.00
        for percentage_tuple in percent_list:
            for percentage in percentage_tuple:
                # If percentage is None, treat it as 0
                percentage = 0 if percentage is None else percentage
                initial_value = self._apply_percentage_change(initial_value, percentage)
        return round((initial_value - 1) * 100, 2)

    def _apply_percentage_change(self, value, p_percentage):
        """
        Apply a percentage change to a given value.

        :param value: Initial value
        :param p_percentage: Percentage change to apply
        :return: New value after applying the percentage change
        """
        return value * (1 + p_percentage / 100)

    def _get_trade_performance_for_n_days(self, n):
        """
        Calculate trading performance over the last N days.

        :param n: Number of days.
        :return: A dictionary with dates and their statistics.
        """
        results = {}
        for i in range(n + 1):
            current_date = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
            percent_list = self._fetch_percentage_data(current_date, 'position_total_performance_percent')

            if percent_list:
                perf_day_real = self._calculate_final_percentage(percent_list)
            else:
                perf_day_real = 0.0
            stats = self._fetch_daily_trade_data(current_date)

            if stats:
                results[current_date] = {
                    "perf_day_real": perf_day_real,
                    "money_made_real": stats["money_made_real"],
                    "trade_number_real": stats["trade_number_real"],
                    "max_perf_day_simulated": None  # Placeholder for future implementation
                }
            else:
                results[current_date] = {
                    "perf_day_real": 0.0,
                    "money_made_real": 0.0,
                    "trade_number_real": 0,
                    "max_perf_day_simulated": None
                }
        return results

    def _fetch_daily_trade_data(self, date):
        """
        Fetch daily trade data for a given date from the database.

        :param date: The date to query data for.
        :return: A dictionary with performance stats.
        """
        query = """
        SELECT 
            COALESCE(SUM(position_profit_loss), 0.0) AS money_made_real,
            COUNT(position_id) AS trade_number_real
        FROM turbo_data_position
        WHERE position_status = 'Closed' AND strftime('%Y/%m/%d', execution_time_close) = ?
        """
        result = self.conn.execute(query, (date,)).fetchone()
        if result:
            return {
                "money_made_real": result[0],
                "trade_number_real": result[1]
            }
        return None

    def insert_trade_performance_data(self, data):
        """
        Inserts trade performance data into the database.
        """
        self.conn.execute(
            """
            INSERT INTO trade_performance (date_day, perf_day_real, money_made_real, trade_number_real, max_perf_day_simulated)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING;
            """,
            (
                data["date_day"],
                data["perf_day_real"],
                data["money_made_real"],
                data["trade_number_real"],
                data["max_perf_day_simulated"],
            ),
        )


class DbTokenManager(TradingDataDB):
    """
    Manages encrypted token storage in DuckDB.
    Provides secure storage for authentication tokens.
    """
    def __init__(self, config_manager):
        super().__init__(config_manager)
        self.create_schema_auth_tokens()

    def create_schema_auth_tokens(self):
        """
        Creates the table schema for storing encrypted auth tokens.
        """
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_id VARCHAR PRIMARY KEY,
                token_type VARCHAR,
                encrypted_data BLOB,
                creation_time TIMESTAMP,
                last_update TIMESTAMP,
                metadata VARCHAR
            )
            """
        )

    def store_token(self, token_id, token_type, encrypted_data, metadata=None):
        """
        Stores an encrypted token in the database.
        
        Args:
            token_id (str): Unique identifier for the token (e.g., 'saxo_token')
            token_type (str): Type of token (e.g., 'oauth', 'api_key')
            encrypted_data (bytes): Encrypted token data
            metadata (str, optional): Additional metadata as JSON string
        """
        now = datetime.now()
        self.conn.execute(
            """
            INSERT INTO auth_tokens (token_id, token_type, encrypted_data, creation_time, last_update, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (token_id) DO UPDATE SET
                encrypted_data = excluded.encrypted_data,
                last_update = excluded.last_update,
                metadata = excluded.metadata
            """,
            (token_id, token_type, encrypted_data, now, now, metadata)
        )

    def get_token(self, token_id):
        """
        Retrieves an encrypted token from the database.
        
        Args:
            token_id (str): Unique identifier for the token
            
        Returns:
            bytes or None: Encrypted token data if found, None otherwise
        """
        result = self.conn.execute(
            """
            SELECT encrypted_data
            FROM auth_tokens
            WHERE token_id = ?
            """,
            (token_id,)
        ).fetchone()
        
        return result[0] if result else None

    def token_exists(self, token_id):
        """
        Checks if a token exists in the database.
        
        Args:
            token_id (str): Unique identifier for the token
            
        Returns:
            bool: True if token exists, False otherwise
        """
        result = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM auth_tokens
            WHERE token_id = ?
            """,
            (token_id,)
        ).fetchone()
        
        return result[0] > 0

    def delete_token(self, token_id):
        """
        Deletes a token from the database.
        
        Args:
            token_id (str): Unique identifier for the token
        """
        self.conn.execute(
            """
            DELETE FROM auth_tokens
            WHERE token_id = ?
            """,
            (token_id,)
        )

    def update_metadata(self, token_id, metadata):
        """
        Updates the metadata for a token.
        
        Args:
            token_id (str): Unique identifier for the token
            metadata (str): New metadata as JSON string
        """
        self.conn.execute(
            """
            UPDATE auth_tokens
            SET metadata = ?, last_update = ?
            WHERE token_id = ?
            """,
            (metadata, datetime.now(), token_id)
        )
