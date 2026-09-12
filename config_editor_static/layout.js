"use strict";

// Layout editor.
//
// The board image is rendered server-side by the real renderers and served at
// native resolution (one image pixel per LED); this scales it by an integer
// factor so a board pixel stays a crisp square. Bounding boxes are computed
// server-side too, beside the anchor metadata and font metrics -- see
// layout_preview.elements(). Nothing here re-derives where an element sits.
//
// Edits are held in a working copy and posted back for preview, so a drag shows
// on the real board before anything is written to disk.

const $ = (id) => (typeof document === "undefined" ? null : document.getElementById(id));

const state = {
  size: null,
  screen: null,
  zoom: "fit",
  elements: [],
  selected: null,
  board: { width: 0, height: 0 },
  coords: null, // working copy of coordinates/<size>.json
  saved: null, // last known on-disk state, for Revert
  dirty: false,
};

// Zoom must stay an INTEGER number of screen pixels per LED. A fractional
// scale resamples the preview and the grid stops lining up with the board,
// which would make coordinates -- and snapping -- untrustworthy.
function zoomFactor() {
  if (state.zoom !== "fit") return Number(state.zoom);

  const { width, height } = state.board;
  if (!width || !height) return 8;

  const wrap = $("stage-wrap");
  const availableWidth = (wrap ? wrap.clientWidth : window.innerWidth) - 8;
  const availableHeight = window.innerHeight - $("stage").getBoundingClientRect().top - 110;

  return Math.max(1, Math.min(Math.floor(availableWidth / width), Math.floor(availableHeight / height)));
}

// ── helpers ──────────────────────────────────────────────────────────────────

function banner(message, kind) {
  const el = $("banner");
  if (!message) {
    el.hidden = true;
    return;
  }
  el.textContent = message;
  el.className = kind === "ok" ? "banner ok" : "banner";
  el.hidden = false;
}

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node[k] = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
}

function nodeAt(doc, keypath) {
  let node = doc;
  for (const part of keypath.split(".")) {
    if (!node || typeof node !== "object") return null;
    node = node[part];
  }
  return node && typeof node === "object" ? node : null;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ── editing ──────────────────────────────────────────────────────────────────

// Applying a move is not simply "x += dx" for every element. Some carry their
// vertical position in keys other than `y`, and some have no `x` of their own.
function applyDelta(element, dx, dy) {
  const node = nodeAt(state.coords, element.keypath);
  if (!node) return false;
  const { width, height } = state.board;
  let changed = false;

  const moveX = (amount) => {
    if (node.x === undefined) return;
    const next = clamp(node.x + amount, 0, width - 1);
    if (next !== node.x) {
      node.x = next;
      changed = true;
    }
  };

  // atbat.batter_stats is pinned to the right edge of the board by the
  // renderer and has no x at all; only its row can move.
  if (element.anchor !== "board-right") moveX(dx);

  if (element.extent === "vline") {
    // A divider carries its span, not a single y.
    const span = node.y_end - node.y_start;
    const nextStart = clamp(node.y_start + dy, 0, height - 1 - span);
    if (nextStart !== node.y_start) {
      node.y_start = nextStart;
      node.y_end = nextStart + span;
      changed = true;
    }
  } else if (element.extent === "squares") {
    // The whole column of challenge squares moves together.
    const ys = node.squares;
    const lo = Math.min(...ys);
    const hi = Math.max(...ys) + node.size - 1;
    const shift = clamp(dy, -lo, height - 1 - hi);
    if (shift !== 0) {
      node.squares = ys.map((v) => v + shift);
      changed = true;
    }
  } else if (node.y !== undefined) {
    const next = clamp(node.y + dy, 0, height - 1);
    if (next !== node.y) {
      node.y = next;
      changed = true;
    }
  }
  return changed;
}

function markDirty() {
  state.dirty = JSON.stringify(state.coords) !== JSON.stringify(state.saved);
  $("dirty").hidden = !state.dirty;
  $("save").disabled = !state.dirty;
  $("revert").disabled = !state.dirty;
}

/** Re-render the board and recompute boxes from the working copy. */
let refreshToken = 0;
async function refresh() {
  const token = ++refreshToken;
  try {
    const data = await postJSON("/api/layout/elements", {
      size: state.size,
      screen: state.screen,
      coords: state.coords,
    });
    if (token !== refreshToken) return; // a newer edit already superseded this
    state.elements = data.elements;
  } catch (err) {
    banner(`Could not recompute boxes: ${err.message}`);
  }

  const res = await fetch("/api/layout/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      size: state.size,
      screen: state.screen,
      coords: state.coords,
      show_disabled: $("show-disabled").checked,
    }),
  });
  if (token !== refreshToken) return;
  if (res.ok) {
    const blob = await res.blob();
    const previous = $("preview").dataset.objectUrl;
    if (previous) URL.revokeObjectURL(previous);
    const url = URL.createObjectURL(blob);
    $("preview").dataset.objectUrl = url;
    $("preview").src = url;
  }
  paint();
  paintList();
  paintDetails();
}

