# Transfer controller data

`archive.sh` exports the private files needed to manage the same servers from
another clone. Install the controller Python dependencies from `README.md` and
the `7zip` package first:

```sh
sudo apt install 7zip
./archive.sh
```

The script asks for a password twice. It creates
`lemp-sites-ansible-YYYYMMDD-HHMMSS.zip` in the repository root. The ZIP
contains every private server directory in `inventories/` and the old root
`secrets/` directory if it exists. It excludes `inventories/example-server/`
because a fresh clone already has that public template. It also excludes Git
history, caches, virtual environments, test results, and unrelated files.

When a server sets `ansible_ssh_private_key_file`, the script adds that key to
the server's inventory directory in the ZIP and changes the path in the ZIP
copy of `settings.yml`. It does not change the live settings. When a server
does not set a key, the script does not guess or add one. Copy any SSH agent or
SSH client configuration that the server needs separately.

The script uses the installed `7z` program to make an AES-256 encrypted ZIP.
The ZIP file names remain visible without the password. Keep the ZIP private
and use a strong password. The ordinary `unzip` program on Debian cannot read
this AES ZIP. Restore from the root of a fresh clone:

```sh
7z x lemp-sites-ansible-YYYYMMDD-HHMMSS.zip
PATH="$PWD/.venv/bin:$PATH" scripts/lemp my-server doctor
```

Install the controller dependencies before `doctor`. On Linux, `7z x` restores
the saved file modes. Check them with `doctor` after transfer, especially when
another file system or transfer tool changed modes.

The optional `--password PASSWORD` argument skips the password prompt. Its
value can remain in shell history or process listings. Prefer the interactive
prompt. Use `--delete-after-archive` to request deletion after the ZIP passes
an integrity test:

```sh
./archive.sh --delete-after-archive
```

The script then calls `clean.sh --clear-inventories` and asks for `y` before
deletion. It also removes the old root `secrets/` directory and any
repository-local SSH key that it copied into the ZIP. It keeps SSH keys outside
this repository because another project can use them. If you answer `N`, the
ZIP and source data remain. No data is deleted when ZIP creation or validation
fails.
