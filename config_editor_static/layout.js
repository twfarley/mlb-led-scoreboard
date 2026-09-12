"use strict";

// Read-only layout viewer.
//
// The board image is rendered server-side by the real renderers and served at
// native resolution (one image pixel per LED); this scales it by an integer
// factor so a board pixel stays a crisp square. Bounding boxes are computed
// server-side too, beside the anchor metadata and font metrics -- see
// layout_preview.elements(). Nothing here re-derives where an element sits.

const $ = (id) => document.getElementById(id);

const state = {
  size: null,
  screen: null,
  zoom: "fit",
  elements: [],
  selected: null,
  board: { width: 0, height: 0 },
};

// Zoom must stay an INTEGER number of screen pixels per LED. A fractional
// scale resamples the preview and the grid stops lining up with the board,
// which would make coordinates -- and later, snapping -- untrustworthy.
function zoomFactor() {
  if (state.zoom !== "fit") return Number(state.zoom);

  const { width, height } = state.board;
  if (!width || !height) return 8;

  const wrap = $("stage-wrap");
  const availableWidth = (wrap ? wrap.clientWidth : window.innerWidth) - 8;
  const availableHeight = window.innerHeight - $("stage").getBoundingClientRect().top - 80;

  return Math.max(1, Math.min(Math.floor(availableWidth / width), Math.floor(availableHeight / height)));
}

// ── helpers ──────────────────────────────────────────────────────────────────

function banner(message) {
  const el = $("banner");
  if (!message) {
    el.hidden = true;
    return;
  }
  el.textContent = message;
  el.hidden = false;
}

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
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

  sizeSel.onchange = () => load();
  screenSel.onchange = () => load();
  $("zoom").onchange = () => {
    state.zoom = $("zoom").value;
    localStorage.setItem("layoutZoom", state.zoom);
    paint();
  };
  $("show-boxes").onchange = paint;
  $("show-grid").onchange = paint;

  const savedZoom = localStorage.getItem("layoutZoom");
  if (savedZoom && [...$("zoom").options].some((o) => o.value === savedZoom)) {
    $("zoom").value = savedZoom;
  }
  state.zoom = $("zoom").value;

  // "Fit" is measured from the viewport, so it has to be recomputed on resize.
  window.addEventListener("resize", () => {
    if (state.zoom === "fit") paint();
  });

  await load();
}

async function load() {
  state.size = $("size").value;
  state.screen = $("screen").value;
  state.selected = null;
  banner("");

  // Cache-bust so a coordinate change is never masked by a stale frame.
  $("preview").src = `/api/layout/preview?size=${state.size}&screen=${state.screen}&t=${Date.now()}`;

  try {
    const data = await getJSON(`/api/layout/elements?size=${state.size}&screen=${state.screen}`);
    state.elements = data.elements;
    state.board = { width: data.width, height: data.height };
  } catch (err) {
    state.elements = [];
    banner(`Could not load elements: ${err.message}`);
  }
  paint();
  paintList();
  paintDetails();
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
    box.onclick = (ev) => {
      ev.stopPropagation();
      select(element.keypath);
    };
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
    const row = el(
      "li",
      { class: classes.join(" ") },
      el("span", { class: "key" }, element.keypath),
      el("span", { class: "where" }, element.enabled === false ? "off" : element.anchor)
    );
    row.onclick = () => select(element.keypath);
    list.append(row);
  }
}

const ANCHOR_HELP = {
  left: "x is the left edge.",
  center: "x is the horizontal CENTRE — the renderer runs it through center_text_position, so the element spreads either side of it.",
  right: "x is the RIGHTMOST pixel — content is drawn right-to-left from it.",
  "board-right": "Pinned to the right edge of the board. x is not configurable for this element.",
  none: "No x/y position; this is a column offset inside a list layout.",
};

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

  box.append(el("h3", {}, element.keypath));

  const tags = el("div");
  tags.append(el("span", { class: "tag" }, element.anchor));
  tags.append(el("span", { class: "tag" }, element.extent));
  if (element.enabled === false) tags.append(el("span", { class: "tag warn" }, "disabled"));
  if (element.dynamic) tags.append(el("span", { class: "tag warn" }, "width varies"));
  box.append(tags);

  box.append(el("p", { class: "note" }, ANCHOR_HELP[element.anchor] || ""));
  if (element.note) box.append(el("p", { class: "note" }, element.note));

  const dl = el("dl");
  for (const [key, value] of Object.entries(element.coords)) {
    dl.append(el("dt", {}, key));
    dl.append(el("dd", {}, typeof value === "object" ? JSON.stringify(value) : String(value)));
  }
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
  box.append(el("p", { class: "readonly-note" }, "Read-only for now — dragging and saving come next."));
}

function select(keypath) {
  state.selected = keypath;
  paint();
  paintList();
  paintDetails();
}

$("stage").addEventListener("click", () => select(null));

boot();
