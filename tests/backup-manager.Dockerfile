FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pymysql restic mariadb-server gnupg ansible-core \
    && rm -rf /var/lib/apt/lists/*
COPY server-tools /source/server-tools
COPY roles/server_tools /source/roles/server_tools
COPY tests/integration_backup_manager.py /source/tests/integration_backup_manager.py
COPY tests/backup-manager-install.yml /source/tests/backup-manager-install.yml
CMD ["python3", "/source/tests/integration_backup_manager.py"]
