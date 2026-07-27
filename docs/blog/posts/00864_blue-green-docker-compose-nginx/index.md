---
title: "Zero-Downtime Deploys with Two Docker Compose Stacks and nginx"
date: 2027-07-17
publishDate: 2027-07-17
tags: ["infrastructure", "docker", "nginx", "deployment"]
summary: "Two identical Docker Compose stacks on one host, both live behind nginx. Deploying restarts them one at a time and waits for a health check in between, so one stack is always serving — even when the new containers re-import the entire card database."
---

## The Problem with In-Place Restarts

The first deploy strategy for Sylvan Librarian was the obvious one: `docker compose down` followed by `docker compose up`.
The gap between the two commands meant the service was unreachable, but that was not the main problem.
The real issue was what came after: the API process that starts fresh has to warm its LRU cache before latency returns to baseline.
On a cold start, cache-miss requests each hit PostgreSQL — P95 latency measured on the production instance by issuing sequential uncached searches immediately after restart stays around 200–400 ms for the first minute or two until the cache fills, compared to under 5 ms for a warm-cache hit.
The "downtime" was not just the restart itself.

The fix I reached for was not Kubernetes, not Nomad, not Fly.io's built-in zero-downtime deploys.
It was two Docker Compose stacks running on the same host, a few env files, and an nginx in front of both.

A note on the names before going further: the two stacks are called `blue` and `green`, but this is not a blue/green deployment in the usual sense.
There is no idle standby and no cutover.
Both stacks serve traffic all the time, and a deploy restarts them one after the other — a rolling restart across two replicas that happen to have colour names.
The distinction matters for what happens when a deploy goes wrong, which is the last section of this post.

## Two Stacks, One Host

Docker Compose's `--project-name` flag gives every container in a stack a namespaced name and an isolated network.
Two stacks with different project names can run the same `docker-compose.yml` on the same host without conflict, as long as their host ports do not collide.

The port split is in two env files:

```
# envs/blue
API_PORT=18080
APP_ENV=blue
ENABLE_CACHE=true
ENVIRONMENT=prod
ENABLE_ENGINE=true

# envs/green
API_PORT=18081
APP_ENV=green
ENABLE_CACHE=true
ENVIRONMENT=prod
ENABLE_ENGINE=true
```

Blue binds to `18080`, green binds to `18081`.
Each stack gets its own Docker project name (`sylvan_blue`, `sylvan_green`), its own bridge network, and its own volume namespace — so `pgdata` in blue is `sylvan_blue_pgdata`, never shared with green.
One stack can be torn down while the other serves traffic.

The data directory is also per-environment:

```yaml
# docker-compose.yml (simplified)
volumes:
  - ./data/api/${APP_ENV:-dev}/:/data/api
```

So `blue` reads from `data/api/blue/` and `green` reads from `data/api/green/`.
Both sets of data stay on disk across restarts.

## Both Stacks in One Upstream

The interesting part is what the nginx configuration *does not* contain: any notion of which stack is current.
Both are in the upstream, permanently.

```nginx
upstream sylvan_librarian {
    server atlas:18080 max_fails=3 fail_timeout=10s;  # blue
    server atlas:18081 max_fails=3 fail_timeout=10s;  # green
    keepalive 128;
    keepalive_requests 1000;
}
```

With no balancing directive, nginx round-robins, so in steady state each stack serves about half the traffic.
There is no active server, no promotion step, and no config template.
A deploy never touches this file and nginx is never reloaded.

What absorbs the restart is passive failure detection plus retry, and it depends on a detail of how the API starts.

### The Port Simply Disappears

