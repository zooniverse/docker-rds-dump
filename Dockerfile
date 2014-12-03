FROM ubuntu:14.04

ENV DEBIAN_FRONTEND noninteractive

ADD requirements.txt /

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y postgresql-client mysql-client python-pip && \
    pip install -r requirements.txt

ADD dump.py /

ENTRYPOINT [ "/dump.py" ]
