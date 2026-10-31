#!/usr/bin/env bash
# Static and controller tests. No server connections.
set -euo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_LOCAL_TEMP=${ANSIBLE_LOCAL_TEMP:-/tmp/wp-hosting-ansible-local}
export ANSIBLE_NOCOWS=1
python3 -m unittest discover -s tests/unit -v
for playbook in playbooks/*.yml; do
    ansible-playbook -i inventories/example-server/settings.yml --syntax-check "$playbook"
done
python3 - <<'PY'
from pathlib import Path
import ast
import subprocess
for root in ['scripts', 'library', 'tests', 'roles']:
    for path in Path(root).rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        data = path.read_text(errors='replace')
        if path.suffix == '.py' or data.startswith(('#!/usr/bin/env python3', '#!/usr/bin/python')):
            ast.parse(data, filename=str(path))
        elif data.startswith(('#!/bin/bash', '#!/usr/bin/env bash', '#!/bin/sh')):
            subprocess.run(['bash' if 'bash' in data.splitlines()[0] else 'sh', '-n', str(path)], check=True)
PY
