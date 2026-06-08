"""Build a container with the Cloudflare `wrangler` CLI installed.

Cloudflare does not publish an official wrangler image, so we install a
pinned wrangler version on top of a pinned Node base image. Both versions
are pinned for reproducible deployments.
"""

import dagger
from dagger import dag

# Pinned Node base image (concrete patch tag + digest). Override the digest
# when bumping the tag: `docker buildx imagetools inspect node:<tag>`.
NODE_IMAGE = "node:22.22.3-alpine@sha256:968df39aedcea65eeb078fb336ed7191baf48f972b4479711397108be0966920"

# Default wrangler version. Exposed as a parameter so callers can override.
DEFAULT_WRANGLER_VERSION = "4.98.0"


def wrangler_container(
    token: dagger.Secret,
    version: str = DEFAULT_WRANGLER_VERSION,
) -> dagger.Container:
    """Return a container with `wrangler` installed and authenticated.

    Args:
        token: Cloudflare API token, exposed as `CLOUDFLARE_API_TOKEN`.
        version: Exact wrangler version to install (npm version specifier).
    """
    return (
        dag.container()
        .from_(NODE_IMAGE)
        .with_exec(["npm", "install", "-g", f"wrangler@{version}"])
        .with_secret_variable("CLOUDFLARE_API_TOKEN", token)
    )
