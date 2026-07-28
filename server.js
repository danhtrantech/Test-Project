'use strict';

/**
 * Live Tracking & ETA dispatch server.
 *
 * Flow:
 *   Google Calendar (poll)  ->  find appointments starting in ~LEAD_MINUTES
 *                           ->  geocode the event location
 *                           ->  create a tracking session + unique link
 *                           ->  send the link to the client over SMS (Quo / OpenPhone)
 *   OwnTracks (phone)       ->  POST /api/location
 *                           ->  ETA recalculated via Google Distance Matrix
 *                           ->  pushed to the tracking page over SSE
 *                           ->  within GEOFENCE_METERS of the destination the
 *                               session is expired and the link stops working.
 */

require('dotenv').config();

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const express = require('express');
const cron = require('node-cron');
const { google } = require('googleapis');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

function num(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

const CONFIG = {
  port: num(process.env.PORT, 3000),

  // Public origin used to build the tracking link that goes out over SMS.
  // Render injects RENDER_EXTERNAL_URL automatically, so this usually needs no setup.
  publicBaseUrl: (process.env.PUBLIC_BASE_URL || process.env.RENDER_EXTERNAL_URL || '')
    .replace(/\/+$/, ''),

  // Google
  googleMapsApiKey: process.env.GOOGLE_MAPS_API_KEY || '',
  googleCalendarId: process.env.GOOGLE_CALENDAR_ID || '',
  googleServiceAccountJson: process.env.GOOGLE_SERVICE_ACCOUNT_JSON || '',

  // Quo (formerly OpenPhone)
  quoApiKey: process.env.QUO_API_KEY || '',
  quoFromNumber: process.env.QUO_FROM_NUMBER || '',
  quoApiBase: (process.env.QUO_API_BASE || 'https://api.quo.com/v1').replace(/\/+$/, ''),

  // OwnTracks shared secret. Accepted as ?token=, Bearer token, or Basic auth password.
  owntracksSecret: process.env.OWNTRACKS_SECRET || '',

  // Protects /api/admin/*
  adminToken: process.env.ADMIN_TOKEN || '',

  // Behaviour
  leadMinutes: num(process.env.LEAD_MINUTES, 60),
  geofenceMeters: num(process.env.GEOFENCE_METERS, 100),
  etaMinIntervalMs: num(process.env.ETA_MIN_INTERVAL_SECONDS, 25) * 1000,
  sessionMaxAgeMinutes: num(process.env.SESSION_MAX_AGE_MINUTES, 180),
  defaultCountryCode: process.env.DEFAULT_COUNTRY_CODE || '+1',
  timezone: process.env.TIMEZONE || 'UTC',
  smsTemplate:
    process.env.SMS_TEMPLATE ||
    'I will be arriving soon. Track my live location and ETA here: {{link}}',

  dataDir: process.env.DATA_DIR || path.join(__dirname, 'data'),

  // Set DRY_RUN=true to run the whole pipeline without actually sending SMS.
  dryRun: String(process.env.DRY_RUN || '').toLowerCase() === 'true',
};

const STATE_FILE = path.join(CONFIG.dataDir, 'state.json');

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function log(...args) {
  console.log(`[${new Date().toISOString()}]`, ...args);
}

function logError(...args) {
  console.error(`[${new Date().toISOString()}]`, ...args);
}

/**
 * The calendar is polled every minute, so a malformed event would otherwise
 * repeat the same warning sixty times before its appointment. Say it once.
 */
const warnedEvents = new Set();
function warnOnce(key, message) {
  if (warnedEvents.has(key)) return;
  warnedEvents.add(key);
  logError(message);
}

function newToken() {
  return crypto.randomBytes(16).toString('hex');
}

/** Constant-time string compare that tolerates length differences. */
function safeEqual(a, b) {
  const bufA = Buffer.from(String(a));
  const bufB = Buffer.from(String(b));
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

/** Great-circle distance in metres. */
function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

function humanizeDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const mins = Math.max(1, Math.round(seconds / 60));
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
}

function fetchJson(url, options = {}, timeoutMs = 15000) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) }).then(async (res) => {
    const text = await res.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    return { ok: res.ok, status: res.status, body };
  });
}

