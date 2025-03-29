# Use an official Python runtime as a parent image
FROM python:3.12

# Set the working directory in the container to /app
WORKDIR /app

# Adding trusting keys to apt for repositories
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add -

# Adding Google Chrome to the repositories
RUN sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list'

# Updating apt to see and install Google Chrome
RUN apt-get -y update

# Magic happens
RUN apt-get install -y google-chrome-stable unzip

# install chromedriver
RUN wget -O /tmp/chromedriver.zip https://storage.googleapis.com/chrome-for-testing-public/`curl -sS https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE`/linux64/chromedriver-linux64.zip
RUN unzip /tmp/chromedriver.zip chromedriver-linux64/chromedriver -d /usr/local/src/

RUN rm -rf /tmp/chromedriver.zip

# Set display port as an environment variable
ENV DISPLAY=:99

# Copy the current directory contents into the container at /app
COPY ./src /app/src

COPY ./requirements.txt /app/requirements.txt

COPY ./VERSION /app/VERSION

ENV PYTHONPATH "${PYTHONPATH}:/app"
ENV WATA_CONFIG_PATH="/app/etc/config.json"

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN chmod +x /app/src/start_python_script.sh

CMD ["./src/start_python_script.sh"]
