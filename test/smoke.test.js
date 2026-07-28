'use strict';

/**
 * Offline smoke tests — no Google or Quo credentials needed.
 * Run with: npm test
 */

const test = require('node:test');
const assert = require('node:assert/strict');

process.env.OWNTRACKS_SECRET = 'test-secret';
process.env.DATA_DIR = require('node:path').join(require('node:os').tmpdir(), 'dispatch-test');
process.env.GEOFENCE_METERS = '100';

const { app, extractPhone, normalizePhone, haversineMeters, humanizeDuration, _state } = require('../server.js');

// A destination and two driver positions: one far away, one inside the geofence.
const DEST = { lat: 33.7743, lng: -117.9375, formattedAddress: 'Garden Grove, CA' };
const FAR = { lat: 33.8358, lng: -117.9143 };  // ~7 km out
const CLOSE = { lat: 33.77436, lng: -117.93744 }; // ~8 m out

let server;
let base;

test.before(async () => {
  await new Promise((resolve) => {
    server = app.listen(0, resolve);
  });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => server?.close());

function seedSession(token) {
  _state.sessions.set(token, {
    token,
    eventId: `evt-${token}`,
    eventSummary: 'Test appointment',
    clientPhone: '+15555550123',
    destination: DEST,
    appointmentStart: new Date(Date.now() + 3600e3).toISOString(),
    status: 'active',
    createdAt: new Date().toISOString(),
    eta: null,
    lastEtaAt: Date.now(), // suppress the Distance Matrix call
    smsStatus: 'sent',
  });
}

function postLocation(pos, query = '?token=test-secret') {
  return fetch(`${base}/api/location${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      _type: 'location',
      lat: pos.lat,
      lon: pos.lng,
      acc: 12,
      vel: 40,
      batt: 88,
      tid: 'dt',
      tst: Math.floor(Date.now() / 1000),
    }),
  });
}

test('phone numbers are pulled out of calendar notes and normalised', () => {
  assert.equal(extractPhone('Client: (714) 555-0142, gate code 4412'), '+17145550142');
  assert.equal(extractPhone('<p>Call&nbsp;714-555-0142<br/>before arrival</p>'), '+17145550142');
  assert.equal(extractPhone('reach me at +44 20 7946 0958'), '+442079460958');
  assert.equal(extractPhone('no digits here'), null);
  assert.equal(extractPhone('zip 92840 only'), null);
  assert.equal(normalizePhone('17145550142'), '+17145550142');
});

test('haversine and duration helpers', () => {
  assert.ok(haversineMeters(DEST.lat, DEST.lng, CLOSE.lat, CLOSE.lng) < 100);
  assert.ok(haversineMeters(DEST.lat, DEST.lng, FAR.lat, FAR.lng) > 5000);
  assert.equal(humanizeDuration(90), '2 min');
  assert.equal(humanizeDuration(4500), '1 hr 15 min');
});

test('/api/location rejects an unauthenticated post', async () => {
  const res = await postLocation(FAR, '');
  assert.equal(res.status, 401);
});

test('/api/location accepts an authenticated fix and exposes it to the tracking page', async () => {
  seedSession('tok-active');

  const res = await postLocation(FAR);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), []); // OwnTracks expects an array back

  const state = await (await fetch(`${base}/api/track/tok-active`)).json();
  assert.equal(state.status, 'active');
  assert.equal(state.driver.lat, FAR.lat);
  assert.equal(state.driver.speedKph, 40);
  assert.equal(state.destination.lat, DEST.lat);

  const page = await fetch(`${base}/t/tok-active`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Live tracking/);
});

test('non-location OwnTracks messages are acknowledged and ignored', async () => {
  const res = await fetch(`${base}/api/location?token=test-secret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ _type: 'transition', event: 'enter', desc: 'home' }),
  });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), []);
});

test('crossing the 100 m geofence expires the link and serves the arrived page', async () => {
  seedSession('tok-geofence');

  await postLocation(CLOSE);

  const state = await (await fetch(`${base}/api/track/tok-geofence`)).json();
  assert.equal(state.status, 'arrived');
  assert.ok(state.arrivedAt);
  // The driver's position must no longer be readable once the session is over.
  assert.equal(state.driver, undefined);
  assert.equal(state.destination, undefined);

  const page = await fetch(`${base}/t/tok-geofence`);
  assert.equal(page.status, 410);
  assert.match(await page.text(), /Driver has arrived/);
});

test('an unknown token never reveals a session', async () => {
  const state = await (await fetch(`${base}/api/track/nope`)).json();
  assert.equal(state.status, 'not_found');

  const page = await fetch(`${base}/t/nope`);
  assert.equal(page.status, 404);
});

test('admin routes require the admin token', async () => {
  const res = await fetch(`${base}/api/admin/sessions`);
  assert.equal(res.status, 503); // ADMIN_TOKEN unset in this test run
});
