import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.window = globalThis;
await import('../../openpilot/selfdrive/carrot/web/js/realtime/raw_capnp.js');
await import('../../openpilot/selfdrive/carrot/web/js/realtime/vision_compact.js');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const raw = globalThis.CarrotRawCapnp.decodeReplayEvent(Buffer.from(fixture.raw, 'base64'));
const compact = globalThis.CarrotVisionCompact.decodeFrame(Buffer.from(fixture.compact, 'base64'));
for (const [name, value] of Object.entries(fixture.expected)) {
  assert.deepEqual(raw.decoded[name], value, `raw Capnp field ${name}`);
  assert.deepEqual(compact.decoded[name], value, `compact field ${name}`);
}
