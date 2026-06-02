#!/usr/bin/env bash

declare -gA ENV_FILE_LOADER_PROCESS_KEYS=()

ensure_conda_on_path() {
  if command -v conda >/dev/null 2>&1; then
    return 0
  fi
  local candidate bin_dir
  for candidate in \
    "${CONDA_EXE:-}" \
    "$HOME/miniconda3/bin/conda" \
    "$HOME/anaconda3/bin/conda" \
    /root/miniconda3/bin/conda \
    /root/anaconda3/bin/conda \
    /opt/conda/bin/conda
  do
    [[ -n "${candidate:-}" && -x "$candidate" ]] || continue
    bin_dir="$(dirname "$candidate")"
    export PATH="$bin_dir:$PATH"
    return 0
  done
  return 0
}

ensure_conda_on_path

capture_env_file_loader_process_keys() {
  ENV_FILE_LOADER_PROCESS_KEYS=()
  while IFS='=' read -r key _; do
    [[ -n "${key:-}" ]] || continue
    ENV_FILE_LOADER_PROCESS_KEYS["$key"]=1
  done < <(env)
}

load_env_files_preserving_process_env() {
  local env_files="${1:-}"
  local file raw_line line name value
  IFS=':' read -r -a files <<< "$env_files"
  for file in "${files[@]}"; do
    [[ -n "${file:-}" ]] || continue
    [[ -f "$file" ]] || continue
    while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
      line="${raw_line%$'\r'}"
      [[ "$line" =~ ^[[:space:]]*$ ]] && continue
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      if [[ "$line" =~ ^[[:space:]]*export[[:space:]]+ ]]; then
        line="${line#export }"
      fi
      [[ "$line" == *=* ]] || continue
      name="${line%%=*}"
      value="${line#*=}"
      name="${name#"${name%%[![:space:]]*}"}"
      name="${name%"${name##*[![:space:]]}"}"
      [[ -n "${name:-}" ]] || continue
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
      if [[ -z "${ENV_FILE_LOADER_PROCESS_KEYS[$name]+x}" ]]; then
        export "${name}=${value}"
      fi
    done < "$file"
  done
}
