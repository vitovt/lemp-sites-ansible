#!/usr/bin/env python3
"""Test tool delivery in an isolated /tmp prefix on an explicitly selected test VM."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inventory', required=True)
parser.add_argument('--limit', required=True)
a = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='tool-delivery-') as tmp:
    source = Path(tmp) / 'source'
    shutil.copytree(ROOT / 'server-tools', source, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    target = '/tmp/wp-tool-delivery-' + uuid.uuid4().hex
    playbook = Path(tmp) / 'check.yml'
    playbook.write_text('''---
- name: Test isolated server tool delivery
  hosts: wp
  become: true
  gather_facts: false
  roles:
    - server_tools
  tasks:
    - name: Execute the automatically exposed command
      ansible.builtin.command:
        argv: ["{{ server_tools_command_dir }}/delivery-probe"]
      register: probe
      changed_when: false
    - name: Check command output
      ansible.builtin.assert:
        that: probe.stdout == expected_output
    - name: Read library permissions
      ansible.builtin.stat:
        path: "{{ server_tools_root }}/lib/nested/data.txt"
      register: library_file
    - name: Check root ownership and library permissions
      ansible.builtin.assert:
        that:
          - library_file.stat.uid == 0
          - library_file.stat.mode == '0644'
    - name: Check that Python caches were excluded
      ansible.builtin.stat:
        path: "{{ server_tools_root }}/lib/__pycache__/bad.pyc"
      register: cache_file
    - name: Refuse copied Python caches
      ansible.builtin.assert:
        that: not cache_file.stat.exists
''')
    (source / 'lib/nested').mkdir()
    (source / 'lib/nested/data.txt').write_text('library data')
    (source / 'lib/__pycache__').mkdir()
    (source / 'lib/__pycache__/bad.pyc').write_bytes(b'not code')
    probe = source / 'bin/delivery-probe'
    def run(expected, unchanged=False):
        variables = {'server_tools_source': str(source), 'server_tools_root': target,
                     'server_tools_command_dir': target + '/commands', 'expected_output': expected}
        env = {**os.environ, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles'), 'ANSIBLE_NOCOWS': '1',
               'ANSIBLE_LOCAL_TEMP': tmp + '/ansible', 'ANSIBLE_HOME': tmp + '/home'}
        result = subprocess.run(['ansible-playbook', '-i', a.inventory, str(playbook),
                                 '--limit', a.limit, '-e', json.dumps(variables)],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=900)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        if unchanged and not re.search(r'changed=0\s', result.stdout):
            raise RuntimeError('Repeat changed resources:\n' + result.stdout)
        print('PASS tool delivery:', expected, 'unchanged' if unchanged else 'converged', flush=True)
    probe.write_text('#!/bin/sh\nprintf "first\\n"\n')
    run('first')
    run('first', unchanged=True)
    probe.write_text('#!/bin/sh\nprintf "second\\n"\n')
    run('second')
    probe.unlink()
    run('second', unchanged=True)
    print('PASS removed source command and its link remain on the VM:', target)