/**
 * Pull a phone number out of free-form calendar text and normalise it to E.164.
 * Google Calendar descriptions may contain HTML, so tags are stripped first.
 */
function extractPhone(...texts) {
  for (const raw of texts) {
    if (!raw) continue;
    const plain = String(raw)
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/gi, ' ');

    const candidates = plain.match(/\+?\d[\d\s().-]{7,}\d/g) || [];
    for (const candidate of candidates) {
      const normalized = normalizePhone(candidate);
      if (normalized) return normalized;
    }
  }
  return null;
}

function normalizePhone(input) {
  if (!input) return null;
  const trimmed = String(input).trim();
  const hasPlus = trimmed.startsWith('+');
  const digits = trimmed.replace(/\D/g, '');

  if (hasPlus) {
    return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
  }
  if (digits.length === 10) return `${CONFIG.defaultCountryCode}${digits}`;
  if (digits.length === 11 && digits.startsWith('1')) return `+${digits}`;
  if (digits.length >= 11 && digits.length <= 15) return `+${digits}`;
  return null;
}

// ---------------------------------------------------------------------------
// State store — in-memory, mirrored to disk so a restart does not re-send SMS
// ---------------------------------------------------------------------------

const state = {
  /** token -> session */
  sessions: new Map(),
  /** calendar event id -> token, so an event is only ever dispatched once */
  dispatchedEvents: new Map(),
  /** most recent OwnTracks fix */
  driver: null,
};

let persistTimer = null;

function loadState() {
  try {
    if (!fs.existsSync(STATE_FILE)) return;
    const parsed = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    for (const session of parsed.sessions || []) {
      state.sessions.set(session.token, session);
      if (session.eventId) state.dispatchedEvents.set(session.eventId, session.token);
    }
    state.driver = parsed.driver || null;
    log(`Restored ${state.sessions.size} session(s) from ${STATE_FILE}`);
  } catch (err) {
    logError('Could not restore state:', err.message);
  }
}

function persistState() {
  if (persistTimer) return;
  persistTimer = setTimeout(writeStateNow, 500);
}

