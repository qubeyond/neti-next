#!/usr/bin/env bash
set -euo pipefail

root=${1:?Укажи каталог установки}
prefix=${2:?Укажи префикс образов}
revision=${3:?Укажи SHA релиза}
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ -f "$root/stack.env" && -f "$root/releases/$revision/compose.yaml" ]] || exit 2

exec 9>"$root/deploy.lock"
flock -n 9
export IMAGE_PREFIX="$prefix"
export FRONTEND_ENV_FILE="$root/frontend.env"
export BACKEND_ENV_FILE="$root/backend.env"
export AGENT_ENV_FILE="$root/agent.env"

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
