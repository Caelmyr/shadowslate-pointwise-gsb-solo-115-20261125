"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false};
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
    if (state.result) paintEdge();
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
}

function displayEdge() {
  return state.previewEdge || state.result?.edge || null;
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
  const edge = displayEdge();
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
  if (edge?.spread) {
    const {start, end} = edge.spread;
    const y = edge.y;
    ctx.strokeStyle = "#2f8574";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(start,y);
    ctx.lineTo(end,y);
    ctx.stroke();
    for (const endX of [start, end]) {
      ctx.beginPath();
      ctx.moveTo(endX,y-9);
      ctx.lineTo(endX,y+9);
      ctx.stroke();
    }
  }
  if (edge?.x !== null && edge?.x !== undefined) {
    ctx.strokeStyle = "#c9efe3";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(edge.x, edge.y-11);
    ctx.lineTo(edge.x+11, edge.y);
    ctx.lineTo(edge.x, edge.y+11);
    ctx.lineTo(edge.x-11, edge.y);
    ctx.closePath();
    ctx.stroke();
  }
  if (probe) {
    ctx.strokeStyle = "#30393b";
    ctx.lineWidth = 2;
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
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
  const edge = displayEdge();
  if (edge?.spread && edge.y === result.row) {
    ctx.fillStyle = "#2f8574";
    for (const level of [edge.spread.low_level, edge.spread.high_level]) {
      ctx.fillRect(xAt(cfg.left),yAt(level)-1,width-76,2);
    }
    ctx.strokeStyle = "#2f8574";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(xAt(edge.spread.start),yAt(255));
    ctx.lineTo(xAt(edge.spread.start),yAt(0));
    ctx.moveTo(xAt(edge.spread.end),yAt(255));
    ctx.lineTo(xAt(edge.spread.end),yAt(0));
    ctx.stroke();
    ctx.font = "12px monospace";
    ctx.fillStyle = "#246d5f";
    ctx.fillText(edge.spread.start.toFixed(2), xAt(edge.spread.start)-10, yAt(255)+14);
    ctx.fillText(edge.spread.end.toFixed(2), xAt(edge.spread.end)+4, yAt(255)+14);
  }
}

function pointLabel(point) {
  const kind = point.dark === null ? "框外" : (point.dark ? "暗区" : "亮区");
  let text = "(" + point.x + ", " + point.y + ") · RGB " + point.rgb.join("/") +
    " · " + point.brightness.toFixed(1) + " · " + kind;
  const edge = point.edge;
  if (edge && edge.row_applies) {
    text += edge.position
      ? " · 本次过渡·" + edge.position
      : " · " + (edge.note || "本次过渡待核");
  } else if (edge && edge.x !== null && edge.x !== undefined) {
    text += " · " + (edge.note || ("本次影边在第 " + edge.y + " 行，本行不套用"));
  }
  return text;
}

function edgeText(edge) {
  if (!edge || edge.x === null || edge.x === undefined) return "尚未点取影边位置";
  const head = "影边点 (" + edge.x + ", " + edge.y + ")";
  if (edge.status !== "ok") return head + " · " + (edge.note || "待核");
  const spread = edge.spread;
  const dark = edge.dark;
  const light = edge.light;
  const order = spread.dark_left ? "暗端 → 亮端" : "亮端 → 暗端";
  return [head + " · 过渡 " + spread.width_px.toFixed(2) + " px / " + spread.width_mm.toFixed(2) +
    " mm（" + order + "）",
    "暗端 " + dark.left + "–" + dark.right + " px · " + dark.pixels + " 像素 · " +
    dark.brightness.toFixed(1) + " · 端内最亮−最暗 " + dark.spread.toFixed(1),
    "亮端 " + light.left + "–" + light.right + " px · " + light.pixels + " 像素 · " +
    light.brightness.toFixed(1) + " · 端内最亮−最暗 " + light.spread.toFixed(1),
    "10% " + spread.low_level.toFixed(1) + " → X " + spread.start.toFixed(2) +
    " · 90% " + spread.high_level.toFixed(1) + " → X " + spread.end.toFixed(2),
    "只在相邻原片像素夹住处读取，不朝画外推"].join("\n");
}

function edgeCell(line) {
  const row = document.createElement("span");
  if (line.includes("待核")) row.className = "note";
  row.textContent = line;
  return row;
}

function paintEdge() {
  const edge = displayEdge();
  const target = get("edge-result");
  target.replaceChildren();
  target.classList.toggle("pending", !edge || edge.status !== "ok");
  for (const line of edgeText(edge).split("\n")) target.append(edgeCell(line));
  get("edge-head").textContent = edge?.spread
    ? "本次过渡 " + edge.spread.width_mm.toFixed(2) + " mm · 第 " + edge.y + " 行"
    : "左右各32列内取实际端";
  const adopted = state.result?.edge;
  const hasAdopted = adopted?.x !== null && adopted?.x !== undefined;
  get("adopt-edge").hidden = !edge?.spread;
  get("adopt-edge").disabled = !edge?.spread || (hasAdopted && edge.x === adopted.x && edge.y === adopted.y);
  get("clear-edge").disabled = !hasAdopted;
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
  paintSource();
  paintChart();
  paintEdge();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/shadowslate" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, previewEdge: null});
  get("library").value = card.id;
  show();
}

async function patchSettings(settings) {
  return api(route(""), {
    method: "PATCH", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, name: state.card.name, settings}),
  });
}

async function saveSettings() {
  const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
  return api(route(""), {
    method: "PATCH", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
  });
}

get("adopt-edge").addEventListener("click", () => action(async () => {
  const edge = displayEdge();
  if (!edge?.spread) return;
  const card = await patchSettings({edge: {x: edge.x, y: edge.y}});
  await open(card);
  await library();
  notice("已采用影边；截线标出这两个坐标，其他原片行不套用本次读数。");
}));
get("clear-edge").addEventListener("click", () => action(async () => {
  const card = await patchSettings({edge: null});
  await open(card);
  await library();
  notice("已清除影边，原覆盖比例不变。");
}));

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
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
    if (state.mode === "edge") {
      const edge = await api(route("/edge") + savedQuery() + "&x=" + x + "&y=" + state.result.row);
      state.previewEdge = edge;
      paintSource();
      paintEdge();
      notice(edge.status === "ok"
        ? "影边过渡已读取，可在「影边过渡区」核对后采用。"
        : (edge.note || "影边过渡待核。"));
      return;
    }
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
    const card = await saveSettings();
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
