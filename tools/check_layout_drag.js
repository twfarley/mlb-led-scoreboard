// Checks the per-extent move rules in layout.js.
//
// Most elements move with "x += dx, y += dy", but three do not: the divider
// carries a y_start/y_end span, the ABS challenge squares carry a list of y
// tops, and atbat.batter_stats is pinned to the board's right edge by the
// renderer and has no x at all. Getting any of those wrong silently corrupts a
// layout, so they are checked here.
//
// Run: node tools/check_layout_drag.js

const assert = require("assert");
const { state, applyDelta } = require("../config_editor_static/layout.js");

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ok    ${name}`);
  } catch (err) {
    failures++;
    console.log(`  FAIL  ${name}\n        ${err.message}`);
  }
}

function reset() {
  state.board = { width: 128, height: 64 };
  state.coords = {
    final: { inning: { x: 90, y: 45 } },
    atbat: { batter_stats: { y: 10, enabled: true } },
    inning: { break: { due_up: { divider: { x: 36, y_start: 32, y_end: 60 } } } },
    teams: { abs_challenges: { away: { x: 125, size: 3, squares: [32, 37] } } },
  };
}

const EL = {
  inning: { keypath: "final.inning", anchor: "center", extent: "text" },
  stats: { keypath: "atbat.batter_stats", anchor: "board-right", extent: "text" },
  divider: { keypath: "inning.break.due_up.divider", anchor: "left", extent: "vline" },
  squares: { keypath: "teams.abs_challenges.away", anchor: "left", extent: "squares" },
};

console.log("layout.js drag rules");

check("a normal element moves in both axes", () => {
  reset();
  assert.strictEqual(applyDelta(EL.inning, -5, 3), true);
  assert.deepStrictEqual(state.coords.final.inning, { x: 85, y: 48 });
});

check("a board-right element ignores horizontal movement", () => {
  reset();
  applyDelta(EL.stats, -20, 6);
  assert.strictEqual(state.coords.atbat.batter_stats.x, undefined, "must not gain an x");
  assert.strictEqual(state.coords.atbat.batter_stats.y, 16);
});

check("a divider keeps its span", () => {
  reset();
  applyDelta(EL.divider, 4, -10);
  const d = state.coords.inning.break.due_up.divider;
  assert.strictEqual(d.x, 40);
  assert.strictEqual(d.y_start, 22);
  assert.strictEqual(d.y_end, 50, "span of 28 must be preserved");
});

check("challenge squares move as a group", () => {
  reset();
  applyDelta(EL.squares, -3, 5);
  const s = state.coords.teams.abs_challenges.away;
  assert.strictEqual(s.x, 122);
  assert.deepStrictEqual(s.squares, [37, 42]);
});

check("movement is clamped to the board", () => {
  reset();
  applyDelta(EL.inning, -500, -500);
  assert.deepStrictEqual(state.coords.final.inning, { x: 0, y: 0 });
  reset();
  applyDelta(EL.inning, 500, 500);
  assert.deepStrictEqual(state.coords.final.inning, { x: 127, y: 63 });
});

check("a divider cannot be pushed off the bottom", () => {
  reset();
  applyDelta(EL.divider, 0, 500);
  const d = state.coords.inning.break.due_up.divider;
  assert.strictEqual(d.y_end, 63);
  assert.strictEqual(d.y_end - d.y_start, 28, "clamping must not squash the span");
});

check("squares cannot be pushed off the bottom", () => {
  reset();
  applyDelta(EL.squares, 0, 500);
  const s = state.coords.teams.abs_challenges.away;
  assert.strictEqual(Math.max(...s.squares) + s.size - 1, 63);
  assert.strictEqual(s.squares[1] - s.squares[0], 5, "spacing must be preserved");
});

check("a no-op drag reports no change", () => {
  reset();
  assert.strictEqual(applyDelta(EL.inning, 0, 0), false);
});

if (failures) {
  console.log(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log("\nall drag rules ok");
