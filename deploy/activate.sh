#!/usr/bin/env bash
set -euo pipefail

root=${1:?Укажи каталог установки}
prefix=${2:?Укажи префикс образов}
revision=${3:?Укажи SHA релиза}
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ -f "$root/stack.env" && -f "$root/releases/$revision/compose.yaml" ]] || exit 2

exec 9>"$root/deploy.lock"
flock -n 9
source "$(dirname "${BASH_SOURCE[0]}")/cache-env.sh"
init_cache_env "$root/.private"
export IMAGE_PREFIX="$prefix"
export FRONTEND_ENV_FILE="$root/frontend.env"
export BACKEND_ENV_FILE="$root/backend.env"
export AGENT_ENV_FILE="$root/agent.env"
export INFERENCE_ENV_FILE="$root/inference.env"
export AGENT_PROMPTS_FILE="$root/agent.prompts.env"
export INFERENCE_PROMPTS_FILE="$root/inference.prompts.env"
export AGENT_PROMPTS_DIR="$root/.private/agent-prompts"
export INFERENCE_PROMPTS_DIR="$root/.private/inference-prompts"
install -d -m 0700 "$root/.private"
install -d -m 0755 "$AGENT_PROMPTS_DIR" "$INFERENCE_PROMPTS_DIR"

service_keys="$root/.private/service-keys"
if [[ ! -f "$service_keys" ]]; then
  (umask 077
    for service in agent inference; do
      var="${service^^}_TOKEN"
      if ! grep -Eq "^$var=.{16,}$" "$root/$service.env" 2>/dev/null; then
        printf '%s=%s\n' "$var" "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
      fi
    done > "$service_keys")
fi

export BACKEND_SERVICE_ENV_FILE="$service_keys"
export AGENT_SERVICE_ENV_FILE="$service_keys"
export INFERENCE_SERVICE_ENV_FILE="$root/.private/inference-keys"
(umask 077; sed -n '/^INFERENCE_TOKEN=/p' "$service_keys" > "$INFERENCE_SERVICE_ENV_FILE")

stack() {
  local release=$1
  shift
  IMAGE_TAG="$release" docker compose --project-name neti-next --project-directory "$root" \
    --env-file "$root/stack.env" -f "$root/releases/$release/compose.yaml" "$@"
}

previous=
if [[ -L "$root/current" ]]; then
  target=$(readlink "$root/current")
  previous=${target##*/}
  [[ $previous =~ ^[0-9a-f]{40}$ ]] || exit 2
fi

stack "$revision" pull
if ! stack "$revision" up -d --remove-orphans --wait --wait-timeout 90; then
  stack "$revision" ps -a >&2 || true
  if [[ -n "$previous" && "$previous" != "$revision" ]]; then
    stack "$previous" up -d --remove-orphans --wait --wait-timeout 90
  fi
  exit 1
fi

pointer=$(mktemp -d "$root/.current.XXXXXXXX")
trap 'rm -f "$pointer/current"; rmdir "$pointer"' EXIT
ln -s "releases/$revision" "$pointer/current"
mv -Tf "$pointer/current" "$root/current"
