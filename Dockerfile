FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for pysnmp
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create directory for state database
RUN mkdir -p /data

# Default environment variables
ENV NETBOX_URL="" \
    NETBOX_TOKEN="" \
    NETBOX_VERIFY_SSL="false" \
    SWITCHES_ROLE="sw" \
    SNMP_COMMUNITY="public" \
    SNMP_VERSION="2c" \
    SNMP_TIMEOUT="5" \
    SNMP_RETRIES="2" \
    STABILITY_RUNS="2" \
    STATE_DB_PATH="/data/state.db" \
    POLL_INTERVAL="0" \
    DRY_RUN="false" \
    CABLE_STATUS="planned"

# Run the service
ENTRYPOINT ["python", "-m", "src.ipmi_autocabling"]
