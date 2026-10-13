# AGENTS.md

## Purpose

This repository tracks employee attendance through a Telegram bot and a Django management panel. Preserve the security, privacy, and accounting boundaries below in every change.

## Architecture

- `config/`: Django settings, URL routing, ASGI/WSGI, and Celery bootstrap.
- `apps/accounts`: email-only admin/manager identities and MFA enforcement.
- `apps/organization`: company, teams, employees, workplaces, numbered workplace shifts, schedules, holidays, and invitations.
- `apps/attendance`: shifts, location evidence, correction requests, geofence math, and transactional attendance services.
- `apps/leave_management`: leave types, balances, requests, and approval services.
- `apps/incidents`: employee issue/emergency records.
- `apps/supervisor_documents`: supervisor document records, immutable file versions, validation, naming, and reupload-window rules.
- `apps/reporting`: scoped queries, daily splitting, exports, dashboard, and web-panel views.
- `apps/botapp`: aiogram dispatcher, bilingual handlers, webhook, long polling, and webhook setup.
- `apps/auditlog`: append-only security/business audit events.
- `apps/notifications`: scheduled reminders and outbound Telegram notifications.

Views and bot handlers must call domain services for state changes. Do not duplicate attendance, leave, authorization, or reporting rules in an interface.

## Required commands

```bash
uv sync --extra dev
uv run ruff check apps config tests manage.py
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest
```

Use `UV_CACHE_DIR=/tmp/management-bot-uv-cache` in restricted environments. Run `compilemessages` after changing translations.

## Invariants

- Store instants in UTC. Derive each workplace's IANA timezone from its coordinates, snapshot it on attendance rows, and use it for display and local date boundaries. `Europe/Chisinau` remains the company fallback. Always use timezone-aware datetimes and test DST/overnight behavior.
- An employee has at most one open shift. Check-in/out and approvals require a database transaction and row locks where concurrent requests can race.
- A bot check-in must include an active numbered shift belonging to the geofence-matched workplace. Historical rows may have a null shift only for backward compatibility.
- Employee categories are `operations` and `supervisor`. Existing employees default to operations. Supervisors never start/end attendance shifts or receive attendance statistics/corrections in the bot; operations workers never receive supervisor document actions.
- A supervisor document is the stable, initially timestamped record used for filtering. Its uploaded files are immutable versions. Daily timesheets are unique per workplace/day, monthly inventories per workplace/month, and delivery-note numbers per workplace/day. Never overwrite or delete an earlier version.
- Supervisor document downloads are authenticated private-media responses. Validate both extension and file signature, enforce the size limit, preserve workplace authorization, and keep uploads in private media included in backups.
- The company supervisor reupload window uses workplace-local calendar dates. `0` disables reuploads; positive `N` allows ages `0 <= age < N`. Admins and managers may change it from the Supervisor documents page.
- Telegram identity is the numeric user ID. Usernames are mutable display metadata, never authorization keys. A Telegram ID is unique among active employees; archiving releases it for a new active employee, and reactivation must fail while another active employee owns it.
- A location must be recent, sufficiently accurate, inside an assigned active workplace, and retained only according to policy.
- Never silently auto-close a shift or fabricate time. Use reminders and correction requests.
- Employees and referenced configuration are archived, not deleted.
- Audit events are append-only. Never log secrets, passwords, invitation tokens, precise coordinates, or rate values in audit metadata.
- `hourly_rate` is optional informational data. Never multiply it by attendance, derive compensation, or add salary/payroll concepts.
- Admins and managers may edit informational rates. Employees never receive rates. Standard attendance and vacation exports never contain rates; only the explicit employee export does.
- Managers have company-wide operational access to employees, teams, workplaces, holidays, attendance, leave, incidents, and exports. Manager-account administration, the Django settings area, and the audit log remain admin-only and must be blocked server-side.
- Account registration, email changes, and forgotten-password recovery are disabled. Admins create managers and replace their passwords. Keep logout as a direct POST action and expose password/MFA management through the dedicated Security page.
- MFA may temporarily be activated without a verified email (`MFA_ALLOW_UNVERIFIED_EMAIL=True`). Enrollment is one-time: users cannot deactivate, replace, or regenerate MFA credentials. Recovery codes are displayed once only. Only a company admin may perform an audited, complete reset of a manager's authenticators, after which the manager must enroll again.
- PostgreSQL is authoritative. Redis data must remain disposable. Celery tasks must be idempotent or safe to retry.

## Development conventions

- Target Python 3.13 and Django 5.2 LTS; keep dependency ranges in `pyproject.toml` and commit `uv.lock`.
- Prefer small service functions with explicit domain errors. Validate at both service and database levels.
- Add a migration for every model change. Never rewrite an applied migration.
- Add English and Russian strings together. Bot text belongs in `apps/botapp/texts.py`; web strings use Django gettext.
- Prefer accessible server-rendered HTML. Maintain responsive behavior and clear loading, empty, success, and error states.
- Exports are private, requester-bound, scoped at generation time, and expired after 24 hours.
- Add tests for permissions, concurrency-sensitive transitions, timezone boundaries, export leakage, and bot idempotency whenever related code changes.

## Delivery checklist

- No secrets or production data in Git.
- New settings appear in `.env.example` and README when operators must configure them.
- `check`, migration drift, lint, and tests pass.
- Authorization is tested with admin, manager, anonymous user, and employee bot identity as applicable. Tests must preserve the three admin-only areas: Managers, Settings, and Audit Log.
- Operational changes include upgrade, rollback, backup, and monitoring implications.