// ── loading ──────────────────────────────────────────────────────────────────

async function boot() {
  let meta;
  try {
    meta = await getJSON("/api/layout/meta");
  } catch (err) {
    banner(`Could not load layout metadata: ${err.message}`);
    return;
  }

  const sizeSel = $("size");
  for (const size of meta.sizes) sizeSel.append(el("option", { value: size }, size));
  sizeSel.value = meta.sizes.includes("w128h64") ? "w128h64" : meta.sizes[0];

  const screenSel = $("screen");
  for (const [name, label] of Object.entries(meta.screens)) {
    screenSel.append(el("option", { value: name }, label));
  }
  screenSel.value = "live" in meta.screens ? "live" : Object.keys(meta.screens)[0];

  sizeSel.onchange = () => guardUnsaved(load);
  screenSel.onchange = () => load({ keepEdits: true });
  $("zoom").onchange = () => {
    state.zoom = $("zoom").value;
    localStorage.setItem("layoutZoom", state.zoom);
    paint();
  };
  $("show-boxes").onchange = paint;
  $("show-grid").onchange = paint;
  $("show-disabled").onchange = refresh;
  $("save").onclick = save;
  $("revert").onclick = revert;

  const savedZoom = localStorage.getItem("layoutZoom");
  if (savedZoom && [...$("zoom").options].some((o) => o.value === savedZoom)) {
    $("zoom").value = savedZoom;
  }
  state.zoom = $("zoom").value;

  window.addEventListener("resize", () => {
    if (state.zoom === "fit") paint();
  });
  window.addEventListener("beforeunload", (ev) => {
    if (state.dirty) ev.preventDefault();
  });
  document.addEventListener("keydown", onKeyDown);

  await load();
}

function guardUnsaved(fn) {
  if (state.dirty && !confirm("You have unsaved layout changes. Discard them?")) {
    $("size").value = state.size;
    return;
  }
  state.dirty = false;
  fn();
}

async function load(opts = {}) {
  state.size = $("size").value;
  state.screen = $("screen").value;
  state.selected = null;
  banner("");

  try {
    const data = await getJSON(`/api/layout/elements?size=${state.size}&screen=${state.screen}`);
    state.board = { width: data.width, height: data.height };
    state.saved = data.coords;
    // Switching screens must not throw away edits to the same board.
    if (!(opts.keepEdits && state.coords)) state.coords = JSON.parse(JSON.stringify(data.coords));
    state.elements = data.elements;
  } catch (err) {
    state.elements = [];
    banner(`Could not load elements: ${err.message}`);
    paint();
    return;
  }
  markDirty();
  await refresh();
}

async function save() {
  $("save").disabled = true;
  try {
    const result = await postJSON(`/api/save/coordinates/${state.size}`, state.coords);
    state.saved = JSON.parse(JSON.stringify(state.coords));
    markDirty();
    banner(`Saved ${result.written}${result.backup ? ` (backup: ${result.backup})` : ""}.`, "ok");
  } catch (err) {
    banner(`Save failed: ${err.message}`);
    $("save").disabled = false;
  }
}

async function revert() {
  state.coords = JSON.parse(JSON.stringify(state.saved));
  markDirty();
  banner("");
  await refresh();
}

// ── interaction ──────────────────────────────────────────────────────────────

function onKeyDown(ev) {
  if (!state.selected) return;
  if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
  const step = ev.shiftKey ? 10 : 1;
  const deltas = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  const delta = deltas[ev.key];
  if (!delta) return;
  ev.preventDefault();
  const element = state.elements.find((e) => e.keypath === state.selected);
  if (element && applyDelta(element, delta[0], delta[1])) {
    markDirty();
    refresh();
  }
}

