# The API Port Was Published on All Interfaces

**Severity: high. Found 2026-07-26. Fixed 2026-07-27 in
[#781](https://github.com/jbylund/sylvan_librarian/pull/781), with the matching nginx upstream change
on the host.**

`docker-compose.yml` published the API with `ports: - "${API_PORT:-28080}:8080"`. Docker binds that on
`0.0.0.0`, so the service answered directly, in cleartext, with nginx entirely out of the path — a
plain `curl` to the origin address on port 18080 returned `200` for both `/get_pid` and `/search`.

Everything the proxy provides was bypassed: TLS, the HTTPS redirect, and any rate limiting added
later. It also defeated proxy-level fixes for anything else — a `location` block denying a path counts
for nothing when the backend is directly reachable, which is why this had to land before or with any
nginx-side control.

Green (18081) and dev (28080) were both closed from outside at audit time, so this was one stack's
port rather than the pattern. But the compose file treated all three identically; only the host's
firewall was making the difference.

This ships in the repo, so it was a self-hoster's exposure too, not only ours.

## The fix was not the obvious one-liner

`127.0.0.1:${API_PORT}:8080` looks right and would have broken the site. The nginx upstream was:

```nginx
upstream sylvan_librarian {
    server atlas:18080 max_fails=3 fail_timeout=10s;  # blue
    server atlas:18081 max_fails=3 fail_timeout=10s;  # green
    keepalive 128;
    keepalive_requests 1000;
}
```

`atlas` resolved to a LAN address, not loopback. Binding the containers to loopback would have made
**both** upstreams unreachable, and `proxy_next_upstream` would have had nowhere left to fail over to
— a full outage rather than a degraded one, and one the compose healthcheck would not have caught,
since it runs inside the container against `localhost:8080` and would have kept reporting healthy.
The nginx config also lives on the host rather than in this repo, so the compose half could not land
on its own.

What made the fix viable: **nginx runs on `atlas` itself**, so the upstream could point at
`127.0.0.1:1808x`. That made it two coordinated edits, one of them outside this repo, and the
out-of-tree half had to be in place *before* the next `make rolling-deploy`. Both are done.

## What the default should be for a self-hoster

Our topology is not the interesting question; the shipped default is. Three topologies cover
essentially every self-hoster:

| topology | wants |
| --- | --- |
| reverse proxy as a host process (ours) | `127.0.0.1` only |
| reverse proxy as a container on the same Docker network | **no published port at all** — the proxy reaches `apiservice:8080` directly |
| no proxy, direct LAN or WAN access | a routable interface |

Most self-hosters are in one of the first two, because TLS effectively requires a proxy — Caddy,
Traefik, nginx-proxy-manager. Only the third wants `0.0.0.0`, and that person knows they want it.

### Why the default should be loopback, not `0.0.0.0`

The decisive argument is not "defence in depth", it is that **Docker's published ports bypass host
firewalls**. Publishing a port inserts DNAT rules that are evaluated before the `INPUT` chain where
`ufw` and `firewalld` operate, so a self-hoster who ran `ufw deny 18080` is still fully exposed and
believes they are not. A `0.0.0.0` default therefore breaks the operator's mental model silently,
which is the worst property a default can have. Loopback fails the other way: visibly, immediately,
and fixed by setting one variable.

The usual objection — that loopback ruins first-run UX — mostly does not apply. Someone running
`make dev-up` browses `localhost` anyway. It only bites when the stack runs on a different machine
than the browser, and that operator should be making a deliberate choice.

### What shipped

```yaml
ports:
  - "${BIND_ADDR:-127.0.0.1}:${API_PORT:-28080}:8080"
```

One variable, safe default, and every topology reachable: host proxy works untouched, LAN access is
`BIND_ADDR=0.0.0.0`, and the container-proxy case can drop the `ports:` block entirely. The three
topologies are documented under "Binding and Reverse Proxies" in the README — the escape hatch is only
useful if it is discoverable.

A single default covers all of `envs/{blue,green,dev}`: the production pair sits behind the host nginx
and dev is browsed locally, so none of them needed an override.

## Related

Part of the July 2026 review. Its scope, the verified-clean list, and the severity rationale that
depended on other findings from the same pass are in the review notes, which are tracked out of tree
per [the `security-` convention](../README.md#unfixed-security-findings).

The proxy-side controls this undermined while it stood — HSTS and rate limiting — were both examined
after the bind was fixed and deliberately not adopted; that reasoning is recorded in the same notes.
