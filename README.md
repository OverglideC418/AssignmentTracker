# UniSync

UniSync is a private, ad-free assignment tracker designed to run on a Raspberry Pi. It imports assignment-like events from multiple iCal feeds, keeps completion state in SQLite, and remains usable on a phone when temporarily offline.

## Features

- Responsive dark-first assignment list with optional calendar view.
- VSCode Dark (default), Soft Light, Blue Gray Dark, and Colored Dark themes.
- Daily server-side iCal refresh plus manual sync.
- Per-calendar colors and filtering rules with an import preview.
- Offline completion changes queued in IndexedDB and synchronized when reconnected.
- Custom tasks with due dates and notes.
- Single-user password authentication and persistent SQLite storage.

## Run with Docker

The recommended Raspberry Pi installation uses Docker Compose.

```sh
git clone <your-repository-url> unysync
cd unysync
cp .env.example .env
docker compose up -d --build
```

To run the same checks directly on the Raspberry Pi, use the included preflight script:

```sh
chmod +x scripts/pi-preflight.sh
./scripts/pi-preflight.sh
```

Repository CI also runs the backend tests, frontend production build, and Docker builds for both `linux/arm64` and `linux/arm/v7`, catching ARM-specific dependency failures before deployment.

Open `http://<raspberry-pi-ip>:8000` for the first-run password setup. For reliable installable PWA/offline behavior, access the app through an HTTPS Tailscale hostname. An HTTP address may still work as a normal website, but browsers can restrict service workers on insecure origins.

`.env` supports:

```dotenv
UNISYNC_TIMEZONE=America/Denver
UNISYNC_SECURE_COOKIE=1
```

Set `UNISYNC_SECURE_COOKIE=1` only when the app is accessed over HTTPS.

## Calendar feeds

Add a calendar in Settings. Feed URLs are stored only by the server and are not returned to the browser after creation. Treat a URL as private if it contains a course token or other access credential.

The importer uses stable iCal UIDs. It recognizes common assignment patterns such as `HW #... Upload`, `HW #... Report`, `RQuiz ...`, and `Statics X1/X2/X3`. Lecture topics, `No Class`, `Corrections`, `Student Ratings`, and other uncertain events are excluded or sent to the review preview. Add per-source include/exclude regular expressions for feeds with different conventions.

For ranged events, `DTEND` is treated as the real due date by design. The card displays the full `DTSTART–DTEND` range, and the end date controls weekly grouping/counts.

## Backups and updates

The database is stored in the `unysync-data` Docker volume. Create a backup before upgrading:

```sh
docker compose stop
docker run --rm --volumes-from unysync -v "$PWD":/backup alpine \
  tar czf /backup/unysync-data-$(date +%Y%m%d).tgz -C /app/data .
docker compose up -d --build
```

Restore by stopping the service and extracting an archive into `/app/data` through a temporary container using `--volumes-from unysync`; then start the service again. To update:

```sh
git pull
docker compose up -d --build
```

## Development

Backend:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app:app --reload
```

Frontend development requires Node.js 22 or newer:

```sh
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api` to `localhost:8000`.

Run backend tests from the repository root:

```sh
PYTHONPATH=backend python3 -m pytest backend/tests
```

## Privacy and security

UniSync has no advertising, analytics, or tracking. Keep the Raspberry Pi and Tailscale account secured, use HTTPS when possible, and never commit `.env`, database files, or private iCal URLs.
# AssignmentTracker
