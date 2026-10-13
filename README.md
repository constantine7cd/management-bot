# Management Bot

Production-oriented employee attendance for Telegram, with a responsive Django management panel. Employees check in and out using a fresh Telegram location; managers review attendance, leave, corrections, incidents, and exports.

Rates are informational employee data only. The system contains no payroll, salary, tax, or `rate × hours` calculation.

## What is included

- English/Russian Telegram interface and language switching in the web panel.
- Expiring one-use employee invitation links bound to immutable Telegram user IDs.
- Telegram IDs are unique among active employees. Archiving preserves the old binding for history but releases the ID for a new active employee.
- Multi-workplace geofencing with configurable radius and acceptable GPS accuracy.
- Numbered daily shifts per workplace; employees select the relevant shift before sharing location.
- Two employee categories: **Operations worker** for attendance workflows and **Shift supervisor** for workplace document collection.
- Supervisor uploads for daily XLS/XLSX timesheets, numbered PDF/JPG/PNG delivery notes with goods descriptions, and monthly XLS/XLSX inventories. Reuploads retain immutable version history.
- Transactional check-in/out, one-open-shift constraint, duplicate Telegram update protection, and location evidence retention.
- Weekly, monthly, and custom-range employee statistics.
- Automatic IANA timezone detection per workplace (including seasonal DST), schedules, overnight shifts, country holiday calendars, leave balances, approvals, and timestamp corrections.
- Company-wide operational access for admins/managers, informational MDL rates, TOTP MFA, audit events, incidents, emergency manager alerts, dedicated attendance/vacation CSV/XLSX exports, and dashboards.
- Docker Compose, PostgreSQL, Redis, Celery, Caddy TLS, health endpoints, CI, and encrypted off-site backup scripts.

## Local setup

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker Desktop (for PostgreSQL/Redis), and a bot created through Telegram's `@BotFather` if bot testing is needed.

1. Clone the repository and enter it.

   ```bash
   git clone <repository-url> management-bot
   cd management-bot
   ```

2. Install dependencies and create the environment file.

   ```bash
   make setup
   ```

3. Edit `.env`. For a host-based development server, either start PostgreSQL/Redis and adjust their URLs, or use SQLite temporarily:

   ```env
   DJANGO_SETTINGS_MODULE=config.settings.local
   DATABASE_URL=sqlite:///db.sqlite3
   REDIS_URL=redis://localhost:6379/0
   COMPANY_TIME_ZONE=Europe/Chisinau
   ```

4. Apply migrations and create the initial company/admin/default leave types:

   ```bash
   make migrate
   uv run python manage.py bootstrap --email admin@example.com --company "Example SRL" --password 'change-this-now'
   ```

5. Start Django:

   ```bash
   make run
   ```

   Open <http://127.0.0.1:8000>. Admins use **Managers** to create manager accounts. Both admins and managers can manage **Teams**, **Employees**, **Workplaces**, **Holidays**, attendance, leave, incidents, and exports. Workplace timezones are detected from coordinates. Enable TOTP from the account menu before deploying.

6. Optional Telegram bot setup:

   - Put the token and bot username in `.env` as `BOT_TOKEN` and `BOT_USERNAME`.
   - Assign an employee to at least one workplace.
   - Choose **Operations worker** or **Shift supervisor** on the employee form. Existing employees default to Operations worker.
   - Configure at least one numbered daily shift for that workplace; otherwise the bot intentionally refuses check-in.
   - From the Employees page, generate an invitation and open it from Telegram.
   - Run long polling in another terminal:

   ```bash
   make bot
   ```

7. Start background jobs when testing reminders/exports:

   ```bash
   make worker
   ```

For an all-container local environment, set `DATABASE_URL=postgresql://management:management@db:5432/management` and `REDIS_URL=redis://redis:6379/0`, then run:

```bash
docker compose up --build -d db redis web worker beat
docker compose exec web python manage.py migrate
docker compose exec web python manage.py bootstrap --email admin@example.com --company "Example SRL" --password 'change-this-now'
docker compose --profile local-bot up -d bot
```

## Routine development

```bash
make test                         # test suite
make lint                         # Ruff and mypy
uv run python manage.py check    # Django checks
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py makemessages -l ru
uv run python manage.py compilemessages
```

Never edit an applied migration. Add a new migration and test both upgrade and fresh installation paths.

## Production VPS deployment

The recommended baseline is an Ubuntu LTS VPS with at least 2 vCPU, 4 GB RAM, a DNS record, Docker Engine/Compose, SSH keys, a firewall allowing only SSH/HTTP/HTTPS, and automatic security updates.

