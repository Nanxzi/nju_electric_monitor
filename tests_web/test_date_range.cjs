const {test} = require("node:test");
const assert = require("node:assert/strict");
const {selectDateRange} = require("../src/static/dashboard.js");

const readingsAt = (...times) => times.map((time) => ({time, timestamp: Date.parse(time)}));

test("custom range includes both complete Beijing calendar days", () => {
  const readings = readingsAt(
    "2026-09-30T23:59:59.999+08:00",
    "2026-10-01T00:00:00+08:00",
    "2026-10-02T23:59:59.999+08:00",
    "2026-10-03T00:00:00+08:00",
  );
  assert.deepEqual(selectDateRange(readings, "2026-10-01", "2026-10-02"), readings.slice(1, 3));
});

test("single-day selection uses Beijing time even for UTC observations", () => {
  const readings = readingsAt(
    "2026-10-01T15:59:59Z",
    "2026-10-01T16:00:00Z",
    "2026-10-02T15:59:59Z",
    "2026-10-02T16:00:00Z",
  );
  assert.deepEqual(selectDateRange(readings, "2026-10-02", "2026-10-02"), readings.slice(1, 3));
});

test("ranges can cross years and include leap days", () => {
  const readings = readingsAt("2025-12-31T23:59:59+08:00", "2026-01-01T00:00:00+08:00");
  assert.deepEqual(selectDateRange(readings, "2025-12-31", "2026-01-01"), readings);
  const leap = readingsAt("2024-02-29T12:00:00+08:00", "2024-03-01T00:00:00+08:00");
  assert.deepEqual(selectDateRange(leap, "2024-02-29", "2024-02-29"), leap.slice(0, 1));
});

test("missing, malformed, impossible and reversed dates are rejected", () => {
  for (const [start, end] of [
    ["", "2026-10-03"], [null, "2026-10-03"], ["2026-10-01", undefined],
    ["2026-10-01", "2026-2-3"], ["2026-02-29", "2026-03-01"],
    ["2026-04-31", "2026-05-01"], ["2026-13-01", "2026-13-02"],
    ["2026-10-04", "2026-10-03"], ["0000-01-01", "2026-10-03"],
  ]) assert.equal(selectDateRange([], start, end), null);
});

test("valid ranges with no observations have an empty result", () => {
  assert.deepEqual(selectDateRange([], "2026-10-01", "2026-10-03"), []);
  assert.deepEqual(selectDateRange(readingsAt("2026-09-01T12:00:00+08:00"), "2026-10-01", "2026-10-03"), []);
});
