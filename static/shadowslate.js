"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false,
               pair: null, pairResult: null, pairProbe: null};
const settings = [...document.querySelectorAll("[data-setting]")];

async function api(path, options) {
  const response = await fetch("/shadowslate" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
  const pairSelect = get("pair-library");
  pairSelect.replaceChildren(new Option("选取已存对照卡", ""));
  for (const card of cards) {
    if (card.id !== state.card?.id) pairSelect.append(new Option(card.name, card.id));
  }
  pairSelect.value = state.pair?.id || "";
}

function clearPair() {
  state.pair = null;
  state.pairResult = null;
  state.pairProbe = null;
  get("pair-bar").hidden = true;
  get("pair-summary").hidden = true;
  get("pair-proof").hidden = true;
  get("pair-notice").hidden = true;
  get("pair-probe-result").hidden = true;
  paintSource();
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  const {result} = state;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#7e537f";
  ctx.setLineDash([6,5]);
  ctx.strokeRect(cfg.left,cfg.top,cfg.right-cfg.left,cfg.bottom-cfg.top);
  ctx.setLineDash([]);
  if (result.profile.length) {
    ctx.strokeStyle = "#db9b4d";
    ctx.beginPath();
    ctx.moveTo(cfg.left,result.row);
    ctx.lineTo(cfg.right,result.row);
    ctx.stroke();
  }
  ctx.strokeStyle = "#85447d";
  ctx.lineWidth = 3;
  for (const [left,right] of result.ranges) {
    ctx.beginPath();
    ctx.moveTo(left,result.row);
    ctx.lineTo(right,result.row);
    ctx.stroke();
  }
  if (state.pairResult) {
    const common = state.pairResult.common;
    const ov = common.overlap;
    ctx.fillStyle = "rgba(216,91,62,0.55)";
    for (const [y, x0, x1] of common.ranges) ctx.fillRect(x0, y, x1 - x0 + 1, 1);
    ctx.strokeStyle = "rgba(215,162,95,0.95)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(ov.x0, ov.y0 - 8, ov.x1 - ov.x0, 8);
    ctx.strokeRect(ov.x0, ov.y1 + 1, ov.x1 - ov.x0, 8);
    ctx.setLineDash([]);
  }
  if (probe) {
    ctx.strokeStyle = "#30393b";
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
  }
  if (state.pairProbe && state.pairProbe.peer) {
    ctx.strokeStyle = "#245c54";
    ctx.strokeRect(state.pairProbe.peer.x - 5, state.pairProbe.peer.y - 5, 10, 10);
  }
}

function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width,height} = canvas;
  const {result,card} = state;
  const cfg = card.settings;
  const xAt = (x) => 40 + (width-76)*(x-cfg.left)/Math.max(1,cfg.right-cfg.left);
  const yAt = (y) => height-27 - (height-45)*y/255;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#fcfaf9";
  ctx.fillRect(0,0,width,height);
  ctx.fillStyle = "#ece0eb";
  for (const [left,right] of result.ranges) {
    ctx.fillRect(xAt(left),yAt(255),Math.max(2,xAt(right)-xAt(left)),yAt(0)-yAt(255));
  }
  ctx.font = "12px monospace";
  ctx.fillStyle = "#695e68";
  ctx.strokeStyle = "#ded3df";
  for (const level of [0,128,255]) {
    ctx.fillText(level,5,yAt(level)+3);
    ctx.beginPath();
    ctx.moveTo(xAt(cfg.left),yAt(level));
    ctx.lineTo(xAt(cfg.right),yAt(level));
    ctx.stroke();
  }
  ctx.fillText(String(cfg.left),xAt(cfg.left),height-7);
  ctx.fillText(String(cfg.right),xAt(cfg.right)-15,height-7);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#785276";
  ctx.beginPath();
  result.profile.forEach((p,i) => {
    if (!i) ctx.moveTo(xAt(p.x),yAt(p.brightness));
    else ctx.lineTo(xAt(p.x),yAt(p.brightness));
  });
  ctx.stroke();
  ctx.strokeStyle = "#be8c52";
  ctx.setLineDash([6,4]);
  ctx.beginPath();
  ctx.moveTo(xAt(cfg.left),yAt(cfg.cutoff));
  ctx.lineTo(xAt(cfg.right),yAt(cfg.cutoff));
  ctx.stroke();
  ctx.setLineDash([]);
}

function pointLabel(point) {
  const kind = point.dark === null ? "框外" : (point.dark ? "暗区" : "亮区");
  return "(" + point.x + ", " + point.y + ") · RGB " + point.rgb.join("/") +
    " · " + point.brightness.toFixed(1) + " · " + kind;
}

