import duckdb

# Connect to the DuckDB database
conn = duckdb.connect("trading_data.duckdb")


def execute_query(query):
    """
    Executes a SQL query against the DuckDB database and prints the results.
    """
    try:
        # Execute the query
        result = conn.execute(query)

        # Fetch all rows from the result
        rows = result.fetchall()

        # Print the rows
        for row in rows:
            print(row)
    except Exception as e:
        print(f"Error executing query: {e}")


def main():
    """
    Main function to interact with the user.
    """
    print("Welcome to DuckDB SQL Interface!")
    print("Type your SQL query or 'exit()' to quit.")

    while True:
        try:
            # Prompt the user for a query
            query = input("SQL> ")

            # Exit condition
            if query.lower() == "exit()":
                break

            # Execute the query
            execute_query(query)
        except KeyboardInterrupt:
            print("\nExiting...")
            break


if __name__ == "__main__":
    main()
