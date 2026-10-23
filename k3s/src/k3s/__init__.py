import hashlib
import re
from typing import Annotated

from dagger import Doc, check, field, function, object_type
from dagger_clients.core import CacheVolume, Container, File, Service, core

DEFAULT_IMAGE = "rancher/k3s:v1.33.4-k3s1@sha256:31a85202039b95a75781d537b35e5756ded4b43949f8e1f9f3db8e69d3b73497"
ALPINE_IMAGE = "alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"

# Derived from github.com/marcosnils/daggerverse/k3s (Apache-2.0),
# rewritten for the native Dagger 1 Python SDK.
ENTRYPOINT = """#!/bin/sh
set -eu

# K3s is not PID 1 under Dagger, so prepare the cgroup v2 root as Docker's
# Apache-2.0 dind helper does.
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs || :
  sed -e 's/ / +/g' -e 's/^/+/' \
    < /sys/fs/cgroup/cgroup.controllers \
    > /sys/fs/cgroup/cgroup.subtree_control
fi

exec "$@"
"""


def _instance_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError("name must be 1-64 letters, numbers, dots, underscores, or dashes")
    return value


@object_type
class Cluster:
    name: str = field()
    image: str = field()
    config_cache: CacheVolume = field()
    container: Container = field()
    enable_traefik: bool = field(default=False)
    debug: bool = field(default=False)

    @function(cache="never")
    def server(self) -> Service:
        flags = [
            "--bind-address",
            "$(ip route | awk '/src/ {print $NF; exit}')",
            "--disable",
            "metrics-server",
            "--egress-selector-mode=disabled",
        ]
        if not self.enable_traefik:
            flags.extend(["--disable", "traefik"])
        if self.debug:
            flags.append("--debug")
        container = self.container.with_exec(["rm", "-f", "/etc/rancher/k3s/k3s.yaml"])
        return container.as_service(
            args=["sh", "-c", "k3s server " + " ".join(flags)],
            insecure_root_capabilities=True,
            use_entrypoint=True,
        )

    @function(cache="never")
    async def config(self, server: Service, local: bool = False) -> File:
        timestamp = await core().current_timestamp()
        container = (
            core()
            .container()
            .from_(ALPINE_IMAGE)
            .with_mounted_cache("/config", self.config_cache)
            .with_service_binding("k3s", server)
            .with_env_variable("K3S_INSTANCE", self.name)
            .with_env_variable("K3S_CONFIG_READ", timestamp)
            .with_exec(
                [
                    "sh",
                    "-ec",
                    (
                        "for i in $(seq 1 240); do "
                        "test -s /config/k3s.yaml && cp /config/k3s.yaml /tmp/k3s.yaml && exit 0; "
                        "sleep 0.5; done; echo 'kubeconfig was not created' >&2; exit 1"
                    ),
                ]
            )
        )
        if local:
            container = container.with_exec(
                ["sed", "-i", "s#https://[^:]*:6443#https://localhost:6443#", "/tmp/k3s.yaml"]
            )
        return container.file("/tmp/k3s.yaml")


@object_type
class K3S:
    @function
    def cluster(
        self,
        name: Annotated[str, Doc("Unique cluster instance name")],
        image: Annotated[str, Doc("K3s image")] = DEFAULT_IMAGE,
        keep_state: Annotated[bool, Doc("Persist cluster state in a cache volume")] = False,
        enable_traefik: bool = False,
        debug: bool = False,
    ) -> Cluster:
        name = _instance_name(name)
        config_cache = core().cache_volume(f"k3s-config-{name}")
        container = (
            core()
            .container()
            .from_(image)
            .with_new_file("/usr/local/bin/dagger-k3s-entrypoint", ENTRYPOINT, permissions=0o755)
            .with_entrypoint(["/usr/local/bin/dagger-k3s-entrypoint"])
            .with_mounted_cache("/etc/rancher/k3s", config_cache)
            .with_mounted_temp("/etc/lib/cni")
            .with_mounted_temp("/var/lib/kubelet")
            .with_mounted_temp("/var/log")
            .with_exposed_port(6443)
        )
        if keep_state:
            container = container.with_mounted_cache("/var/lib/rancher", core().cache_volume(f"k3s-state-{name}"))
        else:
            container = container.with_mounted_temp("/var/lib/rancher")
        return Cluster(
            name=name,
            image=image,
            config_cache=config_cache,
            container=container,
            enable_traefik=enable_traefik,
            debug=debug,
        )

    @function
    @check
    async def smoke(self) -> None:
        timestamp = await core().current_timestamp()
        name = "smoke-" + hashlib.sha256(timestamp.encode()).hexdigest()[:16]
        cluster = self.cluster(name)
        service = cluster.server()
        config = await cluster.config(service)
        await (
            core()
            .container()
            .from_(cluster.image)
            .without_entrypoint()
            .with_service_binding("k3s", service)
            .with_file("/tmp/kubeconfig", config)
            .with_env_variable("KUBECONFIG", "/tmp/kubeconfig")
            .with_exec(
                [
                    "sh",
                    "-ec",
                    "for i in $(seq 1 60); do kubectl --request-timeout=2s get --raw=/readyz && exit 0; sleep 1; done; exit 1",
                ]
            )
            .sync()
        )
