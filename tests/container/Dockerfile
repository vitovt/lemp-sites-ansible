# Throwaway Debian 13 with systemd as PID 1, for scripts/test-local.sh only.
FROM debian:trixie
# apt SRV lookups return a sinkhole address in the box sandbox -> EnableSrvRecords false.
ENV container=docker DEBIAN_FRONTEND=noninteractive
RUN printf 'Acquire::Retries "5";\nAcquire::EnableSrvRecords "false";\n' > /etc/apt/apt.conf.d/80test \
 && apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus python3 python3-apt \
      ca-certificates curl iproute2 procps openssh-server \
 && apt-get clean \
 && systemctl mask getty@.service console-getty.service systemd-firstboot.service \
      systemd-udevd.service systemd-udev-trigger.service systemd-remount-fs.service \
      sys-kernel-config.mount sys-kernel-debug.mount sys-kernel-tracing.mount \
 && systemctl enable ssh \
 && install -d -m 0700 /root/.ssh
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