function darkWord(value) {
  return value === null ? "留待核" : (value ? "暗" : "亮");
}

function pairLabel(sample) {
  if (!sample) return "";
  if (!sample.peer) return "另一张对应位置：图外无真实像素，留待核";
  const here = sample.here, peer = sample.peer;
  const head = "另一张真实坐标 (" + peer.x + ", " + peer.y + ") · RGB " + peer.rgb.join("/") +
    " · " + peer.brightness.toFixed(1) + " · " + darkWord(peer.dark);
  if (sample.pending) return head + " · " + (sample.reason || "缺一边，暗亮留待核");
  const verdict = sample.common_dark ? "两次都暗" : "非两次都暗（本张" + darkWord(here.dark) +
    " / 对照" + darkWord(peer.dark) + "）";
  return head + " · 本张" + darkWord(here.dark) + " → " + verdict;
}

function showPairSummary() {
  const {pairResult, pair} = state;
  if (!pairResult) return;
  const reg = pairResult.registration;
  const common = pairResult.common;
  const ov = common.overlap;
  get("pair-summary").hidden = false;
  get("pair-summary").innerHTML =
    "<b>" + pair.name + "</b> · 横移 <b>" + (reg.shift >= 0 ? "+" : "") + reg.shift +
    " px</b>（允许 ±12 整数）<br>" +
    "上参照 SAD " + reg.chosen.top_sad + " · 下参照 SAD " + reg.chosen.bottom_sad +
    " · 综合 " + reg.chosen.score +
    (reg.best_shift !== reg.shift ? "<br>纹理在多个位置相近，按上下实见贴合且最少移动选用" : "") +
    "<br>共同暗区 <b>" + common.dark_px + "</b> px / " + common.percentage.toFixed(2) +
    "% · 重合 " + common.total_px + " px（X " + ov.x0 + "–" + ov.x1 + "，Y " + ov.y0 + "–" + ov.y1 + "）";
  const proof = get("pair-proof");
  proof.hidden = false;
  proof.href = "/shadowslate/cards/" + state.card.id + "/pair/" + pair.id +
    "/proof.png?revision=" + state.card.revision;
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? Math.round((card.settings.left+card.settings.right)/2);
  get("probe-y").value = state.probe?.y ?? result.row;
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const reading = result.reading;
  get("cover-value").textContent = reading.percentage.toFixed(2);
  get("cover-unit").textContent = "%";
  get("measurement-state").textContent = reading.dark_px + " 个暗像素";
  get("selected-level").textContent = reading.area_mm2.toFixed(2) + " mm² · 原片第 " + result.row + " 行" +
    (result.profile.length ? "" : " · 读数行在框外");
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/shadowslate" + route("/export/" + filename) + savedQuery();
  }
  get("pair-bar").hidden = false;
  paintSource();
  paintChart();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/shadowslate" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, pair: null, pairResult: null, pairProbe: null});
  get("pair-summary").hidden = true;
  get("pair-proof").hidden = true;
  get("pair-notice").hidden = true;
  get("pair-probe-result").hidden = true;
  get("library").value = card.id;
  await library();
  get("pair-bar").hidden = false;
  show();
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  const pairNode = get("pair-probe-result");
  if (state.pairResult) {
    state.pairProbe = await api("/cards/" + state.card.id + "/pair/" + state.pair.id +
      "/probe?revision=" + state.card.revision + "&x=" + x + "&y=" + y);
    pairNode.hidden = false;
    pairNode.textContent = pairLabel(state.pairProbe);
    pairNode.classList.toggle("pending", state.pairProbe.pending);
  } else {
    state.pairProbe = null;
    pairNode.hidden = true;
  }
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("practice-pair").addEventListener("click", () => action(async () => {
  const {card_a, card_b} = await api("/practice-pair", {method: "POST"});
  await open(card_a);
  await library();
  get("pair-library").value = card_b.id;
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      settings.find((field) => field.dataset.setting === "row").value = y;
      notice("已填入实际原片读数行。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
get("pair-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const peerId = get("pair-library").value;
    if (!peerId) {
      notice("请先选择另一张已存投影卡。", "pair-notice");
      return;
    }
    const pairResult = await api("/cards/" + state.card.id + "/pair/" + peerId +
      "?revision=" + state.card.revision);
    const {cards} = await api("/cards");
    state.pair = cards.find((item) => item.id === peerId);
    state.pairResult = pairResult;
    state.pairProbe = null;
    get("pair-notice").hidden = true;
    showPairSummary();
    paintSource();
  }, "pair-notice");
});
get("pair-clear").addEventListener("click", () => clearPair());
action(library);
