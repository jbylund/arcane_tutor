# Changelog

All notable changes to this project are documented here. Versions follow
[CalVer](https://calver.org/) (`YYYY.MM.DD.PATCH`) rather than semantic versioning.

## [2026.08.21.0]

Initial release.

### Security

- Admin/data-management routes now require authentication (`/_admin`, HTTP Basic Auth) — set
  `ADMIN_PASSWORD` in `env.json` ([#961](https://github.com/jbylund/sylvan_librarian/pull/961),
  [#962](https://github.com/jbylund/sylvan_librarian/pull/962),
  [#963](https://github.com/jbylund/sylvan_librarian/pull/963),
  [#966](https://github.com/jbylund/sylvan_librarian/pull/966))
- API port bound to loopback by default
  ([#781](https://github.com/jbylund/sylvan_librarian/pull/781))
- Error responses no longer leak internal detail
  ([#782](https://github.com/jbylund/sylvan_librarian/pull/782))
