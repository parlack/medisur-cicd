#!/usr/bin/env bash
set -euo pipefail
# The image does not implement SONAR_JDBC_PASSWORD_FILE; bridge it explicitly.
export SONAR_JDBC_PASSWORD
SONAR_JDBC_PASSWORD="$(cat /run/secrets/sonar_db_password)"
test -n "$SONAR_JDBC_PASSWORD"
exec /opt/sonarqube/docker/entrypoint.sh "$@"