/** Drag moves the box live; the board re-renders once, on drop. */
function startDrag(ev, element) {
  ev.preventDefault();
  ev.stopPropagation();
  select(element.keypath);

  const z = zoomFactor();
  const startX = ev.clientX;
  const startY = ev.clientY;
  const box = ev.currentTarget;
  const originLeft = parseFloat(box.style.left);
  const originTop = parseFloat(box.style.top);
  let dx = 0;
  let dy = 0;

  const onMove = (move) => {
    // Snap to whole board pixels: the coordinates are integers, so a drag that
    // reports fractions would be lying about where the element will land.
    dx = Math.round((move.clientX - startX) / z);
    dy = Math.round((move.clientY - startY) / z);
    box.style.left = `${originLeft + dx * z}px`;
    box.style.top = `${originTop + dy * z}px`;
    $("readout").textContent = `${dx >= 0 ? "+" : ""}${dx}, ${dy >= 0 ? "+" : ""}${dy}`;
  };

  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    if ((dx || dy) && applyDelta(element, dx, dy)) {
      markDirty();
      refresh();
    } else {
      paint();
    }
  };

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// ── painting ─────────────────────────────────────────────────────────────────

function paint() {
  const z = zoomFactor();
  const { width, height } = state.board;
  if (!width) return;

  const img = $("preview");
  img.width = width * z;
  img.height = height * z;

  const stage = $("stage");
  stage.style.width = `${width * z}px`;
  stage.style.height = `${height * z}px`;

  const grid = $("grid");
  grid.hidden = !$("show-grid").checked;
  grid.style.backgroundSize = `${z}px ${z}px`;

  const boxes = $("boxes");
  boxes.innerHTML = "";
  if (!$("show-boxes").checked) return;

  for (const element of state.elements) {
    if (!element.box) continue; // derived: nothing of its own to draw a box around
    const [x0, y0, x1, y1] = element.box;
    const classes = ["el"];
    if (element.dynamic) classes.push("nominal");
    if (element.enabled === false) classes.push("disabled");
    if (element.keypath === state.selected) classes.push("selected");

    const box = el("div", { class: classes.join(" "), title: element.keypath });
    box.style.left = `${x0 * z}px`;
    box.style.top = `${y0 * z}px`;
    box.style.width = `${(x1 - x0 + 1) * z}px`;
    box.style.height = `${(y1 - y0 + 1) * z}px`;
    box.onmousedown = (mev) => startDrag(mev, element);
    boxes.append(box);

    const { x, y } = element.coords;
    if (x !== undefined && y !== undefined) {
      const dot = el("div", { class: "anchor-dot" });
      dot.style.left = `${x * z}px`;
      dot.style.top = `${y * z}px`;
      dot.style.width = `${z}px`;
      dot.style.height = `${z}px`;
      boxes.append(dot);
    }
  }

  $("readout").textContent = `${width}×${height} · ${state.elements.length} elements · ${z}×`;
}

function paintList() {
  const list = $("list");
  list.innerHTML = "";
  for (const element of state.elements) {
    const classes = [];
    if (element.keypath === state.selected) classes.push("selected");
    if (element.enabled === false) classes.push("off");
    if (element.anchor === "derived") classes.push("derived");
    const where = element.anchor === "derived" ? "derived" : element.enabled === false ? "off" : element.anchor;
    const row = el(
      "li",
      { class: classes.join(" ") },
      el("span", { class: "key" }, element.keypath),
      el("span", { class: "where" }, where)
    );
    row.onclick = () => select(element.keypath);
    list.append(row);
  }
}

const ANCHOR_HELP = {
  left: "x is the left edge.",
  center: "x is the horizontal CENTRE — the renderer runs it through center_text_position, so the element spreads either side of it.",
  right: "x is the RIGHTMOST pixel — content is drawn right-to-left from it.",
  "board-right": "Pinned to the right edge of the board. This element has no x, so it can only move vertically.",
  none: "No x/y position; this is a column offset inside a list layout.",
};

// Numeric keys worth exposing as editable fields, in a sensible order.
const EDITABLE = ["x", "y", "width", "height", "size", "y_start", "y_end", "spacing", "offset"];