function writeStateNow() {
  clearTimeout(persistTimer);
  persistTimer = null;
  try {
    fs.mkdirSync(CONFIG.dataDir, { recursive: true });
    const payload = {
      savedAt: new Date().toISOString(),
      sessions: [...state.sessions.values()],
      driver: state.driver,
    };
    // Write-then-rename so a crash mid-write cannot leave a truncated file behind.
    const tmp = `${STATE_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2));
    fs.renameSync(tmp, STATE_FILE);
  } catch (err) {
    logError('Could not persist state:', err.message);
  }
}

function activeSessions() {
  return [...state.sessions.values()].filter((s) => s.status === 'active');
}

/** Drop sessions that finished more than a day ago so the file cannot grow forever. */
function pruneSessions() {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  let removed = 0;
  for (const [token, session] of state.sessions) {
    const ended = Date.parse(session.endedAt || session.appointmentStart || session.createdAt);
    if (session.status !== 'active' && Number.isFinite(ended) && ended < cutoff) {
      state.sessions.delete(token);
      if (session.eventId) state.dispatchedEvents.delete(session.eventId);
      removed += 1;
    }
  }
  if (removed) {
    log(`Pruned ${removed} finished session(s)`);
    persistState();
  }
}

// ---------------------------------------------------------------------------
// Google Calendar
// ---------------------------------------------------------------------------

let calendarClient = null;

function getServiceAccountCredentials() {
  const raw = CONFIG.googleServiceAccountJson.trim();
  if (!raw) throw new Error('GOOGLE_SERVICE_ACCOUNT_JSON is not set');

  // Accept either the raw JSON key or a base64-encoded copy of it.
  const json = raw.startsWith('{') ? raw : Buffer.from(raw, 'base64').toString('utf8');
  const creds = JSON.parse(json);
  if (creds.private_key) creds.private_key = creds.private_key.replace(/\\n/g, '\n');
  return creds;
}

function getCalendarClient() {
  if (calendarClient) return calendarClient;
  const auth = new google.auth.GoogleAuth({
    credentials: getServiceAccountCredentials(),
    scopes: ['https://www.googleapis.com/auth/calendar.readonly'],
  });
  calendarClient = google.calendar({ version: 'v3', auth });
  return calendarClient;
}

async function listUpcomingEvents(lookaheadMinutes) {
  const calendar = getCalendarClient();
  const now = new Date();
  const timeMax = new Date(now.getTime() + lookaheadMinutes * 60 * 1000);

  const res = await calendar.events.list({
    calendarId: CONFIG.googleCalendarId,
    timeMin: now.toISOString(),
    timeMax: timeMax.toISOString(),
    singleEvents: true,
    orderBy: 'startTime',
    maxResults: 50,
  });
  return res.data.items || [];
}

// ---------------------------------------------------------------------------
// Google Maps — geocoding + Distance Matrix
// ---------------------------------------------------------------------------

const geocodeCache = new Map();

async function geocodeAddress(address) {
  const key = address.trim().toLowerCase();
  if (geocodeCache.has(key)) return geocodeCache.get(key);

  const url =
    'https://maps.googleapis.com/maps/api/geocode/json' +
    `?address=${encodeURIComponent(address)}&key=${encodeURIComponent(CONFIG.googleMapsApiKey)}`;

  const { body } = await fetchJson(url);
  if (!body || body.status !== 'OK' || !body.results?.length) {
    throw new Error(
      `Geocoding failed for "${address}": ${body?.status || 'no response'}` +
        (body?.error_message ? ` — ${body.error_message}` : '')
    );
  }

  const top = body.results[0];
  const result = {
    lat: top.geometry.location.lat,
    lng: top.geometry.location.lng,
    formattedAddress: top.formatted_address,
  };
  geocodeCache.set(key, result);
  return result;
}

/** Driving ETA with live traffic. Returns null when Google has no route. */
async function getDrivingEta(origin, destination) {
  const url =
    'https://maps.googleapis.com/maps/api/distancematrix/json' +
    `?origins=${origin.lat},${origin.lng}` +
    `&destinations=${destination.lat},${destination.lng}` +
    '&mode=driving&departure_time=now&traffic_model=best_guess' +
    `&key=${encodeURIComponent(CONFIG.googleMapsApiKey)}`;

  const { body } = await fetchJson(url);
  if (!body || body.status !== 'OK') {
    throw new Error(
      `Distance Matrix error: ${body?.status || 'no response'}` +
        (body?.error_message ? ` — ${body.error_message}` : '')
    );
  }

  const element = body.rows?.[0]?.elements?.[0];
  if (!element || element.status !== 'OK') {
    logError(`Distance Matrix element status: ${element?.status || 'missing'}`);
    return null;
  }

  // duration_in_traffic is only present when the key has traffic data for the route.
  const seconds = element.duration_in_traffic?.value ?? element.duration?.value;
  return {
    seconds,
    text: humanizeDuration(seconds),
    distanceMeters: element.distance?.value ?? null,
    distanceText: element.distance?.text ?? null,
    arrivalTime: new Date(Date.now() + seconds * 1000).toISOString(),
    computedAt: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Quo (OpenPhone) SMS
// ---------------------------------------------------------------------------

async function sendSms(to, content) {
  if (CONFIG.dryRun) {
    log(`[DRY_RUN] Would text ${to}: ${content}`);
    return { dryRun: true };
  }

  const { ok, status, body } = await fetchJson(`${CONFIG.quoApiBase}/messages`, {
    method: 'POST',
    headers: {
      // Quo/OpenPhone takes the raw API key — no "Bearer" prefix.
      Authorization: CONFIG.quoApiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      content,
      from: CONFIG.quoFromNumber,
      to: [to],
    }),
  });

  if (!ok) {
    throw new Error(`Quo API returned ${status}: ${JSON.stringify(body).slice(0, 400)}`);
  }
  return body;
}

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------

function trackingLink(token) {
  if (!CONFIG.publicBaseUrl) {
    throw new Error('PUBLIC_BASE_URL (or RENDER_EXTERNAL_URL) is not set — cannot build a link');
  }
  return `${CONFIG.publicBaseUrl}/t/${token}`;
}

/**
 * Everything the tracking page is allowed to see.
 * Once a session is over this deliberately drops the driver's location.
 */
function publicState(session) {
  if (!session || session.status !== 'active') {
    return {
      status: session ? session.status : 'not_found',
      arrivedAt: session?.arrivedAt || null,
    };
  }

  const driver = state.driver;
  return {
    status: 'active',
    destination: {
      lat: session.destination.lat,
      lng: session.destination.lng,
      address: session.destination.formattedAddress,
    },
    appointmentStart: session.appointmentStart,
    geofenceMeters: CONFIG.geofenceMeters,
    driver: driver
      ? {
          lat: driver.lat,
          lng: driver.lng,
          accuracy: driver.accuracy,
          speedKph: driver.speedKph,
          updatedAt: driver.updatedAt,
          ageSeconds: Math.round((Date.now() - Date.parse(driver.updatedAt)) / 1000),
        }
      : null,
    eta: session.eta || null,
  };
}

/** Poll the calendar and dispatch any appointment that has crossed the lead time. */
async function scanCalendarAndDispatch() {
  if (!CONFIG.googleCalendarId || !CONFIG.googleServiceAccountJson) {
    return { skipped: 'calendar not configured' };
  }

  // Look a little further ahead than the lead time so events are already loaded
  // by the time they become due.
  const events = await listUpcomingEvents(CONFIG.leadMinutes + 60);
  const results = [];

  for (const event of events) {
    // All-day events have `date` instead of `dateTime` — they have no arrival time.
    if (!event.start?.dateTime) continue;
    if (state.dispatchedEvents.has(event.id)) continue;

    const startsAt = new Date(event.start.dateTime);
    const minutesAway = (startsAt.getTime() - Date.now()) / 60000;
    if (minutesAway > CONFIG.leadMinutes || minutesAway <= 0) continue;

    const phone = extractPhone(event.description, event.summary);
    if (!phone) {
      warnOnce(event.id, `Event "${event.summary}" starts in ${Math.round(minutesAway)} min but has no phone number in its notes — skipping`);
      results.push({ eventId: event.id, skipped: 'no phone number in notes' });
      continue;
    }
    if (!event.location) {
      warnOnce(event.id, `Event "${event.summary}" has no location — skipping`);
      results.push({ eventId: event.id, skipped: 'no location' });
      continue;
    }

    try {
      const destination = await geocodeAddress(event.location);
      const token = newToken();
      // Built before anything is recorded, so a missing PUBLIC_BASE_URL leaves the
      // event untouched and retryable rather than marked as dispatched.
      const link = trackingLink(token);

      const session = {
        token,
        eventId: event.id,
        eventSummary: event.summary || 'Appointment',
        clientPhone: phone,
        destination: { ...destination, rawAddress: event.location },
        appointmentStart: startsAt.toISOString(),
        status: 'active',
        createdAt: new Date().toISOString(),
        eta: null,
        lastEtaAt: 0,
        smsStatus: 'pending',
      };

      state.sessions.set(token, session);
      // Recorded before the send so a crash mid-send cannot cause a duplicate text.
      state.dispatchedEvents.set(event.id, token);
      persistState();

      try {
        await sendSms(phone, CONFIG.smsTemplate.replace('{{link}}', link));
        session.smsStatus = 'sent';
        session.smsSentAt = new Date().toISOString();
        log(`Dispatched "${session.eventSummary}" -> ${phone} (${link})`);
        results.push({ eventId: event.id, dispatched: true, token });
      } catch (err) {
        session.smsStatus = 'failed';
        session.smsError = err.message;
        logError(`SMS failed for "${session.eventSummary}":`, err.message);
        results.push({ eventId: event.id, dispatched: false, error: err.message });
      }
      persistState();
    } catch (err) {
      logError(`Could not prepare event "${event.summary}":`, err.message);
      results.push({ eventId: event.id, error: err.message });
    }
  }

  return { checked: events.length, results };
}

function endSession(session, status) {
  session.status = status;
  session.endedAt = new Date().toISOString();
  if (status === 'arrived') session.arrivedAt = session.endedAt;
  persistState();
  broadcast(session.token, publicState(session));
  closeStream(session.token);
}

/** Expire anything that ran well past its appointment without a geofence hit. */
function expireStaleSessions() {
  const now = Date.now();
  for (const session of activeSessions()) {
    const start = Date.parse(session.appointmentStart);
    if (Number.isFinite(start) && now - start > CONFIG.sessionMaxAgeMinutes * 60 * 1000) {
      log(`Expiring stale session ${session.token} ("${session.eventSummary}")`);
      endSession(session, 'expired');
    }
  }
}

/**
 * Called on every OwnTracks fix: geofence first (free and instant), then refresh
 * the ETA for whatever is still running.
 */
async function onDriverLocation(fix) {
  state.driver = fix;
  persistState();

  for (const session of activeSessions()) {
    const distance = haversineMeters(
      fix.lat,
      fix.lng,
      session.destination.lat,
      session.destination.lng
    );

    if (distance <= CONFIG.geofenceMeters) {
      log(
        `Arrived at "${session.eventSummary}" (${Math.round(distance)} m) — expiring ${session.token}`
      );
      endSession(session, 'arrived');
      continue;
    }

    // Throttle Distance Matrix calls so a chatty phone cannot run up the bill.
    if (Date.now() - (session.lastEtaAt || 0) < CONFIG.etaMinIntervalMs) {
      broadcast(session.token, publicState(session));
      continue;
    }

    session.lastEtaAt = Date.now();
    try {
      const eta = await getDrivingEta({ lat: fix.lat, lng: fix.lng }, session.destination);
      if (eta) session.eta = eta;
    } catch (err) {
      logError(`ETA update failed for ${session.token}:`, err.message);
    }
    persistState();
    broadcast(session.token, publicState(session));
  }
}

// ---------------------------------------------------------------------------
// Server-sent events
// ---------------------------------------------------------------------------

/** token -> Set<res> */
const streams = new Map();

function addStream(token, res) {
  if (!streams.has(token)) streams.set(token, new Set());
  streams.get(token).add(res);
}

function removeStream(token, res) {
  const set = streams.get(token);
  if (!set) return;
  set.delete(res);
  if (!set.size) streams.delete(token);
}

function broadcast(token, payload) {
  const set = streams.get(token);
  if (!set) return;
  const frame = `data: ${JSON.stringify(payload)}\n\n`;
  for (const res of set) {
    try {
      res.write(frame);
    } catch {
      set.delete(res);
    }
  }
}

function closeStream(token) {
  const set = streams.get(token);
  if (!set) return;
  for (const res of set) {
    try {
      res.end();
    } catch {
      /* already gone */
    }
  }
  streams.delete(token);
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

const app = express();
app.set('trust proxy', 1);
app.use(express.json({ limit: '256kb' }));

app.get('/healthz', (req, res) => {
  res.json({
    ok: true,
    activeSessions: activeSessions().length,
    driverFixAgeSeconds: state.driver
      ? Math.round((Date.now() - Date.parse(state.driver.updatedAt)) / 1000)
      : null,
  });
});

app.get('/', (req, res) => {
  res.type('html').send(
    `<!doctype html><meta charset="utf-8"><title>Dispatch server</title>` +
      `<style>body{font:16px/1.6 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1.5rem;color:#111}code{background:#f1f1f4;padding:.15em .4em;border-radius:4px}</style>` +
      `<h1>Dispatch server is running</h1>` +
      `<p>${activeSessions().length} active tracking session(s).</p>` +
      `<p>Tracking links look like <code>/t/&lt;token&gt;</code> and are sent to clients by SMS one hour before their appointment.</p>`
  );
});

