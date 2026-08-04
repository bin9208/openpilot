import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = globalThis;
await import("../js/realtime/mini_hud_model.js");
await import("../js/realtime/vision_compact.js");
await import("../js/realtime/raw_capnp.js");

const { build, sourceDiagnostic } = globalThis.CarrotMiniHudModel;
const LEGACY_CARROT_MAN_FRAME = Uint8Array.from(Buffer.from(
  "Q1ZTMQUAEQACAAAAUAAAAAEAAAA8AAAAQQEAAAcAAAANAAAA3gAAAAUAAAAEAHR1cm4LAGxlZ2FjeS1yb2FkBQByaWdodDcAAAAAABZCAAD+QgAAtEIAAJBCAgAAANIEAADIAQAAAwBjYW0AAAMAY2Ft",
  "base64",
));

function appendCompactText(parts, value) {
  const encoded = new TextEncoder().encode(value);
  const length = new Uint8Array(2);
  new DataView(length.buffer).setUint16(0, encoded.length, true);
  parts.push(length, encoded);
}

function carrotManCompactFixture(sequence) {
  const parts = [LEGACY_CARROT_MAN_FRAME];
  appendCompactText(parts, "naver_v1");
  appendCompactText(parts, "session");
  const scalars = new Uint8Array(16);
  const view = new DataView(scalars.buffer);
  view.setBigUint64(0, sequence, true);
  view.setInt32(8, 1250, true);
  view.setInt32(12, 375, true);
  parts.push(scalars);
  appendCompactText(parts, "guiding");
  parts.push(Uint8Array.of(1));
  appendCompactText(parts, "");
  appendCompactText(parts, "naver_v1");
  appendCompactText(parts, "cam");
  return Uint8Array.from(Buffer.concat(parts.map((part) => Buffer.from(part))));
}

function carrotManRawFixture({ legacy = false, sequence = 42n } = {}) {
  // Layout values come from capnp's CodeGeneratorRequest for custom.CarrotMan.
  // Event is the checked-in one-pointer union envelope used by raw_capnp.js.
  const dataWords = legacy ? 10 : 13;
  const pointerCount = legacy ? 9 : 15;
  const words = new Uint8Array(8 + 128 * 8);
  const view = new DataView(words.buffer);
  const segmentBase = 8;
  const writeWord = (word, lo, hi) => {
    view.setUint32(segmentBase + word * 8, lo >>> 0, true);
    view.setUint32(segmentBase + word * 8 + 4, hi >>> 0, true);
  };
  const writeStructPointer = (word, offset, dataCount, pointerCountValue) => {
    writeWord(word, (offset << 2) >>> 0, (dataCount | (pointerCountValue << 16)) >>> 0);
  };

  writeStructPointer(0, 0, 2, 1);
  view.setBigUint64(segmentBase + 8, 987654321n, true);
  view.setUint16(segmentBase + 16, 105, true);
  writeStructPointer(3, 0, dataWords, pointerCount);
  const payloadWord = 4;
  const payloadBase = segmentBase + payloadWord * 8;
  view.setInt32(payloadBase, 2, true);

  let nextWord = payloadWord + dataWords + pointerCount;
  const writeText = (slot, value) => {
    const encoded = new TextEncoder().encode(`${value}\0`);
    const pointerWord = payloadWord + dataWords + slot;
    const relative = nextWord - (pointerWord + 1);
    writeWord(pointerWord, ((relative << 2) | 1) >>> 0, ((encoded.length << 3) | 2) >>> 0);
    words.set(encoded, segmentBase + nextWord * 8);
    nextWord += Math.ceil(encoded.length / 8);
  };

  writeText(4, legacy ? "cam" : "route");
  if (!legacy) {
    view.setBigUint64(payloadBase + 10 * 8, sequence, true);
    view.setInt32(payloadBase + 22 * 4, 1250, true);
    view.setInt32(payloadBase + 23 * 4, 375, true);
    view.setUint8(payloadBase + Math.floor(768 / 8), 1);
    writeText(9, "naver_v1");
    writeText(10, "session-42");
    writeText(11, "guiding");
    writeText(12, "");
    writeText(13, "naver_v1");
    writeText(14, "cam");
  }

  view.setUint32(0, 0, true);
  view.setUint32(4, nextWord, true);
  return words.subarray(0, segmentBase + nextWord * 8);
}

