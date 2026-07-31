#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for authenticated Elasticsearch..."
while true; do
  # Check the login endpoint until Elasticsearch and security are ready.
  http_code="$(
    curl --noproxy "*" -s \
      -u "elastic:${ELASTIC_PASSWORD}" \
      -o /dev/null \
      -w "%{http_code}" \
      "${ELASTICSEARCH_URL}/_security/_authenticate" || true
  )"

  if [[ "${http_code}" == "200" ]]; then
    break
  fi

  # An old Docker volume can still have a password from an earlier run.
  if [[ "${http_code}" == "401" || "${http_code}" == "403" ]]; then
    echo "Elasticsearch rejected the elastic user (HTTP ${http_code})." >&2
    echo "The Docker volume may have an old password." >&2
    echo "For this demo run: make clean && make run" >&2
    exit 1
  fi

  sleep 2
done

echo "Configuring the kibana_system user..."
curl --noproxy "*" -fsS \
  -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST \
  "${ELASTICSEARCH_URL}/_security/user/kibana_system/_password" \
  -d "{\"password\":\"${KIBANA_PASSWORD}\"}" >/dev/null

echo "Creating or updating ${ADMIN_USERNAME}..."
curl --noproxy "*" -fsS \
  -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PUT \
  "${ELASTICSEARCH_URL}/_security/user/${ADMIN_USERNAME}" \
  -d "{\"password\":\"${ADMIN_PASSWORD}\",\"roles\":[\"superuser\"],\"full_name\":\"Local Demo Administrator\"}" >/dev/null

echo "Elasticsearch security users are ready."
