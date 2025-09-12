FROM ubuntu:latest

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    dumb-init \
    python3 \
    python3-pip

RUN python3 -V
RUN ln -s $(python3 -c "import sys; print(sys.executable)") /usr/local/bin/python

# Install uv for faster package management
# Try the official installer first, fallback to pip if needed
RUN if curl -LsSf https://astral.sh/uv/install.sh | sh; then \
        echo "uv installed via official installer"; \
    else \
        echo "Installing uv via pip as fallback..." && \
        python -m pip install --upgrade pip && \
        python -m pip install uv; \
    fi
ENV PATH="/root/.cargo/bin:$PATH"

RUN apt-get install -y libpython3-dev libev-dev libpq-dev

# In Docker containers, we can use --system without --break-system-packages as we control the environment
RUN uv pip install --system --quiet --upgrade \
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

RUN mkdir -p /scryfallos
COPY ./api /scryfallos/api