test("navigation owner and deceleration provider remain independent", () => {
  assert.deepEqual(
    sourceDiagnostic({ activeCarrot: 2, naviOwner: "naver_v1", decelProvider: "hda", decelReason: "cam" }),
    { guidance: "naver", deceleration: "hda", reason: "cam" },
  );
  assert.deepEqual(
    sourceDiagnostic({ activeCarrot: 3, naviOwner: "tmap_legacy", decelProvider: "tmap_legacy", decelReason: "section" }),
    { guidance: "tmap", deceleration: "tmap", reason: "section" },
  );
  assert.deepEqual(
    sourceDiagnostic({ activeCarrot: 0, naviOwner: "", decelProvider: "hda", decelReason: "hda" }),
    { guidance: "none", deceleration: "hda", reason: "hda" },
  );
});

test("compact HUD preserves naver provider and camera reason separately", () => {
  const model = build(
    { isMetric: true, vEgoKph: 80, vSetKph: 100 },
    {
      carrotMan: {
        activeCarrot: 2,
        desiredSpeed: 60,
        desiredSource: "route",
        naviOwner: "naver_v1",
        decelProvider: "naver_v1",
        decelReason: "cam",
      },
    },
    null,
  );

  assert.equal(model.source, "naver");
  assert.deepEqual(model.sourceDiagnostic, { guidance: "naver", deceleration: "naver", reason: "cam" });
  assert.equal(model.temp.label, "naver:cam");
});

test("compact HUD preserves carrot navi provider and bump reason separately", () => {
  const model = build(
    { isMetric: true, vEgoKph: 50, vSetKph: 80 },
    {
      carrotMan: {
        activeCarrot: 2,
        desiredSpeed: 30,
        desiredSource: "bump",
        naviOwner: "carrot_navi_v2",
        decelProvider: "carrot_navi_v2",
        decelReason: "bump",
      },
    },
    null,
  );

  assert.equal(model.source, "v2");
  assert.deepEqual(model.sourceDiagnostic, { guidance: "v2", deceleration: "v2", reason: "bump" });
  assert.equal(model.temp.label, "v2:bump");
});

test("old logs retain the generic navigation fallback without invented providers", () => {
  const legacy = { activeCarrot: 3, desiredSpeed: 55, desiredSource: "cam" };

  assert.deepEqual(sourceDiagnostic(legacy), { guidance: "nav", deceleration: "nav", reason: "none" });
  const model = build(
    { isMetric: true, vEgoKph: 80, vSetKph: 100 },
    { carrotMan: legacy },
    null,
  );
  assert.equal(model.source, "nav");
  assert.equal(model.temp.label, "cam");
});

test("legacy compact service 5 ends cleanly before appended diagnostics", () => {
  const frame = globalThis.CarrotVisionCompact.decodeFrame(LEGACY_CARROT_MAN_FRAME);

  assert.equal(frame.service, "carrotMan");
  assert.equal(frame.sequence, 17);
  assert.equal(frame.decoded.activeCarrot, 2);
  assert.equal(frame.decoded.desiredSource, "cam");
  assert.equal(Object.hasOwn(frame.decoded, "naviOwner"), false);
  assert.equal(Object.hasOwn(frame.decoded, "decelProvider"), false);
});

test("legacy compact compatibility does not accept truncation or trailing corruption", () => {
  assert.throws(
    () => globalThis.CarrotVisionCompact.decodeFrame(LEGACY_CARROT_MAN_FRAME.subarray(0, -1)),
    /truncated/,
  );
  const trailing = new Uint8Array(LEGACY_CARROT_MAN_FRAME.length + 1);
  trailing.set(LEGACY_CARROT_MAN_FRAME);
  trailing[trailing.length - 1] = 0xff;
  assert.throws(() => globalThis.CarrotVisionCompact.decodeFrame(trailing), /truncated|trailing/);
});

