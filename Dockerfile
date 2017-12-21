FROM ubuntu:16.04

COPY ACCC4CF8.asc /usr/src/app/

RUN echo "deb http://apt.postgresql.org/pub/repos/apt/ xenial-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list && \
    apt-key add /usr/src/app/ACCC4CF8.asc && \
    apt-get update && \
    apt-get install -y \
        postgresql-client-9.5 \
        mysql-client \
        python-yaml \
        python-boto \
        && \
    rm -rf /var/lib/apt/lists/*

COPY dump.py /usr/src/app/

ENTRYPOINT [ "/usr/src/app/dump.py" ]
