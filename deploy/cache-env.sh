#!/usr/bin/env bash

init_cache_env() {
  local dir=$1
  install -d -m 0700 "$dir"

  (
    umask 077
    flock -x 9
    credentials="$dir/postgres-cache.env"

    if [[ ! -f "$credentials" ]]; then
      draft=$(mktemp "$dir/pg-env.XXXXXXXX")
      printf 'POSTGRES_PASSWORD=%s\nAGENT_CACHE_PASSWORD=%s\n' \
        "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')" \
        "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')" > "$draft"
      mv -T "$draft" "$credentials"
    fi

    backend_credentials="$dir/postgres-backend.env"
    if [[ ! -f "$backend_credentials" ]]; then
      draft=$(mktemp "$dir/pg-backend.XXXXXXXX")
      printf 'BACKEND_DATABASE_PASSWORD=%s\n' \
        "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')" > "$draft"
      mv -T "$draft" "$backend_credentials"
    fi

    draft=$(mktemp "$dir/pg-shared.XXXXXXXX")
    cat "$credentials" "$backend_credentials" > "$draft"
    mv -T "$draft" "$dir/postgres.env"

    for service in agent backend; do
      case "$service" in
        agent) entry=AGENT_CACHE_PASSWORD; destination=agent-cache.env ;;
        backend) entry=BACKEND_DATABASE_PASSWORD; destination=backend-database.env ;;
      esac
      password=$(sed -n "s/^$entry=//p" "$dir/postgres.env")
      [[ $password =~ ^[0-9a-f]{64}$ ]] || exit 1
      draft=$(mktemp "$dir/$service-env.XXXXXXXX")
      printf 'PGPASSWORD=%s\n' "$password" > "$draft"
      mv -T "$draft" "$dir/$destination"
    done
  ) 9>"$dir/cache.lock"

  export AGENT_POSTGRES_ENV_FILE="$dir/postgres-cache.env"
  export POSTGRES_ENV_FILE="$dir/postgres.env"
  export AGENT_CACHE_ENV_FILE="$dir/agent-cache.env"
  export BACKEND_DATABASE_ENV_FILE="$dir/backend-database.env"
}
