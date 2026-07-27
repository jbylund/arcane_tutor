# 500 Responses Returned Stack Frames and Local Variables

**Severity: medium (latent, never reachable from outside). Found 2026-07-26. Fixed 2026-07-27 in
[#782](https://github.com/jbylund/sylvan_librarian/pull/782).**

The unhandled-exception branch in `_handle` caught every exception, walked `inspect.trace()`, and
serialized each frame into the `HTTPInternalServerError` description — file, function, line, and every
local that `error_monitoring.can_serialize` accepted:

```python
"locals": {k: v for k, v in iframe.frame.f_locals.items() if error_monitoring.can_serialize(v)},
```

## Latent, not live

This could not be triggered from outside. `/search?limit=abc`, malformed UTF-8 in `q`, negative
`num_cards`, and a bogus `shape` all returned clean 400s or 200s — the validation in front of it was
doing its job, and that is worth stating plainly rather than recording this as an active leak.

What made it worth fixing anyway is that the protection was entirely upstream of the handler. The
disclosure was one unvalidated code path away, the frames in scope at a throw site inside `_run_query`
or the import path include connection and query state, and nothing about the handler would have flagged
the regression. The severity was a property of the validation, not of the handler.

## What shipped

Frames and locals still go to the log and the error monitor; the client gets a fixed string:

```json
{"title": "Server Error", "description": "An internal error occurred."}
```

One wrinkle worth recording, because the obvious version of this fix is wrong: the frame list was
**never actually logged**. It was built solely to be serialized into the response. Deleting the
construction along with the description — the natural reading of "stop returning it" — would have
dropped the locals entirely, since `exc_info=True` carries file, function, and line but not locals, and
a deployment without `HONEYBADGER_API_KEY` has nowhere else to read them. So the fix moved the list to
a `logger.error` call rather than removing it.

`INTERNAL_ERROR_DESCRIPTION` is a plain string rather than a dict, so there is no structured slot for a
future caller to append detail into.

### The test is the durable half

Nothing upstream of the handler would catch a regression here, so the assertion belongs on the
response. `test_handle_500_body_carries_no_frame_data` plants a local at the throw site and asserts the
rendered body carries neither its name, its value, nor any frame key.

It was verified by reverting the handler to the previous form and confirming the test **fails**, with
the planted value visible in the body. A test of this shape that has never been seen to fail is not
evidence of anything.

## Related

Part of the July 2026 review; its scope and verified-clean list are in the review notes, tracked out of
tree per [the `security-` convention](../README.md#unfixed-security-findings).

The sibling `TypeError` branch still returns `str(oops)` on a 400, which can expose a handler
signature. Left as is: it is caller error, and the names it reveals are public API parameters.