function paintDetails() {
  const box = $("details");
  const element = state.elements.find((e) => e.keypath === state.selected);
  if (!element) {
    box.className = "empty";
    box.textContent = "Select an element on the board, or from the list below.";
    return;
  }
  box.className = "";
  box.innerHTML = "";
  box.append(el("h3", {}, element.label ? `${element.label} — ${element.keypath}` : element.keypath));

  // A derived element is drawn from other elements' coordinates and has none of
  // its own, so there is nothing to drag and nothing to type into.
  if (element.anchor === "derived") {
    box.append(el("div", {}, el("span", { class: "tag warn" }, "derived — cannot be moved directly")));
    box.append(el("p", { class: "note" }, element.note || ""));

    if (element.toggle) {
      const owner = nodeAt(state.coords, element.toggle.keypath);
      if (owner && typeof owner[element.toggle.key] === "boolean") {
        const fields = el("div", { class: "fields" });
        const toggle = el("input", { type: "checkbox" });
        toggle.checked = owner[element.toggle.key];
        toggle.onchange = () => {
          owner[element.toggle.key] = toggle.checked;
          markDirty();
          refresh();
        };
        fields.append(el("label", { class: "check" }, el("span", {}, element.toggle.key), toggle));
        box.append(fields);
      }
    }

    if (element.controlled_by && element.controlled_by.length) {
      const links = el("p", { class: "readonly-note" }, "Move these instead: ");
      element.controlled_by.forEach((keypath, i) => {
        if (i) links.append(", ");
        const link = el("a", { href: "#", class: "xref" }, keypath);
        link.onclick = (ev) => {
          ev.preventDefault();
          select(keypath);
        };
        links.append(link);
      });
      box.append(links);
    }
    return;
  }

  const tags = el("div");
  tags.append(el("span", { class: "tag" }, element.anchor));
  tags.append(el("span", { class: "tag" }, element.extent));
  if (element.enabled === false) tags.append(el("span", { class: "tag warn" }, "disabled"));
  if (element.dynamic) tags.append(el("span", { class: "tag warn" }, "width varies"));
  box.append(tags);

  box.append(el("p", { class: "note" }, ANCHOR_HELP[element.anchor] || ""));
  if (element.note) box.append(el("p", { class: "note" }, element.note));

  const node = nodeAt(state.coords, element.keypath) || {};
  const fields = el("div", { class: "fields" });
  for (const key of EDITABLE) {
    if (typeof node[key] !== "number") continue;
    const input = el("input", { type: "number", step: "1", value: String(node[key]) });
    input.onchange = () => {
      const next = parseInt(input.value, 10);
      if (Number.isNaN(next) || next === node[key]) return;
      node[key] = next;
      markDirty();
      refresh();
    };
    fields.append(el("label", {}, el("span", {}, key), input));
  }
  if (typeof node.enabled === "boolean") {
    const toggle = el("input", { type: "checkbox" });
    toggle.checked = node.enabled;
    toggle.onchange = () => {
      node.enabled = toggle.checked;
      markDirty();
      refresh();
    };
    fields.append(el("label", { class: "check" }, el("span", {}, "enabled"), toggle));
  }
  if (fields.children.length) box.append(fields);

  const dl = el("dl");
  if (element.font) {
    dl.append(el("dt", {}, "font"));
    dl.append(el("dd", {}, `${element.font.width}×${element.font.height}`));
  }
  const [x0, y0, x1, y1] = element.box;
  dl.append(el("dt", {}, "box"));
  dl.append(el("dd", {}, `${x0},${y0} → ${x1},${y1}`));
  box.append(dl);

  if (element.dynamic) {
    box.append(
      el(
        "p",
        { class: "readonly-note" },
        "This element's width depends on live game data, so the box is indicative. The magenta dot marks the coordinate that is actually stored."
      )
    );
  }
}

function select(keypath) {
  state.selected = keypath;
  paint();
  paintList();
  paintDetails();
}

$("stage")?.addEventListener("mousedown", (ev) => {
  if (ev.target.id === "stage" || ev.target.id === "preview" || ev.target.id === "boxes") select(null);
});

// Loaded in a browser: start. Loaded by a test runner (no DOM): just export the
// pure bits so the per-extent move rules can be checked without a browser.
if (typeof document !== "undefined" && document.getElementById("stage")) {
  boot();
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { state, applyDelta, nodeAt, clamp };
}
