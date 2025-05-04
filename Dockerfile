# Use an official Python runtime as a parent image
FROM python:3.12

# Set the working directory in the container to /app
WORKDIR /app

# Updating apt
RUN apt-get -y update

# Install Unzip
RUN apt-get install -y unzip

# Copy the current directory contents into the container at /app
COPY ./src /app/src

COPY ./requirements.txt /app/requirements.txt

COPY ./VERSION /app/VERSION

ENV PYTHONPATH "${PYTHONPATH}:/app"
ENV WATA_CONFIG_PATH="/app/wata/etc/config.json"

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN chmod +x /app/src/start_python_script.sh

CMD ["./src/start_python_script.sh"]
