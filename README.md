# Live Tracking & ETA Dispatch

An unattended dispatch server for a driving business. It watches your Google Calendar, and an hour before each appointment it texts the client a private link showing where you are on a map and how long until you get there. When your phone comes within 100 metres of the destination, the link dies and anyone who reloads it sees "Driver has arrived."

Nothing to press. Book the job in your calendar with the client's phone number in the notes, and the rest runs on its own.

## How the pieces fit

Your phone runs OwnTracks, a free open-source GPS app that posts your coordinates to `/api/location` every time you move. The server polls Google Calendar once a minute looking for appointments that have just crossed the one-hour mark, geocodes whatever address you typed into the event's Location field, mints a random 32-character token, and sends the client an SMS through the Quo (formerly OpenPhone) API. The tracking page holds an open connection to the server, so each new GPS fix pushes a fresh Google Distance Matrix ETA straight to the client's screen without them refreshing.

Arrival is measured locally with a haversine calculation on every incoming fix, which costs nothing and fires instantly. Once you're inside the radius the session flips to `arrived` and the API stops returning your location entirely, so an old link can't be used to follow you to your next job.

## What you'll need

Four accounts, and one of them costs money: a Google account (any Gmail works, no Workspace required), a Google Cloud project with billing enabled, a Quo/OpenPhone account on a plan that includes API access, and a Render account. Quo gates the API behind its Business tier, so check your plan before you get too far in. Render's free instances sleep after fifteen minutes of no traffic, which stops the cron job dead; you need the cheapest paid tier for this to work.

---

## Step 1 — Google Cloud: API key for maps

