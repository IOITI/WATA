# The Python the services run on: what the server builds, which asks for
# nothing else. CI also builds the image with another one (--build-arg
# PYTHON_VERSION=...): the next, so that it is known to work before it becomes
# this one, or the one before, for as long as going back to it must stay possible.
ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}

# Set the working directory in the container to /app
WORKDIR /app

# Updating apt
RUN apt-get -y update

# Install Unzip
RUN apt-get install -y unzip

# The libraries before the code: this layer is rebuilt only when
# requirements.txt changes, so a deploy that only changes the code does not
# download them again while the application is stopped. Ready-made builds
# only: a library that would have to be compiled here fails at once instead.
COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt

# Copy the current directory contents into the container at /app
COPY ./src /app/src

COPY ./VERSION /app/VERSION

ENV PYTHONPATH "${PYTHONPATH}:/app"
ENV WATA_CONFIG_PATH="/app/etc/config.json"

RUN chmod +x /app/src/start_python_script.sh

CMD ["./src/start_python_script.sh"]
