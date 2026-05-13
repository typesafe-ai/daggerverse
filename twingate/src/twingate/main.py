from typing import Annotated, TypeAlias

import dagger
from dagger import Doc, dag, field, function, object_type

Port: TypeAlias = Annotated[int, Doc("Port the HTTP proxy listens on.")]

_DEFAULT_PORT = 3170
_DEFAULT_ALIAS = "twingate"
_DEFAULT_IMAGE = "twingate/client"
_DEFAULT_VERSION = "2026.106"


@object_type
class Twingate:
    """Twingate HTTP CONNECT proxy as a Dagger service."""

    service_key: Annotated[
        dagger.Secret,
        Doc("Twingate service key JSON for headless authentication."),
    ] = field()

    ctr: Annotated[
        dagger.Container,
        Doc("Container with the Twingate client installed."),
    ] = field()

    @classmethod
    async def create(
        cls,
        service_key: Annotated[
            dagger.Secret,
            Doc("Twingate service key JSON for headless authentication."),
        ],
        version: Annotated[
            str,
            Doc("twingate/client image tag."),
        ] = _DEFAULT_VERSION,
    ) -> "Twingate":
        ctr = dag.container().from_(f"{_DEFAULT_IMAGE}:{version}")
        return cls(service_key=service_key, ctr=ctr)

    @function
    def service(
        self,
        port: Port = _DEFAULT_PORT,
    ) -> dagger.Service:
        """Start the Twingate client as an HTTP proxy service (userspace mode).

        Returns a long-running service listening on the given port.
        Bind it to a container with `bind_proxy`, or manually via
        `Container.with_service_binding` + `HTTPS_PROXY`

        Learn more [here](https://www.twingate.com/docs/linux-userspace-networking).
        """
        _KEY_PATH = "/etc/twingate/service_key.json"
        return (
            self.ctr.with_mounted_secret(_KEY_PATH, self.service_key)
            .with_exposed_port(port)
            .as_service(
                args=[
                    "/usr/sbin/twingated",
                    "--http-proxy",
                    f"0.0.0.0:{port}",
                    "--tun",
                    "off",
                ],
            )
        )

    @function
    def bind_proxy(
        self,
        ctr: Annotated[
            dagger.Container,
            Doc("Container to attach the Twingate proxy to."),
        ],
        alias: Annotated[
            str,
            Doc("Hostname alias for the proxy service binding."),
        ] = _DEFAULT_ALIAS,
        port: Port = _DEFAULT_PORT,
        set_proxy_env_vars: bool = True,
    ) -> dagger.Container:
        """Bind the Twingate proxy to a container.

        If `set_proxy_env_vars` is True, it also sets common env vars:
            - HTTP_PROXY
            - HTTPS_PROXY
            - NO_PROXY=localhost,127.0.0.1
        """
        ctr = ctr.with_service_binding(alias, self.service(port=port))

        if set_proxy_env_vars:
            ctr = (
                ctr.with_env_variable("HTTPS_PROXY", f"https://{alias}:{port}")
                .with_env_variable("HTTP_PROXY", f"http://{alias}:{port}")
                .with_env_variable("NO_PROXY", "localhost,127.0.01")
            )

        return
