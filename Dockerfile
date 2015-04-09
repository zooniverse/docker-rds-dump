FROM ubuntu:14.04

ENV DEBIAN_FRONTEND noninteractive

ADD requirements.txt /

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y wget mysql-client python-pip && \
    echo "deb http://apt.postgresql.org/pub/repos/apt/ trusty-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list && \
    wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | apt-key add - && \
    apt-get update && apt-get install -y postgresql-client-9.4 && \
    pip install -r requirements.txt

ADD dump.py /

ENTRYPOINT [ "/dump.py" ]
