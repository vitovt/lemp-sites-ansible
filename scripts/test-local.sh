#!/usr/bin/env bash
# Optional local smoke test in a throwaway Debian 13 docker container. LOCAL ONLY.
# The real test target is the test CT in inventories/test (README: "Test run");
# this script reuses that inventory and points host wptest at the container.
# Container differences vs LXC: privileged docker, /run tmpfs, no real disk
# mount for backups, apt SRV lookups off (box DNS quirk, tests/container/Dockerfile),
# /etc/hosts is a docker bind mount, so site/tasks/hosts.yml fails here (EBUSY on rename).
#   scripts/test-local.sh up        build image, start container wp-test
#   scripts/test-local.sh run       playbooks/site.yml twice (2nd run must be changed=0)
#   scripts/test-local.sh verify    checks inside the container
#   scripts/test-local.sh down      destroy container + network
#   scripts/test-local.sh all       up, run, verify, down
# Needs: docker (DOCKER_HOST), .venv with ansible-core, collections from requirements.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=wp-test NET=wp-test-net IP=172.31.250.10 IMAGE=wp-test-trixie
INV=inventories/test/hosts.yml
KEY=secrets/test/ssh/id_ed25519
CT_VARS=(-e "ansible_host=$IP" -e "ansible_ssh_private_key_file=$KEY"
  -e "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes'")
ANSIBLE_PLAYBOOK=${ANSIBLE_PLAYBOOK:-.venv/bin/ansible-playbook}
export ANSIBLE_HOST_KEY_CHECKING=False

in_ct() { ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes "root@$IP" "$@"; }

up() {
  install -d -m 0700 secrets/test/ssh
  [[ -f $KEY ]] || ssh-keygen -q -t ed25519 -N '' -C wp-test -f "$KEY"
  docker build -q -t "$IMAGE" tests/container >/dev/null
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet 172.31.250.0/24 "$NET" >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker create --name "$NAME" --hostname "$NAME" --network "$NET" --ip "$IP" \
    --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock --memory 3g "$IMAGE" >/dev/null
  docker cp "$KEY.pub" "$NAME:/root/.ssh/authorized_keys"
  docker start "$NAME" >/dev/null
  # docker cp keeps the host uid; sshd refuses that. (Short exec calls are fine.)
  docker exec "$NAME" sh -c 'chown root:root /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys'
  for _ in $(seq 30); do in_ct true 2>/dev/null && { echo "up: $NAME at $IP"; return; }; sleep 2; done
  echo "ssh to $NAME did not come up" >&2; exit 1
}

run() {
  "$ANSIBLE_PLAYBOOK" -i "$INV" "${CT_VARS[@]}" playbooks/site.yml | tee /tmp/wp-test-run1.log | tail -n 4
  "$ANSIBLE_PLAYBOOK" -i "$INV" "${CT_VARS[@]}" playbooks/site.yml | tee /tmp/wp-test-run2.log | tail -n 4
  grep -qE 'changed=0 .*failed=0' /tmp/wp-test-run2.log || { echo "NOT idempotent, see /tmp/wp-test-run2.log" >&2; exit 1; }
}

# Checks from README "Test run", run on the container.
verify() { scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes tests/verify.sh "root@$IP:/root/verify.sh"; in_ct bash /root/verify.sh </dev/null; }

down() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  echo "down: $NAME removed"
}

case "${1:-}" in
  up) up ;; run) run ;; verify) verify ;; down) down ;;
  all) up; run; verify; down ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
