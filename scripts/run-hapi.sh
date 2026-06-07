#!/usr/bin/env bash
set -euo pipefail

# Native HAPI FHIR launcher — no Docker. Tuned for the Apple M4 (16GB).
# Reads DB creds from ./.env, runs the Spring Boot war with generational ZGC.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
export DB_PASSWORD="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"

WAR="$ROOT/.build/hapi-fhir-jpaserver-starter/target/ROOT.war"
[ -f "$WAR" ] || { echo "ERROR: war not built at $WAR (run the Maven build first)"; exit 1; }

mkdir -p "$ROOT/logs"

# JVM tuning:
#  - Generational ZGC: sub-millisecond pauses, ideal for a low-latency FHIR API.
#  - Heap capped at 2g so Postgres + the MLX AI layer share the 16GB comfortably.
#  - String dedup: FHIR JSON produces huge volumes of duplicate strings.
exec "$JAVA_HOME/bin/java" \
  -XX:+UseZGC -XX:+ZGenerational \
  -Xms1g -Xmx2g \
  -XX:+UseStringDeduplication \
  -XX:+AlwaysActAsServerClassMachine \
  -Dfile.encoding=UTF-8 \
  -Djava.security.egd=file:/dev/urandom \
  -jar "$WAR" \
  --spring.config.additional-location="file:$ROOT/config/application.yaml"
