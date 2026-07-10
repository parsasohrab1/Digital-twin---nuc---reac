#!/bin/sh
# Additional InfluxDB buckets for NDT (DI-04)
# Runs after Docker init setup

set -e

influx bucket create \
  --name ekf_params \
  --org "${DOCKER_INFLUXDB_INIT_ORG:-ndt}" \
  --retention 90d \
  --token "${DOCKER_INFLUXDB_INIT_ADMIN_TOKEN}" \
  2>/dev/null || true

influx bucket create \
  --name pinn_predictions \
  --org "${DOCKER_INFLUXDB_INIT_ORG:-ndt}" \
  --retention 7d \
  --token "${DOCKER_INFLUXDB_INIT_ADMIN_TOKEN}" \
  2>/dev/null || true

influx bucket create \
  --name scenario_results \
  --org "${DOCKER_INFLUXDB_INIT_ORG:-ndt}" \
  --retention 30d \
  --token "${DOCKER_INFLUXDB_INIT_ADMIN_TOKEN}" \
  2>/dev/null || true

echo "NDT InfluxDB buckets initialized"
