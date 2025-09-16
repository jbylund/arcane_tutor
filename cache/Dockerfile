FROM ubuntu:latest

RUN apt-get update -qqq && apt-get install -qqq -y \
    build-essential \
    curl \
    dumb-init \
    libev-dev \
    libpq-dev \
    libpython3-dev \
    nginx \
    openssl \
    python3 > /dev/null

RUN ln -s $(python3 -c "import sys; print(sys.executable)") /usr/local/bin/python

ENV PIP_BREAK_SYSTEM_PACKAGES=1

RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python && \
    python -m pip install --upgrade pip && \
    python -m pip install --upgrade uv

# Copy requirements and install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN python -m uv pip install --no-cache-dir --quiet --system --upgrade --break-system-packages -r /app/requirements.txt

# Copy application code
COPY . /app/

# Create cache directory and SSL directories
RUN mkdir -p /data/cache /etc/ssl/certs /etc/ssl/private /app/ssl/certs /app/ssl/private

# Copy nginx configuration
RUN cp /app/nginx.conf /etc/nginx/nginx.conf

# Expose ports 80 and 443
EXPOSE 80 443

# Run the application with dumb-init for proper signal handling
CMD ["dumb-init", "--", "/bin/bash", "/app/start.sh"]