for (const [name, sequence] of [
  ["2^53 + 1", 9_007_199_254_740_993n],
  ["UInt64 max", 18_446_744_073_709_551_615n],
]) {
  test(`compact CarrotMan preserves naviSequence at ${name}`, () => {
    const frame = globalThis.CarrotVisionCompact.decodeFrame(carrotManCompactFixture(sequence));

    assert.equal(frame.decoded.naviSequence, sequence.toString());
    assert.doesNotThrow(() => JSON.stringify(frame.decoded));
  });
}

test("old raw CarrotMan stays bounded at its 10 data and 9 pointer words", () => {
  const event = globalThis.CarrotRawCapnp.decodeReplayEvent(carrotManRawFixture({ legacy: true }));

  assert.equal(event.service, "carrotMan");
  assert.equal(event.decoded.activeCarrot, 2);
  assert.equal(event.decoded.desiredSource, "cam");
  assert.deepEqual(
    {
      naviOwner: event.decoded.naviOwner,
      naviSessionId: event.decoded.naviSessionId,
      naviSequence: event.decoded.naviSequence,
      naviOwnerAgeMs: event.decoded.naviOwnerAgeMs,
      naviSafetyAgeMs: event.decoded.naviSafetyAgeMs,
      naviLifecycle: event.decoded.naviLifecycle,
      naviControlAllowed: event.decoded.naviControlAllowed,
      naviSafetyRejection: event.decoded.naviSafetyRejection,
      decelProvider: event.decoded.decelProvider,
      decelReason: event.decoded.decelReason,
    },
    {
      naviOwner: "",
      naviSessionId: "",
      naviSequence: null,
      naviOwnerAgeMs: null,
      naviSafetyAgeMs: null,
      naviLifecycle: "",
      naviControlAllowed: null,
      naviSafetyRejection: "",
      decelProvider: "",
      decelReason: "",
    },
  );
  assert.equal(globalThis.CarrotRawCapnp.deriveHudPayload({ carrotMan: event.decoded }).apm, "APN");
});

test("raw CarrotMan fixture decodes every appended diagnostic field", () => {
  const event = globalThis.CarrotRawCapnp.decodeReplayEvent(carrotManRawFixture());

  assert.deepEqual(
    {
      naviOwner: event.decoded.naviOwner,
      naviSessionId: event.decoded.naviSessionId,
      naviSequence: event.decoded.naviSequence,
      naviOwnerAgeMs: event.decoded.naviOwnerAgeMs,
      naviSafetyAgeMs: event.decoded.naviSafetyAgeMs,
      naviLifecycle: event.decoded.naviLifecycle,
      naviControlAllowed: event.decoded.naviControlAllowed,
      naviSafetyRejection: event.decoded.naviSafetyRejection,
      decelProvider: event.decoded.decelProvider,
      decelReason: event.decoded.decelReason,
    },
    {
      naviOwner: "naver_v1",
      naviSessionId: "session-42",
      naviSequence: "42",
      naviOwnerAgeMs: 1250,
      naviSafetyAgeMs: 375,
      naviLifecycle: "guiding",
      naviControlAllowed: true,
      naviSafetyRejection: "",
      decelProvider: "naver_v1",
      decelReason: "cam",
    },
  );
});

for (const [name, sequence] of [
  ["2^53 + 1", 9_007_199_254_740_993n],
  ["UInt64 max", 18_446_744_073_709_551_615n],
]) {
  test(`raw CarrotMan preserves naviSequence at ${name}`, () => {
    const event = globalThis.CarrotRawCapnp.decodeReplayEvent(carrotManRawFixture({ sequence }));

    assert.equal(event.decoded.naviSequence, sequence.toString());
    assert.doesNotThrow(() => JSON.stringify(event.decoded));
  });
}
