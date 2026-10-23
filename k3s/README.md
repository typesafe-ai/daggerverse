# k3s

Run an isolated [K3s](https://k3s.io/) Kubernetes API as a Dagger service.

```console
dagger call -m github.com/typesafe-ai/daggerverse/k3s \
  cluster --name example server
```

From another module, create one `cluster`, get its `server`, then pass that
service to `config` and bind it to the Kubernetes client. Use a unique `name`
for concurrent clusters. Cluster state is ephemeral unless `keep-state` is
enabled.

This module is derived from
[`marcosnils/daggerverse/k3s`](https://github.com/marcosnils/daggerverse/tree/28eea1fcf3b6ecb38a628186107760acd717442f/k3s),
rewritten for the native Dagger 1 Python SDK. The original and this derivative
are licensed under Apache-2.0; see [`LICENSE`](./LICENSE).