1. Open [console.cloud.google.com](https://console.cloud.google.com) and create a project. Call it something like `dispatch`.
2. Attach a billing account. Google won't serve the Maps APIs without one even while you're inside the free allowance.
3. Go to **APIs & Services → Library** and enable three APIs by name:
   - Geocoding API
   - Distance Matrix API
   - Google Calendar API
4. Go to **APIs & Services → Credentials → Create credentials → API key**. Copy the key; this is your `GOOGLE_MAPS_API_KEY`.
5. Click the key to edit it, and under **API restrictions** choose "Restrict key" and tick only Geocoding and Distance Matrix. Leave the application restriction on "None" for now. Render assigns outbound IPs that you can pin later under **Settings → Outbound IPs** if you want to lock it down further.

An unrestricted Maps key that leaks will be scraped and billed to you within hours, so don't skip the restriction.

## Step 2 — Google Cloud: service account for the calendar

The server reads your calendar as a robot user, which means no OAuth consent screen and no refresh token that quietly expires.

1. In the same project go to **IAM & Admin → Service Accounts → Create service account**. Name it `dispatch-reader`. Skip the optional role and permission screens; it needs no project-level roles at all.
2. Open the finished service account, go to the **Keys** tab, and pick **Add key → Create new key → JSON**. A `.json` file downloads.
3. Copy the service account's email address from the Details tab. It looks like `dispatch-reader@your-project.iam.gserviceaccount.com`.
4. Now open [calendar.google.com](https://calendar.google.com). Hover the calendar you book jobs in, click the three dots, and choose **Settings and sharing**.
5. Under **Share with specific people or groups**, add the service account email and give it **See all event details**. This is the step that grants access; without it the API returns 404 on your own calendar.
6. Scroll down to **Integrate calendar** and copy the **Calendar ID**. For your main calendar it's just your Gmail address. That's `GOOGLE_CALENDAR_ID`.

Open the downloaded JSON in a text editor and copy the whole thing. You'll paste it as one long line into `GOOGLE_SERVICE_ACCOUNT_JSON`. Render's environment variable fields handle the embedded newlines fine, but if yours mangles them, base64 the file instead (`base64 -w0 key.json`) and paste that; the server accepts either form.

## Step 3 — Quo (OpenPhone) API key

1. In the Quo web or desktop app open **Settings → Integrations → API**.
2. Generate a key and copy it. That's `QUO_API_KEY`.
3. Note the Quo phone number you want texts to come from, in E.164 format with the country code and no spaces, like `+17145550142`. That's `QUO_FROM_NUMBER`.

If your account still points at the old OpenPhone host, set `QUO_API_BASE=https://api.openphone.com/v1`. The default is `https://api.quo.com/v1`.

## Step 4 — Deploy to Render

Push this repo to GitHub first, then:

1. In Render pick **New → Web Service** and connect the repo.
2. Runtime **Node**, build command `npm ci`, start command `npm start`.
3. Choose a paid instance type. Free instances spin down when idle, and a sleeping server misses the one-hour trigger.
4. Add a persistent disk under **Advanced**: mount path `/var/data`, 1 GB is plenty. Set `DATA_DIR=/var/data` so tracking sessions survive a redeploy. Skip this and a deploy in the middle of someone's appointment will re-send them the same text.
5. Add the environment variables below.
6. Set the health check path to `/healthz`.

There's a `render.yaml` in the repo if you'd rather create it as a Blueprint and have the disk and variables set up for you.

You don't need to set `PUBLIC_BASE_URL` on Render, because Render injects `RENDER_EXTERNAL_URL` and the server falls back to it when building links.

### Environment variables

| Variable | Required | What it is |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | yes | Key from Step 1 |
| `GOOGLE_CALENDAR_ID` | yes | Usually your Gmail address |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes | Contents of the JSON key, raw or base64 |
| `QUO_API_KEY` | yes | Key from Step 3 |
| `QUO_FROM_NUMBER` | yes | Your Quo number, E.164 |
| `OWNTRACKS_SECRET` | yes | Any long random string you invent |
| `ADMIN_TOKEN` | yes | Another long random string, guards `/api/admin/*` |
| `PUBLIC_BASE_URL` | no | Only if you're not on Render |
| `QUO_API_BASE` | no | Defaults to `https://api.quo.com/v1` |
| `LEAD_MINUTES` | no | Defaults to 60 |
| `GEOFENCE_METERS` | no | Defaults to 100 |
| `ETA_MIN_INTERVAL_SECONDS` | no | Floor between Distance Matrix calls per session, default 25 |
| `SESSION_MAX_AGE_MINUTES` | no | Force-expiry after the appointment start, default 180 |
| `DEFAULT_COUNTRY_CODE` | no | Defaults to `+1` |
| `TIMEZONE` | no | For cron and log timestamps |
| `DATA_DIR` | no | Set to `/var/data` if you added a disk |
| `SMS_TEMPLATE` | no | `{{link}}` gets replaced with the tracking URL |
| `DRY_RUN` | no | `true` runs everything but sends no SMS |

For the two secrets, `openssl rand -hex 32` gives you something nobody will guess.

## Step 5 — OwnTracks on your phone

Install OwnTracks from the App Store or Play Store, then open **Preferences → Connection**.

- Set **Mode** to **HTTP** (not MQTT, which is the default on some builds).
- Set the **URL** to `https://your-app.onrender.com/api/location?token=YOUR_OWNTRACKS_SECRET`, using whatever you put in `OWNTRACKS_SECRET`.
- Under **Identification**, give the device a Device ID and a two-character Tracker ID. Any values work. If you'd rather not put the secret in the URL, leave the URL clean and put the secret in the **Password** field instead; the server accepts it either way.

Back in Preferences, set the reporting mode to **Move**. OwnTracks defaults to "Significant changes," which can sit quiet for ten minutes while you're on the freeway and makes the ETA look broken. Move mode drains more battery, so plug in on long days.

Hit the arrow icon to publish a fix manually, then check `https://your-app.onrender.com/healthz`. If `driverFixAgeSeconds` comes back as a number instead of `null`, your phone is talking to the server.

Android will ask for background location permission and then ask again to disable battery optimisation for OwnTracks. Say yes to both or the app gets killed mid-drive and your client watches a frozen dot.

## Step 6 — Check every credential at once

```
curl -H "x-admin-token: YOUR_ADMIN_TOKEN" https://your-app.onrender.com/api/admin/selftest
```

This reads your calendar, geocodes a known address, runs a sample Distance Matrix query, and asks Quo which numbers are on your account. It sends no texts. Each check reports `ok: true` or the exact error, so you can tell a wrong calendar share apart from a disabled API without reading the logs.

Once it's all green, set `DRY_RUN=true`, put a test event an hour out with your own mobile number in the notes, and watch the logs. The dispatch line prints the tracking link so you can open it yourself. Then set `DRY_RUN` back to `false`.

---

## Booking a job

Create a normal calendar event:

- **Location** — the client's address, written the way you'd type it into Google Maps. This gets geocoded, so a rough address works but a precise one geofences better.
- **Description** — the client's phone number anywhere in the notes. `(714) 555-0142`, `714-555-0142`, `+17145550142` and bare ten-digit numbers all parse. Gate codes and other text around it are fine.

All-day events are ignored, since they have no arrival time. An event with no location, or no findable phone number, gets skipped with a line in the logs explaining which.

Sixty minutes out, the client gets:

> I will be arriving soon. Track my live location and ETA here: https://your-app.onrender.com/t/a1b2c3…

## What the client sees

A dark full-screen map with your position as a green dot, their address as a red pin, and a trail behind you. The panel underneath shows the driving ETA in traffic, the distance left in miles, and a clock time for arrival. A status dot turns amber if your last fix is more than 90 seconds old and red past ten minutes, so a dead phone reads as "location paused" rather than a stale ETA they'd trust.

The page never shows your phone number, the client's number, or anything else from the calendar event.

## Endpoints

| Route | Purpose |
| --- | --- |
| `POST /api/location` | OwnTracks ingest. Needs the secret. Returns `[]`, which is what OwnTracks expects. |
| `GET /t/:token` | The client-facing page. Serves the arrived page once the session is over. |
| `GET /api/track/:token` | JSON state. Returns only a status once the session ends. |
| `GET /api/track/:token/stream` | Server-sent events feeding the live page. |
| `GET /healthz` | Uptime check. |
| `GET /api/admin/sessions` | Every session and its link. Needs `x-admin-token`. |
| `POST /api/admin/scan` | Force a calendar poll instead of waiting for the next minute. |
| `GET /api/admin/selftest` | Credential check described above. |

## Running it locally

```
npm install
cp .env.example .env      # fill it in
npm start
```

`npm test` runs offline smoke tests covering phone parsing, the geofence cutoff, OwnTracks authentication, and the check that an expired link stops returning your coordinates. No API keys needed for those.

To exercise the ingest path by hand:

```
curl -X POST "http://localhost:3000/api/location?token=YOUR_OWNTRACKS_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"_type":"location","lat":33.7743,"lon":-117.9375,"acc":10,"vel":45,"tst":1753670000}'
```

## Costs to watch

Distance Matrix is the only thing that scales with use. The server throttles to one call per session per 25 seconds, so an hour-long drive runs somewhere around 100 to 140 calls. Google gives a free monthly allowance per API that typically swallows a solo operator's volume, but pricing changes, so look at your billing dashboard after the first busy week rather than trusting a number in a README. If it's higher than you like, raise `ETA_MIN_INTERVAL_SECONDS` to 60; the map dot still moves at full speed, only the ETA number updates less often.

## When something doesn't work

**No text went out.** Look at the logs for the minute the event should have fired. A skipped event names its reason. If the SMS itself failed, the session still exists, and `/api/admin/sessions` shows `smsStatus: "failed"` with the error Quo returned.

**Calendar returns 404 or 403.** The service account isn't shared on the calendar, or `GOOGLE_CALENDAR_ID` is wrong. Re-check Step 2. Sharing a calendar takes a minute or two to propagate.

**ETA shows a dash.** Either no fix has arrived yet, or Distance Matrix has no driving route between you and the destination. The server logs the element status from Google in that case.

**The dot doesn't move.** OwnTracks is in the wrong reporting mode or the OS killed it. Check `/healthz` for the fix age before blaming the server.

**Link expired too early.** You passed within 100 m of the destination on the way there, maybe on a road that loops nearby. Raise `GEOFENCE_METERS`, or accept it; the ETA was near zero anyway.

**A client got the same text twice.** The server lost its session file, almost certainly because there's no persistent disk and Render redeployed. Add the disk and set `DATA_DIR`.

## A note on what this doesn't do

There's no database, no queue, and no retry on a failed SMS. Sessions live in memory and get mirrored to a JSON file, which is the right shape for one driver and a handful of jobs a day and the wrong shape for a fleet. If you add a second driver, the single global `state.driver` is the first thing that breaks: every active session reads the same last known position. Splitting that by OwnTracks Tracker ID and matching sessions to a driver is maybe an afternoon of work, but it isn't done here.
