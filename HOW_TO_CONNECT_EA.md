# Connecting the EA to the live web app

## 1. Deploy the backend somewhere reachable
`localhost` only works if the backend runs on the exact same PC as MT5.
For the EA to check in from wherever it actually trades, deploy `backend/app.py`
(a Flask app) to a small always-on host — e.g. Render, Railway, or a VPS.

**Database (PostgreSQL):** this backend now requires a real PostgreSQL database
instead of a local file, so your member data survives restarts/redeploys.
Render and Railway both offer a free/cheap managed Postgres — create one first,
then copy its connection string.

Set these environment variables on your host:
- `DATABASE_URL` — your Postgres connection string, e.g.
  `postgresql://user:password@host:5432/dbname` (Render/Railway give you this
  directly when you create the database — just paste it in).
- `ADMIN_API_KEY` — a secret you choose; keep it private, it's the admin password.
- `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` — for OTP/notification emails.
- `ADMIN_EMAIL` — where new-registration/payment alerts go.
- `PORT` — usually set automatically by the host.

Once deployed you'll have a URL like `https://yourapp.onrender.com`.

**Checking the database connection:** open
`https://yourapp.onrender.com/api/admin/db-check` and send your `X-Admin-Key`
header (or use `curl -H "X-Admin-Key: yoursecret" https://yourapp.onrender.com/api/admin/db-check`).
- `{"ok":true,"connected":true,...}` with a Postgres version and table counts means everything's wired up correctly.
- An error message here tells you exactly what's wrong (missing `DATABASE_URL`, wrong password, unreachable host, etc.) — use this first if anything doesn't seem to work after deploying.

## 2. Point the frontend at it
Open the dashboard (`frontend/index.html`), and in the "API URL" box at the top,
enter your deployed backend URL instead of `http://localhost:8000`.

## 3. Register and get a license key
On the dashboard: Register → verify the emailed OTP → wait for admin approval
(admin approves via the Admin tab using `ADMIN_API_KEY`) → Login. After login,
a green box shows your **License Key** and MT5 account — copy both.

## 4. Set the member's tier
Admin tab → "Set Membership" → enter the member's email, pick a tier, and
optionally an expiry date (ISO format, e.g. `2027-01-01T00:00:00+00:00`;
leave blank for no expiry / LIFETIME). This takes effect on the EA's next
check-in — no reinstall needed.

## 5. Configure the EA
In the EA's inputs (right-click chart → Expert Advisors → Properties):
- `UseBackendLicenseCheck` = **true**
- `BackendVerifyURL` = `https://yourapp.onrender.com/api/mt5/verify`
- `MT5AccountForLicense` = leave blank (it uses the terminal's own account number) or set explicitly
- `LicenseKey` = the key copied from the dashboard
- `LicenseCheckIntervalMinutes` = how often it re-checks (30 is a reasonable default)

## 6. Whitelist the URL in MT5
Tools → Options → Expert Advisors → tick "Allow WebRequest for listed URL" and
add your backend's domain (e.g. `https://yourapp.onrender.com`).

## What changes once this is live
- Membership tier, expiry, and wallet balance are now read from the server on
  every check-in, not from EA inputs a member could edit themselves.
- If the backend explicitly says the account is inactive/suspended/rejected,
  the EA blocks trading immediately.
- If the backend is briefly unreachable (network hiccup), the EA keeps the
  last known good membership state rather than shutting down — logged to the
  Experts tab either way.
- `UseBackendLicenseCheck = false` keeps the old behavior (static inputs) —
  useful for your own testing before rolling this out to members.
