#!/usr/bin/env bash
set -euo pipefail
task_id="${1:?task id required}"
echo "example worker handled ${task_id}"
