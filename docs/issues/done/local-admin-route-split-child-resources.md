# Child API Resources, with the Admin Split as the First Consumer

Give `APIResource` a way to mount **child resources** at a path prefix, each with its own route
table, its own registration policy, and its own error behavior. Move the admin/data-management
handlers into one such child at `/_admin`.

The motivation is that `APIResource` has no way to express "this handler is not part of the public
API." Registration auto-exposes every public method as an HTTP route, so data-management handlers sit
in the same flat namespace as `search` — and the only lever for hiding one is a `_` prefix, which also
lies about its Python visibility. There is no boundary to put them behind because registration is a
single flat `action_map` built from `dir(self)`.

This has security consequences for any deployment, which is why it gates tagged releases
([#778](https://github.com/jbylund/sylvan_librarian/issues/778)) — a self-hoster should not have to
know to firewall these paths.

**Severity: critical. Found 2026-07-26. Fixed 2026-08-21** across
[#794](https://github.com/jbylund/sylvan_librarian/pull/794),
[#961](https://github.com/jbylund/sylvan_librarian/pull/961)–[#964](https://github.com/jbylund/sylvan_librarian/pull/964),
and [#966](https://github.com/jbylund/sylvan_librarian/pull/966). Declassified and moved out of the
`security-` prefix now that the fix has shipped.

## Exposure as found

Probes were run against the live host on 2026-07-26. 37 routes are reachable with no authentication,
and nginx does not allowlist paths. Among them:

| Route | Effect |
| --- | --- |
| `setup_schema` | Applies migrations; **drops everything** if applied migration hashes differ from the files |
| `import_data` | Full Scryfall re-import |
| `backfill_prefer_scores`, `backfill_cubecobra_scores` | Whole-table `UPDATE`s |
| `import_all_is_tags`, `import_art_tags`, `import_oracle_tags` | Writes + outbound fetches |
| `import_card_by_name`, `import_cards_by_search`, `ingest_cubecobra` | Writes + outbound fetches |
| `get_migrations` | Publishes the full DB schema (31 KB) |
| `get_stats` | Streams + reparses the whole bulk dump; blocks a worker for tens of seconds per request |

Reachability was established with the **read-only** `get_migrations`, deliberately, rather than by
firing `setup_schema` or `import_data` at production — so the impact above is reasoned from source,
not demonstrated end to end:

```
$ curl -o /dev/null -w "%{http_code} %{size_download}\n" https://sylvan-librarian.com/get_migrations
200 31094
```

Three things make this worse than "an endpoint exists":

- **`add_sink` binds every HTTP method** (`api/api_worker.py:141`). `POST/PUT/DELETE/PATCH /get_pid`
  all return 200, and conversely `GET /setup_schema` works. A mutation needs only a URL fetch — a
  crawler, a link prefetcher, or any third-party page's `<img src="…/import_data">`.
- **`robots.txt` returns `Disallow:` (empty)** — crawlers are explicitly permitted everywhere.
- **The 404 handler publishes the route map** (`api/api_resource.py:789-796`) — every route name,
  docstring, and parameter, on any bogus path. That is where the table above came from; no source
  access was needed.

**Nothing calls these over HTTP**, so gating them at the HTTP layer will not break startup,
`make rolling-deploy`, or any script in `scripts/`. In-process callers are enumerated under
[call-site impact](#call-site-impact) below.

Interim stopgaps were considered and rejected as fail-open: a `_PUBLIC_ROUTES` allowlist, or
`_`-prefixing the admin methods. Both leave the next new method exposed by default, which is the
actual defect.

## The problem with the current registration

`api/api_resource.py:622-628`:

```python
for method_name in dir(self):
    if method_name.startswith("_"):
        continue
    method = getattr(self, method_name)
    if callable(method):
        self.action_map[method_name] = make_type_converting_wrapper(method)
```

Three properties worth naming, because the design has to fix all three and not just the third:

1. **Fail-open.** Exposure is the default. A method is public unless someone remembers `_`.
2. **`_` is overloaded.** It means both "private to the class" and "not HTTP-reachable", so a
   genuinely-internal-API method like `setup_schema` — called from `__init__` and from tests — cannot
   be hidden from HTTP without lying about its Python visibility.
3. **The route table and the 404 listing are the same object.** `_build_routes_listing` iterates
   `action_map`, so anything registered is also *advertised*. See the trap below.

A fourth, latent: `dir(self)` + `callable()` means adding any new public attribute to the class can
change the route table. If a child resource is stored as `self.admin`, it escapes registration only
because an `AdminResource` instance has no `__call__`. That is the same accident that exposed
`read_sql`. **The replacement should register explicitly, not by introspecting `dir()`.**

## The cut line, measured

The admin route set, stated explicitly so the closure below is checkable — **11 routes move**:

`setup_schema`, `import_data`, `import_all_is_tags`, `import_art_tags`, `import_oracle_tags`,
`import_card_by_name`, `import_cards_by_search`, `ingest_cubecobra`, `backfill_prefer_scores`,
`backfill_cubecobra_scores`, `discover_is_tags_from_syntax`

Three more are admin-adjacent but should be *reclassified* rather than moved (see below), and
`prefer_score_tuner` is an open question. Everything else — `search`, `card`, `get_catalog`,
`get_common_keywords`, `random_search`, `get_pid`, and the static-asset handlers — stays public.

The naive framing is "move the handlers." That is wrong — they are entangled with private helpers,
and the real question is where those helpers land. Computing the transitive `self.*` closure from the
admin route set and from the public route set separately:

- **Admin closure:** 16 helpers. **Public closure:** 13 helpers.
- **Reached only by admin — moves with it (11):** `_add_is_tag_to_cards`,
  `_add_is_tag_to_cards_or_printings`, `_add_is_tag_to_custom`, `_add_is_tag_to_printings`,
  `_clear_caches`, `_fetch_cubecobra_data`, `_import_recent`, `_insert_cubecobra_data`,
  `_run_import_under_lock`, `_scryfall_search`, `_upsert_cards`
- **Truly shared, reached by both (5):** `_reload_engine`, `_run_query`, `_serve_static_file`,
  `_set_statement_timeout`, `_setup_complete`

So the move is **11 routes + 11 helpers = 22 methods**.

The infrastructure handles need the same per-handle treatment, because "admin touches it" and "both
halves need it" are different claims and only the second one is seam:

| Handle | Classification |
| --- | --- |
| `_conn_pool` | **Shared.** Admin routes use it directly; public reaches it via `_run_query`, `_setup_complete`, `_reload_engine`. |
| `_last_import_time` | **Shared.** Admin writes it (`import_data`, `_import_recent`, `_run_import_under_lock`); public reads it in `_setup_complete`. |
| `_cache_generation` | **Shared.** `_search` reads it, admin-only `_clear_caches` bumps it. |
| `_engine`, `_engine_reload_guard` | **Not independent seam members.** Public reads `_engine` directly (`_search`, `get_catalog`, `random_search`); admin touches both only *through* `_reload_engine`, already one of the shared 5. |
| `_import_guard`, `_schema_setup_event` | **Admin-only** — sole user is `setup_schema`. Move outright. |
| `_session` | **Admin-only** once `discover_is_tags_from_syntax` moves; other users are `_fetch_cubecobra_data` and `_scryfall_search`. |
| `_bulk_data_fetcher` | **Admin-only** once `get_stats` is deleted; other users are `_run_import_under_lock`, `import_oracle_tags`, `import_art_tags`. |

So the entire coupling surface is **5 methods + 3 handles**, not five methods and nine handles. That
is a small enough seam to make the split worthwhile rather than a rename with extra steps — and it is
small enough that the option-(b) context object below is a genuinely modest object rather than a bag
of nine references.

Three handlers should be reclassified rather than moved, since the split is the moment to ask whether
they should be routes at all:

- **`read_sql`** is a file-reading helper that became a route by accident. It joins a caller-supplied
  `filename` onto a path under `api/sql/`, and the only reason that is not a live path traversal is
  that `_handle` injects `falcon_response=` into every call and `read_sql`'s signature has no
  `**kwargs` to absorb it, so requests die on `TypeError` first. Adding `**_` to that signature would
  arm it. It should be `_read_sql` or move to `db_utils` — not a route at all. Note it cannot move
  into the admin child: the public route `get_common_keywords` calls it (`api/api_resource.py:1842`),
  alongside `backfill_prefer_scores` (`:1867`) and `backfill_cubecobra_scores` (`:2005`). So it is a
  **sixth shared method**, which is the argument for `db_utils` over `_read_sql` — a shared file reader
  has no reason to be a method on either resource.
- **`get_migrations`** is a thin pass-through to `db_utils.get_migrations()`. It publishes the full
  schema and has no caller that needs it over HTTP.
- **`get_stats`** (`api/api_resource.py:940`) has **no caller anywhere** — `git grep get_stats`
  returns only the definition; no test, script, makefile target, or frontend reference. It streams
  every line of the `default_cards` bulk dump through `preprocess_card` to count JSON key
  frequencies, which is tens of seconds of single-threaded CPU that blocks one Bjoern worker for the
  duration — unauthenticated worker exhaustion, one request per worker. `_ensure_cached` keeps the
  dump on local disk, so this is not repeated bandwidth in the steady state, but the cache path is
  derived from the dump's published filename: when Scryfall rolls a new dump the path changes and the
  next callers each pay a full download, since `_ensure_cached` coordinates only by atomic rename and
  has no cross-process lock. It also declares `-> dict[str, Any]` while returning
  `most_common()`'s list of tuples, which is its own evidence that nothing exercises it. **Delete it**
  rather than moving it; that also removes `_bulk_data_fetcher` from the shared-handle list.

## Design

### Extract the routing machinery

Today `make_type_converting_wrapper`, `_max_positional_args`, and `_build_routes_listing` are applied
inline in `APIResource.__init__`. A child resource needs all three, so they move behind one reusable
object — say `api/routing.py`:

```python
class Router:
    """Owns one action_map: registration, dispatch, and (optionally) a route listing."""

    def __init__(self, *, advertise: bool = False) -> None:
        self._actions: dict[str, Callable] = {}
        self._capacity: dict[str, float] = {}
        self._advertise = advertise      # controls 404 listing; see the trap below

    def register(self, name: str, func: Callable) -> None: ...
    def mount(self, prefix: str, child: "Router") -> None: ...
    def resolve(self, path: str) -> tuple[Callable, list[str]]: ...
```

`register` is explicit — callers name what they expose. That kills the fail-open default and the
`dir()` fragility in one move, and it decouples "is this a route" from "does this start with `_`",
which is what lets `setup_schema` keep its honest Python name.

### Dispatch

`_handle` currently does exact-match-then-split (`api/api_resource.py:733-745`), where the
exact-match branch exists for the slash-containing static keys like `static/app_js`. Prefix
dispatch slots in ahead of that:

```python
path = req.path.strip("/") or "_root"
path = path.replace(".", "_")
head, _, rest = path.partition("/")
if head in self._mounts:
    return self._mounts[head].dispatch(rest, req, resp)
# ...existing exact-match, then action_word/args split
```

Note the existing global `.` → `_` rewrite still applies, so `/_admin/setup_schema` arrives as
`_admin/setup_schema` and partitions cleanly. Worth a test that a mount prefix cannot be spoofed by
a dotted path.

### The 404 listing trap

**This is the part most likely to be got wrong, because it undoes the whole change silently.**
`_build_routes_listing` iterates `action_map`, so it has no concept of "private" — it lists whatever
was registered. `_root` proves it: underscore-prefixed, explicitly registered at
`api/api_resource.py:629`, and it does appear in the live 404 response.

If the admin handlers are registered into a router that participates in the listing, they reappear as
`_admin/setup_schema`, `_admin/import_data`, … and the mount becomes a URL prefix rather than a
boundary. Requirements:

- The admin router is built with `advertise=False` and contributes nothing to any listing.
- The admin router's *own* 404 does not list its routes either — or lists them only after auth.
- An unknown prefix returns the **same** 404 as any other unknown path, so `/_admin/` is not
  distinguishable from `/nonsense/`.
- Best: make the public listing opt-in too (list a known-public set), so the failure mode of
  forgetting a flag is under-advertising rather than over-advertising.

### Sharing state with the child

The 5 shared methods and ~9 infra handles have to reach the child somehow. Three options:

**(a) Child holds a parent reference** — `AdminResource(parent=self)`. Smallest diff by a wide
margin; internal callers become `self._admin.setup_schema()`. Does *not* decouple anything — admin
can still reach all of `APIResource` — so the boundary is purely about routing. Honest about that.

**(b) Extract a shared context** — an `AppContext` carrying the infra handles, with the 5 shared
methods moving onto it or onto a service layer beneath both resources. Genuinely decouples. Biggest
diff, and the 5 shared methods are the awkward part: they are behavior, not state, so folding them
into a context risks a god object.

**(c) Context for the handles + a small mixin for the shared 5.** Middle ground, two new concepts.

**Recommendation: (a) first, (b) later if it earns its keep.** All of the security value is in the
routing boundary and the auth check; none of it depends on breaking the parent reference. Shipping
(a) makes the exposure fix small and reviewable, and leaves the decoupling as an independent,
non-urgent refactor that can be judged on its own merits.

### Auth at the mount

The mount point is what makes this cheap: one check when dispatch enters the child, rather than a
decorator on 14 handlers that someone forgets on the 15th.

**The check has to be in the app, not only in the proxy.** `docker-compose.yml` publishes the API
with `ports: - "${API_PORT:-28080}:8080"`, which Docker binds on all interfaces — so wherever the
host firewall permits it, the service answers directly with the proxy out of the path and a
`location /_admin { deny all; }` counts for nothing. Binding `127.0.0.1:${API_PORT}:8080` is worth
doing independently, but the app-level check is the control that does not depend on deployment
topology.

If the check is a shared secret, compare with `secrets.compare_digest` and fail to the indistinguishable
404, not a 401 — no reason to confirm the mount exists. Keep it a **header** token rather than a
cookie: responses currently set `access-control-allow-origin: *`, which is safe only because there is
no ambient credential for a cross-origin caller to ride on. A cookie-based admin scheme would silently
invalidate that and require revisiting CORS; a header token does not.

#### Decided instead, 2026-08-21: HTTP Basic Auth, not a bespoke header + indistinguishable 404

Implemented in `api/middlewares/admin_auth_middleware.py`. The plan above optimized purely for
not confirming the mount's existence to a scanner. In practice `_admin` is operated from a browser
on the local network, not only from scripts, and a bespoke header can't be attached from a plain
address-bar navigation — the realistic choices were a query-param token (leaks into logs/history),
a cookie (the CORS/ambient-credential problem this doc already ruled out), or Basic Auth (native
browser prompt, `curl -u` works with no extra tooling, one shared secret).

Took Basic Auth, explicitly trading the indistinguishable-404 property for browser ergonomics: a
`401` with `WWW-Authenticate` is distinguishable from a generic 404 to a path-enumerating scanner.
Accepted because the mitigations that already exist — `_admin` over `/admin`, default loopback
bind in `docker-compose.yml` — do most of the work of making the mount uninteresting to scan; a
targeted attacker who already knows the path gets no extra signal either way (both a 404 and a
401 confirm "something is here" to someone probing this specific path on purpose). The password is
still compared with `secrets.compare_digest`, and only the password half of the credential is
checked — there's one shared secret, not per-operator accounts, so the username is ignored.

This also resolves the "reachable from the public interface at all?" open question below in favor
of yes, with the mount password required over the network — loopback-only was ruled out because it
costs the ability to trigger a backfill from off-host.

**`Cache-Control: no-store` on every admin response, pass or reject**, set once in the same
middleware rather than by convention in each handler. Belt-and-suspenders relative to the auth
check itself: `CachingMiddleware` already never serves a cache hit to a request the auth check
rejected, because the auth middleware runs first in the middleware list and short-circuits via
`resp.complete` before `CachingMiddleware.process_request` ever looks at the cache — but stamping
`no-store` means nothing under the mount is ever eligible for `CachingMiddleware` to store in the
first place, independent of that ordering holding forever.

ADMIN_PASSWORD is generated into `env.json` on first boot by `scripts/gen_env_json.sh`, alongside
the existing postgres credentials — but unlike those, it's backfilled into an *existing* env.json
too, since nothing persists it into a database volume the way XPGPASSWORD does.

#### Follow-on, same day: the 404 listing shows admin routes too, once authenticated

Delisting (step 3) hides admin routes from the 404 listing to keep the mount from becoming "a
directory of what is behind it" to an unauthenticated caller — but that's the only caller it
needs to hide from. A caller who has already sent a valid admin credential has proven they hold
the shared secret, so `APIResource._raise_not_found` now serves a second, precomputed listing
(`build_routes_listing(self.routes, include_unadvertised=True)`) instead of the public one when
`req.context["admin_authenticated"]` is set. `AdminAuthMiddleware` sets that flag on *every*
request now, not only ones under the mount, since a 404 for any unresolved path can occur
anywhere — the credential check itself stays cheap on the hot path because the common case (no
Authorization header) short-circuits immediately.

This reopens the caching hazard the `no-store` header above was written to close, in a form that
header doesn't cover: the enriched listing is a 404 for a path that isn't under `_admin/` at all,
so `AdminAuthMiddleware` never touches its `Cache-Control` header. Since `CachingMiddleware`'s
cache key has no notion of auth state, caching that response would either leak the admin listing
forward to a later unauthenticated caller on the same bogus path, or cache a plain 404 first and
mask the enriched one from a later authenticated caller.

Went through three shapes before landing:

1. **Disable 4xx caching outright** — simple, but a bigger behavior change than the problem called
   for: 4xx responses were cacheable before this doc's changes existed, and only one handler in the
   entire app (`_raise_not_found`) actually varies its output by auth state.
2. **Partition 4xx by caller** — an admin-authenticated 4xx and an anonymous 4xx to the same request
   go in separate cache slots (a suffix on the ordinary key), so 2xx/3xx keeps sharing the one slot
   every caller maps to and only the 4xx path pays for the extra slot.
3. **Reorder the lookup** to avoid a second cache read on the common path: check the ordinary slot
   first, only consult the admin-only slot when that lookup already found a 4xx there.

**Landed on a fourth, simpler than all three**: don't cache 4xx at all, full stop — reverting to
shape 1, but for a different and better reason than the one shape 1 was rejected for. A 404's path
space is effectively unbounded (bots, scanners, typos), so almost none of it repeats; caching it
spends LRU slots on one-off traffic that would otherwise hold a reusable `/search` response, for a
hit rate close to zero. That argument doesn't depend on the auth question at all — it would have
been true before `_raise_not_found` ever varied by caller. Once 4xx isn't cached, the leak/mask
hazard (and all three shapes' machinery for it) stops being something to get right and becomes
inapplicable: nothing auth-dependent is ever in the cache to leak or be masked.

One exception, added after landing: 400 is cacheable after all. Unlike 404's unbounded path space,
a 400 (a query parameter that fails type coercion, e.g. `ParamCoercionError`) is a deterministic
function of the request shape already in the cache key, never varies by caller, and a client
retrying the same malformed request genuinely repeats the same key — the "almost none of it
repeats" argument that rules out 404 doesn't hold for it. Extracted as `status_is_cacheable()` so
the exception has one place to live rather than a growing `startswith` chain inline.

## Staging

Each step is independently shippable and testable:

0. **Delete `get_stats`.** No caller, no test, nothing to migrate — a standalone PR that needs none
   of the machinery below, and it retires one unauthenticated worker-exhaustion route on its own. The
   natural place to start: it is the smallest possible demonstration that the route table is carrying
   handlers nobody asked for.
1. **Extract `Router`.** Pure refactor, no behavior change, existing tests pass untouched. Lands the
   explicit-registration API without moving anything.
2. **Create `api/admin_resource.py`; move the 22 methods; mount at `_admin`.** Only two production
   crossings to rewrite (`api/api_resource.py:670-671` → `self.admin.…`); see
   [call-site impact](#call-site-impact). Reclassify `read_sql` and `get_migrations` here rather than
   moving them — `read_sql` to `db_utils`, since a public route needs it too.
3. **Delist.** `advertise=False` on the admin router; make the public listing opt-in.
4. **Auth at the mount. Done** (2026-08-21) as HTTP Basic Auth rather than the header-token design
   above — see [the addendum](#decided-instead-2026-08-21-http-basic-auth-not-a-bespoke-header--indistinguishable-404).
5. *(Optional, later)* Context object per option (b).

## Call-site impact

Recounted 2026-07-27 against the route set above. **119 call sites total**, but the count that
matters is not test-vs-production — it is whether a site **crosses the boundary**. An admin method
calling another admin method is unaffected, because both ends move together.

### Production code: 30 sites, 5 crossings

Of 29 sites in `api/api_resource.py` plus 1 in `scripts/`, only **five** cross:

| Site | Caller → callee | Change |
| --- | --- | --- |
| `:670`, `:671` | `__init__` (public) → `setup_schema`, `import_data` | `self.admin.…` |
| `:1842` | `get_common_keywords` (public) → `read_sql` | follows the `read_sql` reclassification |
| `:1867`, `:2005` | `backfill_*` (admin) → `read_sql` | same |
| `scripts/bench_random.py:44` | `api._import_recent = lambda: True` | must target the child — see monkeypatching below |

The other **24 are intra-admin and need no edit at all**: `_run_import_under_lock` → `_import_recent`
/ `setup_schema` / `_upsert_cards` / `backfill_*` / `_clear_caches`, `ingest_cubecobra` →
`_fetch_cubecobra_data` / `_insert_cubecobra_data`, `import_all_is_tags` →
`discover_is_tags_from_syntax` / `_add_is_tag_to_*`, `import_card_by_name` →
`import_cards_by_search` → `_scryfall_search` / `_upsert_cards`, and `_upsert_cards` →
`setup_schema` / `_clear_caches`.

This corrects an earlier claim in this doc that the in-process callers were `__init__` plus "two
internal call sites (`:1093`, `:2513`)". Those two are `_run_import_under_lock` → `setup_schema` and
`_upsert_cards` → `setup_schema` — both intra-admin, so neither needs changing. The real crossings are
the five above, three of which are `read_sql` and therefore step 2's reclassification rather than the
move itself.

Also note `api/middlewares/caching_middleware.py:75` mentions `APIResource._clear_caches()` in a
comment about work not yet wired up. Not a call site, but it will read as stale after the move.

### Tests: 89 sites across 12 files

| File | Sites |
| --- | --- |
| `test_upsert_cards.py` | 25 |
| `test_cubecobra_and_prefer_scores.py` | 15 |
| `test_import_card_by_name.py` | 12 |
| `test_api_resource.py` | 11 |
| `test_import_cards_by_search.py`, `test_read_sql.py` | 7 each |
| `test_integration_testcontainers.py` | 5 |
| `conftest.py`, `test_tagging.py` | 2 each |
| `test_card_ordering.py`, `test_prefer_order.py`, `test_type_conversion.py` | 1 each |

Most become `api.admin.<method>()`. Three cases need different handling, which is the reason the
count matters more than its size:

- **Monkeypatched attributes (4 here, 5 counting `scripts/bench_random.py:44`):**
  `api._import_recent = lambda: True` in `conftest.py:40`, `test_card_ordering.py:18`,
  `test_prefer_order.py:26`, `test_integration_testcontainers.py:120`. These patch the attribute on
  the instance rather than calling it, so they must target the child
  (`api.admin._import_recent = …`). **This is the one failure mode that is silent:** patching the
  parent after the move leaves the real `_import_recent` in place, so the fixture stops suppressing
  the import and the tests still pass while doing real Scryfall work. Worth converting to
  `monkeypatch.setattr` so a wrong target raises.
- **Class-level references (2):** `test_tagging.py:64,68` assert on `APIResource.import_oracle_tags`
  / `.import_art_tags`; they become `AdminResource.…`.
- **`read_sql` (11, in `test_read_sql.py` and `test_api_resource.py`):** these follow the
  reclassification, not the move, and two of them assert on `read_sql.cache`, so wherever it lands has
  to keep the memoization.

Eight of the 89 are bare `assert callable(self.api_resource.<name>)` smoke checks
(`test_api_resource.py:264`, `test_import_card_by_name.py:43,48,53`,
`test_import_cards_by_search.py:29`, `test_read_sql.py:33`, `test_tagging.py:64,68`). The
expected-route-table test below supersedes them; deleting them is better than porting them.

Exposing `admin` as a non-underscore attribute is fine under explicit registration — and is precisely
the thing that would be unsafe under the current `dir()`-based scheme, which is the argument for doing
step 1 first.

Worth adding alongside: a test asserting the public route table equals an expected literal set. That
is the regression guard that would have caught this class of bug originally, and it is cheap.

## Decisions taken 2026-07-28

Recorded here so the open questions below are not re-litigated mid-implementation.

- **Mount at `/_admin`.** Kept over `/admin`: security-identical, since the mount must return the
  indistinguishable 404 either way, but `/admin` is among the most-scanned paths on the internet and
  would pull constant probe noise into the logs. Precedent for the prefix exists in API namespaces
  (Elasticsearch `_search`, CouchDB `_all_docs`), and `_root` is already a route key here.
- **`prefer_score_tuner` moves to the admin child.** It mutates server-global scoring weights, so it
  belongs behind the boundary. Consequence: `_serve_static_file` becomes shared again, taking the
  seam from 4 methods back to 5.
- **`get_migrations` loses its route entirely**; `db_utils.get_migrations()` stays for in-process use.
  Nothing calls it over HTTP.

### What the recomputed closure says (2026-07-28)

Re-derived from the AST against current `main`, after #783, #787, #789, #791:

- **11 admin routes and 11 admin-only helpers — exactly as documented.**
- **Shared methods are 4, not 5**, with `prefer_score_tuner` public: `_reload_engine`, `_run_query`,
  `_set_statement_timeout`, `_setup_complete`. Moving the tuner adds `_serve_static_file` back.
- Test call sites are 74, not 89 — #783 removed the 11 `read_sql` ones.
- The silent monkeypatch failure is already pre-empted: #783 added `api/tests/support.py:override_attr`,
  which raises when the target has moved. **`scripts/bench_search.py:45` is not covered** — it still
  does a raw `api._import_recent = lambda: True` and will silently stop suppressing after the move.

### Premises that have since changed

- **Step 0 and the `read_sql` reclassification already shipped** in #783, along with the test
  hardening. Step 1's substance shipped in #789 as the `@route` marker rather than a `Router` class.
- **`path.replace(".", "_")` no longer exists** (#791), so the dispatch sketch above needs rewriting
  and the dotted-path spoofing note no longer describes the mechanism — though the test it suggests
  is still worth having.
- **Every method is no longer bound** (#789): only `GET`/`HEAD` reach a handler, so the
  `POST /import_data` framing is narrowed. `GET /setup_schema` still works, so this is a narrowing,
  not a fix.

## Open questions

- Does anything outside the repo call these endpoints over HTTP? Nothing in `makefile`, `scripts/`,
  or `client/` does (`scripts/bench_random.py` touches `_import_recent`, but in-process, not over
  HTTP), and the in-process callers are enumerated above — but a personal cron or shell
  history is not visible from here. Worth confirming before step 4 makes them unreachable.
- ~~Should `/_admin` be reachable from the public interface at all, or only bound to loopback with
  admin work done via `docker compose exec`?~~ **Resolved 2026-08-21**: yes, reachable, gated by
  Basic Auth — see [the addendum](#decided-instead-2026-08-21-http-basic-auth-not-a-bespoke-header--indistinguishable-404).
- ~~`prefer_score_tuner` serves a static HTML tuning page (via `_serve_static_file`). It is a
  developer tool rather than a data-management route — admin child, or delete it?~~ Stale: answered
  by "Decisions taken 2026-07-28" above (moved to the admin child) and confirmed in
  `EXPECTED_ADMIN_ROUTES` (`api/tests/test_admin_mount.py`).

## Related

Part of the July 2026 review; its scope and the verified-clean list are in
[reference-security-review-2026-07.md](./reference-security-review-2026-07.md).
