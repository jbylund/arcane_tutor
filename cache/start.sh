#!/bin/bash

# Check if SSL certificates are mounted
if [ ! -f /app/ssl/certs/nginx-selfsigned.crt ]; then
    echo "ERROR: SSL certificates not found at /app/ssl/certs/nginx-selfsigned.crt"
    echo "Please run 'make ssl_certs' to generate SSL certificates"
    exit 1
fi

echo "SSL certificates found, starting services..."

# Function to handle shutdown signals
cleanup() {
    echo "Received shutdown signal, stopping services..."
    if [ ! -z "$GUNICORN_PID" ]; then
        echo "Stopping gunicorn (PID: $GUNICORN_PID)..."
        kill -TERM "$GUNICORN_PID" 2>/dev/null
        wait "$GUNICORN_PID" 2>/dev/null
    fi
    if [ ! -z "$NGINX_PID" ]; then
        echo "Stopping nginx (PID: $NGINX_PID)..."
        kill -TERM "$NGINX_PID" 2>/dev/null
        wait "$NGINX_PID" 2>/dev/null
    fi
    echo "All services stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Start gunicorn in the background
echo "Starting gunicorn on port 8081..."
cd /app && python -m gunicorn --bind 127.0.0.1:8081 --workers 2 --timeout 60 "cache_service:create_app()" &
GUNICORN_PID=$!

# Wait a moment for gunicorn to start
sleep 2

# Start nginx in the background
echo "Starting nginx on ports 80 and 443..."
nginx -g "daemon off;" &
NGINX_PID=$!

# Wait for either process to exit
wait
