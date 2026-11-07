FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-yaml bash bash-completion util-linux sudo git php8.4-cli curl ca-certificates ansible-core \
    && rm -rf /var/lib/apt/lists/*
# Same pinned public WP-CLI artifact as the PHP role. No site or database is downloaded.
RUN curl --fail --location --retry 4 --retry-delay 3 \
    https://github.com/wp-cli/wp-cli/releases/download/v2.12.0/wp-cli-2.12.0.phar -o /usr/local/bin/wp \
    && echo 'be928f6b8ca1e8dfb9d2f4b75a13aa4aee0896f8a9a0a1c45cd5d2c98605e6172e6d014dda2e27f88c98befc16c040cbb2bd1bfa121510ea5cdf5f6a30fe8832  /usr/local/bin/wp' | sha512sum -c - \
    && chmod 0755 /usr/local/bin/wp
COPY server-tools /source/server-tools
COPY roles/server_tools /source/roles/server_tools
COPY roles/site/tasks/one-site.yml /source/roles/site/tasks/one-site.yml
COPY tests/integration_site_shell.py /source/tests/integration_site_shell.py
CMD ["python3", "/source/tests/integration_site_shell.py"]