1. Clone the repository into a dedicated non-root service account.
2. Copy `.env.example` to `.env` and set strong unique values. At minimum set:
   - `DJANGO_SETTINGS_MODULE=config.settings.production`
   - a 50+ character `DJANGO_SECRET_KEY`
   - `DOMAIN`, `PUBLIC_BASE_URL`, `ALLOWED_HOSTS`, and `CSRF_TRUSTED_ORIGINS`
   - a strong PostgreSQL password and matching `DATABASE_URL`
   - `BOT_TOKEN`, `BOT_USERNAME`, and a random webhook secret containing only letters, numbers, `_`, or `-`
   - SMTP settings and optionally `SENTRY_DSN`
3. Ensure PostgreSQL and Redis are not published to the host. The production override exposes only Caddy on ports 80/443.
4. Build, migrate, bootstrap, collect static files, and configure Telegram:

   ```bash
   docker compose -f compose.yaml -f compose.production.yaml up --build -d db redis web worker beat caddy
   docker compose -f compose.yaml -f compose.production.yaml exec web python manage.py migrate --noinput
   docker compose -f compose.yaml -f compose.production.yaml exec web python manage.py bootstrap --email admin@example.com --company "Example SRL" --password 'use-a-strong-unique-password'
   docker compose -f compose.yaml -f compose.production.yaml exec web python manage.py setwebhook
   ```

   Self-service registration, email changes, and forgotten-password recovery are intentionally disabled. An administrator creates manager accounts and sets replacement passwords when needed.

5. Verify `https://your-domain/health/live`, `https://your-domain/health/ready`, a real bot check-in/out, a leave approval, and an XLSX export.
6. Configure an uptime monitor for `/health/ready` and alerts for container restarts, Celery failures, disk usage, and backup failures.

For upgrades, take a backup, pull/build a versioned image, run migrations, restart services, and verify health. Roll back to the previous image; database rollback requires a tested compatible reverse migration or restore.

## Backups and restore

`deploy/backup.sh` creates a PostgreSQL custom-format dump, backs it up together with private media through encrypted restic storage, and retains 7 daily, 5 weekly, and 12 monthly snapshots. Install `postgresql-client` and `restic` on the backup runner, configure the `RESTIC_*` and S3 credentials from `.env.example`, initialize the repository once, then schedule the script daily with systemd or cron.

Do not rely on Docker volumes as backups. Monitor every backup run and test a restoration monthly on an isolated database:

```bash
restic snapshots
restic restore latest --target /tmp/management-restore
DATABASE_URL=postgresql://... sh deploy/restore.sh /tmp/management-restore/tmp/management-*.dump
uv run python manage.py check
```

The restore script uses `--clean`; only run it against the explicitly selected recovery database.

## Security and privacy notes

- Geofencing is a deterrent, not tamper-proof presence verification; device GPS can be spoofed.
- Incoming production webhooks require Telegram's secret header. Never put the bot token in a URL.
- Exact coordinates are removed after 90 days by default; workplace, distance, accuracy, and verification result remain.
- Attendance edits happen only through audited corrections. Employees are archived instead of deleted.
- Supervisor files are private media and are available only through authenticated panel downloads. The original upload timestamp drives document-library date filtering; list and bulk downloads use the latest version while the detail page retains every version.
- The supervisor reupload window is configured on **Supervisor documents** by an admin or manager. `0` disables reuploads; `2` permits the upload day and the next local calendar day. Filename format includes document date, type, workplace, optional daily delivery-note number, and version.
- Staff MFA is mandatory in production. Managers have company-wide operational access but cannot access manager-account administration, Django settings, or the audit log.
- Email verification is temporarily not required before MFA activation. MFA enrollment is one-time and recovery codes are displayed only once, so managers must store them securely. Users cannot deactivate or replace MFA themselves; if credentials are lost, an admin must use **Reset MFA** on the Managers page, after which the manager enrolls again.
- Rate fields must never appear in employee bot messages, standard attendance exports, dashboards, logs, or audit metadata.
- Incident uploads are limited to JPG, PNG, and PDF and to the global upload-size limit. Add malware scanning if untrusted document uploads are enabled broadly.
- Set a legally reviewed retention policy before production use.

## Troubleshooting

- **Bot does not answer:** verify `BOT_TOKEN`; in development ensure no webhook remains, and in production run `setwebhook` then inspect Telegram webhook info.
- **Location rejected:** verify workplace coordinates/radius, employee assignments, location age (maximum two minutes), and reported accuracy.
- **Export remains pending:** check Celery and Redis, then inspect the worker logs.
- **Supervisor cannot upload:** confirm that the employee category is Shift supervisor, the workplace is assigned and active, and the file matches the allowed type and 5 MB limit.
- **Static files fail:** rebuild the image or run `collectstatic`; production uses a manifest and fails closed on missing assets.
- **MFA redirect loop:** visit `/accounts/2fa/` directly and configure TOTP; local/test settings intentionally do not enforce it.

## License

MIT — see [LICENSE](LICENSE).
