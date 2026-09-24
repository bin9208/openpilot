import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

test('mini HUD shows HDA reduction with no navigation owner', () => {
  const nodes = new Map();
  function node() {
    return { dataset: {}, style: { setProperty() {} }, classList: { toggle() {} },
      setAttribute() {}, addEventListener() {}, textContent: '', hidden: false };
  }
  const root = node();
  root.querySelector = selector => {
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  };
  const window = { addEventListener() {} };
  const document = { documentElement: { lang: 'en' }, getElementById: () => root,
    createElement: () => ({ getContext: () => ({ measureText: () => ({ width: 10 }) }) }) };
  const context = vm.createContext({ window, document, requestAnimationFrame: () => 1,
    localStorage: { getItem() { return null; }, setItem() {} }, setTimeout: () => 1 });
  for (const name of ['mini_hud_model.js', 'mini_hud.js']) {
    vm.runInContext(readFileSync(new URL(`../js/realtime/${name}`, import.meta.url), 'utf8'), context);
  }
  const model = window.CarrotMiniHudModel.build({ vSetKph: 80 }, { carrotMan: {
    naviLifecycle: 'idle', naviOwner: '', desiredSource: 'hda', decelProvider: 'hda', desiredSpeed: 40,
  }});
  window.CarrotMiniHud.render(model);
  assert.equal(nodes.get('[data-mini-hud-temp-speed]').textContent, '40');
  assert.equal(nodes.get('[data-mini-hud-temp-label]').textContent, 'HDA');
});