// --- OwnTracks ingest -------------------------------------------------------

function owntracksAuthorized(req) {
  if (!CONFIG.owntracksSecret) return true; // unset = open (not recommended)

  const bearer = (req.get('authorization') || '').match(/^Bearer\s+(.+)$/i);
  if (bearer && safeEqual(bearer[1], CONFIG.owntracksSecret)) return true;

  const basic = (req.get('authorization') || '').match(/^Basic\s+(.+)$/i);
  if (basic) {
    const decoded = Buffer.from(basic[1], 'base64').toString('utf8');
    const password = decoded.slice(decoded.indexOf(':') + 1);
    if (safeEqual(password, CONFIG.owntracksSecret)) return true;
  }

  const token = req.query.token || req.get('x-owntracks-token');
  return Boolean(token) && safeEqual(token, CONFIG.owntracksSecret);
}

app.post('/api/location', async (req, res) => {
  if (!owntracksAuthorized(req)) {
    return res.status(401).json({ error: 'unauthorized' });
  }

  const body = req.body || {};

  // OwnTracks also posts transition/waypoint/lwt messages. Acknowledge and ignore.
  if (body._type !== 'location') {
    return res.json([]);
  }

  const lat = Number(body.lat);
  const lng = Number(body.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return res.status(400).json({ error: 'lat/lon required' });
  }

  const fix = {
    lat,
    lng,
    accuracy: Number.isFinite(Number(body.acc)) ? Number(body.acc) : null,
    altitude: Number.isFinite(Number(body.alt)) ? Number(body.alt) : null,
    speedKph: Number.isFinite(Number(body.vel)) ? Number(body.vel) : null,
    battery: Number.isFinite(Number(body.batt)) ? Number(body.batt) : null,
    trackerId: body.tid || null,
    // `tst` is Unix seconds on the device clock; fall back to server time.
    updatedAt: Number.isFinite(Number(body.tst))
      ? new Date(Number(body.tst) * 1000).toISOString()
      : new Date().toISOString(),
    receivedAt: new Date().toISOString(),
  };

  try {
    await onDriverLocation(fix);
  } catch (err) {
    logError('Location handling failed:', err.message);
  }

  // OwnTracks expects a JSON array back (it may carry commands for the device).
  res.json([]);
});

