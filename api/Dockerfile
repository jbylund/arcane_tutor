FROM ubuntu:latest

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    dumb-init \
    python3

RUN python3 -V
RUN ln -s $(python3 -c "import sys; print(sys.executable)") /usr/local/bin/python

ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV PIP_ROOT_USER_ACTION=ignore

RUN curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py && python get-pip.py && rm get-pip.py

RUN python -m pip install --upgrade pip 

RUN apt-get install -y libpython3-dev libev-dev libpq-dev

RUN python -m pip install --quiet --upgrade \
    bjoern \
    brotli \
    cachetools \
    falcon \
    honeybadger \
    orjson \
    psycopg[binary,pool] \
    pyparsing \
    requests \
    zstandard

COPY ./api /api