`APIResource.__init__` runs `setup_schema()` and `import_data()`, and only when the constructor returns does `bjoern.run()` bind the socket
([api_resource.py](https://github.com/jbylund/sylvan_librarian/blob/bc903d2052aa7e62ac7cb4687d390bc5ee3f5c04/api/api_resource.py#L670-L671)).
A stack that is rebuilding, or importing 97,000 cards on a fresh volume, is not listening on its port at all.
It is not slow, and it is not returning errors — connections are refused outright.

That is precisely the failure mode nginx handles best.
When a request is routed to a stack that is down, the connection is refused, and `proxy_next_upstream` — which defaults to `error timeout` — retries it against the other server.
The client gets a normal response, one extra round trip later.
It never sees a failure.

After `max_fails=3` refusals inside `fail_timeout=10s`, nginx marks that server unavailable and stops routing to it for ten seconds.
All traffic goes to the surviving stack.
Ten seconds later nginx tries it again; if the stack is still importing, that probe fails and the timer resets.

So the total cost of a multi-minute restart is roughly one retried request every ten seconds, plus the three that were retried on the way in.
That is the entire deploy penalty.

### Why the Defaults Line Up

There are two independent notions of health here, and it is worth being clear that they never talk to each other.
`docker compose up --wait` gates the *deploy* on the container health check.
nginx's view is passive: it learns a stack is unavailable only by failing a request to it.

They happen to agree because the API refuses connections until it is genuinely ready.
Had it bound the socket early and returned `503` while loading data — the more common shape for a service with a slow warmup — the default `proxy_next_upstream` would not have covered it, since `error timeout` does not include `http_503`.
nginx would have cheerfully forwarded those `503`s to clients while considering the upstream perfectly healthy.
Getting that wrong is easy, and the symptom is a deploy that looks clean in the logs and drops requests anyway.

The `keepalive 128` pool is a throughput optimisation rather than part of the failover story, though it interacts with it: pooled connections to a stack that goes away are broken and those requests get retried like any other.
Note that upstream keepalive only works if the `location` block also sets `proxy_http_version 1.1` and clears the `Connection` header.

## The Deploy Script

`make rolling-deploy` (added in [PR #455](https://github.com/jbylund/sylvan_librarian/pull/455))
restarts the stacks one after the other. Because the image tag changes when the code does, `up`
recreates blue's containers — stopping the old ones and starting the new — and `--wait` blocks until
they are healthy before green is touched at all. The full target, anchored to the current commit
(line breaks added for readability):

```makefile
# https://github.com/jbylund/sylvan_librarian/blob/bc903d2052aa7e62ac7cb4687d390bc5ee3f5c04/makefile#L127-L132
rolling-deploy: deps-blue deps-green
	@echo "=== Deploying blue ==="
	cd $(GIT_ROOT) && docker compose \
	  --project-name sylvan_blue \
	  --env-file .env \
	  --env-file envs/blue \
	  --file $(BASE_COMPOSE) \
	  up --remove-orphans --detach --wait
	@echo "=== Blue healthy. Deploying green ==="
	cd $(GIT_ROOT) && docker compose \
	  --project-name sylvan_green \
	  --env-file .env \
	  --env-file envs/green \
	  --file $(BASE_COMPOSE) \
	  up --remove-orphans --detach --wait
	@echo "=== Rolling deploy complete ==="
```

The `--wait` flag blocks until every service in the stack passes its health check (or the retries are exhausted).
The API health check probes `localhost:8080/get_pid`, which only succeeds after the process has fully started and is accepting connections
([docker-compose.yml, lines 89–100](https://github.com/jbylund/sylvan_librarian/blob/f3e11f809493ab330a9aa67a4acb8a13dbdcf090/docker-compose.yml#L89-L100)):

```yaml
healthcheck:
  test:
    - CMD
    - curl
    - --fail
    - --user-agent
    - healthcheck
    - localhost:8080/get_pid
  interval: 5s
  timeout: 1s
  retries: 60
  start_period: 40s
```

`retries: 60` at `interval: 5s` gives the stack five minutes to become healthy.
The API process takes roughly 40 seconds to load card data on first start — the `start_period` covers that window.

## Failure Modes and Rollback

**Blue never becomes healthy.** `docker compose up --wait` exits non-zero after the retry window, and make aborts the target before green is touched.
Green is still running the previous build and still serving, so users see nothing.
This is the case the design actually protects against, and it protects against it well: a deploy that fails to start cannot take the service down, because the failure is detected before the second stack is disturbed.

**A bug makes it through the health check.** The `/get_pid` probe confirms the process started and responded — it does not verify that search queries return correct results, that card data loaded cleanly, or that database connectivity is intact.
A code bug that allows startup will reach production, and here the rolling restart is weaker than a true blue/green cutover.
Once the deploy finishes, *both* stacks run the new code. There is no untouched old stack to fall back to, so rolling back means checking out the previous commit and running `make rolling-deploy` again — another full cycle, including a re-import if the data changed, rather than a config reload.

That asymmetry is the price of keeping both stacks live.
A cutover deployment holds the previous version idle and can revert in seconds; this one spends that capacity on serving traffic instead, and accepts a slower rollback.
For a read-heavy search API with a single operator, where the common failure is "the new build does not start" rather than "the new build is subtly wrong", that has been the right trade.

**Reduced capacity mid-deploy.** While one stack is restarting, the other carries all traffic alone, and it carries it with a cold cache once its turn comes.
Deploys are best run when traffic is low.

## What This Skips

This approach works well for a single-host deployment with a low deploy rate.
Three things it does not handle:

**Cross-host load balancing.** Both stacks are addressed as ports on one host. Spreading across machines would move the failover decision up to a load balancer, and passive detection scales poorly once a marked-down server is a whole machine.

**Non-additive database migrations.** Both stacks share their postgres volume only within a project, but if a migration is not backward-compatible — a dropped column, a changed type — old and new code cannot run simultaneously against the same schema.
The current deploy requires every migration to be additive (new columns with defaults, nothing dropped).

**Fast rollback.** As above, a completed deploy leaves no previous version running, so reverting costs a second full deploy rather than a switch flip.

For a self-hosted project where the operator controls the deploy window and traffic is predictable, those constraints are manageable.
The gap between the complexity of running Kubernetes and the simplicity of `docker compose up --wait` plus four lines of upstream config is wide enough that the constraint list is worth accepting.

Two stacks, one upstream, and a retry.
No magic — just composition of primitives that already exist.

## Related

The multi-process worker model this deploys is covered in
[Falcon + Bjoern: Choosing a Python Web Framework](00064_falcon-bjoern-web-framework.md).
Cross-process cache invalidation — a subtlety exposed by the multi-worker setup — is in
[Multi-Process Cache Invalidation with a Generation Counter](00512_multi-process-cache-invalidation.md).