// --- Tracking page ----------------------------------------------------------

app.get('/t/:token', (req, res) => {
  const session = state.sessions.get(req.params.token);
  if (!session || session.status !== 'active') {
    return res.status(session ? 410 : 404).sendFile(path.join(__dirname, 'public', 'arrived.html'));
  }
  res.sendFile(path.join(__dirname, 'public', 'track.html'));
});

app.get('/api/track/:token', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json(publicState(state.sessions.get(req.params.token)));
});

app.get('/api/track/:token/stream', (req, res) => {
  const { token } = req.params;
  const session = state.sessions.get(token);

  res.set({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no', // stop proxies from buffering the stream
  });
  res.flushHeaders();
  res.write(`data: ${JSON.stringify(publicState(session))}\n\n`);

  if (!session || session.status !== 'active') {
    return res.end();
  }

  addStream(token, res);
  const heartbeat = setInterval(() => res.write(': ping\n\n'), 20000);
  req.on('close', () => {
    clearInterval(heartbeat);
    removeStream(token, res);
  });
});

// --- Admin ------------------------------------------------------------------

function requireAdmin(req, res, next) {
  if (!CONFIG.adminToken) {
    return res.status(503).json({ error: 'ADMIN_TOKEN is not configured' });
  }
  const supplied = req.get('x-admin-token') || req.query.token || '';
  if (!safeEqual(supplied, CONFIG.adminToken)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  next();
}

app.get('/api/admin/sessions', requireAdmin, (req, res) => {
  res.json({
    driver: state.driver,
    sessions: [...state.sessions.values()].map((s) => ({
      ...s,
      link: CONFIG.publicBaseUrl ? `${CONFIG.publicBaseUrl}/t/${s.token}` : null,
    })),
  });
});

app.post('/api/admin/scan', requireAdmin, async (req, res) => {
  res.json(await scanCalendarAndDispatch());
});

/** One call that tells you whether every credential actually works. */
app.get('/api/admin/selftest', requireAdmin, async (req, res) => {
  const checks = {};

  checks.publicBaseUrl = CONFIG.publicBaseUrl
    ? { ok: true, value: CONFIG.publicBaseUrl }
    : { ok: false, error: 'PUBLIC_BASE_URL / RENDER_EXTERNAL_URL not set' };

  try {
    const events = await listUpcomingEvents(24 * 60);
    checks.googleCalendar = {
      ok: true,
      calendarId: CONFIG.googleCalendarId,
      eventsInNext24h: events.length,
      sample: events.slice(0, 3).map((e) => ({
        summary: e.summary,
        start: e.start?.dateTime || e.start?.date,
        location: e.location || null,
        phoneFound: Boolean(extractPhone(e.description, e.summary)),
      })),
    };
  } catch (err) {
    checks.googleCalendar = { ok: false, error: err.message };
  }

  try {
    const geo = await geocodeAddress('1600 Amphitheatre Parkway, Mountain View, CA');
    checks.googleGeocoding = { ok: true, resolved: geo.formattedAddress };
  } catch (err) {
    checks.googleGeocoding = { ok: false, error: err.message };
  }

  try {
    const eta = await getDrivingEta(
      { lat: 37.4221, lng: -122.0841 },
      { lat: 37.3861, lng: -122.0839 }
    );
    checks.googleDistanceMatrix = eta ? { ok: true, sampleEta: eta.text } : { ok: false, error: 'no route returned' };
  } catch (err) {
    checks.googleDistanceMatrix = { ok: false, error: err.message };
  }

  try {
    const { ok, status, body } = await fetchJson(`${CONFIG.quoApiBase}/phone-numbers`, {
      headers: { Authorization: CONFIG.quoApiKey },
    });
    checks.quo = ok
      ? {
          ok: true,
          fromNumber: CONFIG.quoFromNumber,
          numbersOnAccount: (body?.data || []).map((n) => n.number || n.formattedNumber).filter(Boolean),
        }
      : { ok: false, error: `HTTP ${status}: ${JSON.stringify(body).slice(0, 300)}` };
  } catch (err) {
    checks.quo = { ok: false, error: err.message };
  }

  const allOk = Object.values(checks).every((c) => c.ok);
  res.status(allOk ? 200 : 500).json({ ok: allOk, dryRun: CONFIG.dryRun, checks });
});

app.use((err, req, res, next) => {
  logError('Unhandled error:', err);
  res.status(500).json({ error: 'internal error' });
});

// ---------------------------------------------------------------------------
// Scheduled jobs
// ---------------------------------------------------------------------------

function startCronJobs() {
  // Every minute: anything that has just crossed the one-hour mark gets dispatched.
  cron.schedule(
    '* * * * *',
    async () => {
      try {
        await scanCalendarAndDispatch();
      } catch (err) {
        logError('Calendar scan failed:', err.message);
      }
    },
    { name: 'calendar-dispatch', noOverlap: true, timezone: CONFIG.timezone }
  );

  // Every five minutes: safety net for sessions that never got a geofence hit.
  cron.schedule(
    '*/5 * * * *',
    () => {
      expireStaleSessions();
      pruneSessions();
    },
    { name: 'session-maintenance', noOverlap: true, timezone: CONFIG.timezone }
  );
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

function warnAboutMissingConfig() {
  const required = {
    GOOGLE_MAPS_API_KEY: CONFIG.googleMapsApiKey,
    GOOGLE_CALENDAR_ID: CONFIG.googleCalendarId,
    GOOGLE_SERVICE_ACCOUNT_JSON: CONFIG.googleServiceAccountJson,
    QUO_API_KEY: CONFIG.quoApiKey,
    QUO_FROM_NUMBER: CONFIG.quoFromNumber,
    'PUBLIC_BASE_URL or RENDER_EXTERNAL_URL': CONFIG.publicBaseUrl,
  };
  const missing = Object.entries(required)
    .filter(([, value]) => !value)
    .map(([key]) => key);

  if (missing.length) {
    logError(`Missing configuration: ${missing.join(', ')}. See README.md.`);
  }
  if (!CONFIG.owntracksSecret) {
    logError('OWNTRACKS_SECRET is not set — /api/location is open to anyone. Set it.');
  }
}

loadState();
warnAboutMissingConfig();

if (require.main === module) {
  startCronJobs();
  const server = app.listen(CONFIG.port, () => {
    log(`Listening on :${CONFIG.port}`);
    log(`Lead time ${CONFIG.leadMinutes} min · geofence ${CONFIG.geofenceMeters} m` + (CONFIG.dryRun ? ' · DRY_RUN' : ''));
  });

  // Render sends SIGTERM on every deploy. Flush first, otherwise a debounced
  // write is lost and a redeployed server re-texts a client it already texted.
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, () => {
      log(`${signal} received — flushing state and shutting down`);
      writeStateNow();
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(0), 5000).unref();
    });
  }
}

module.exports = {
  app,
  CONFIG,
  extractPhone,
  normalizePhone,
  haversineMeters,
  humanizeDuration,
  // exposed for tests
  _state: state,
  _onDriverLocation: onDriverLocation,
};
