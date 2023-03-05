FROM ubuntu:rolling

RUN apt-get update && apt-get install -y dumb-init python3
RUN python3 -V
RUN ln -s $(python3 -c "import sys; print(sys.executable)") /usr/local/bin/python

COPY . .