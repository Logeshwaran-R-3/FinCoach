/* FinCoach app.js — vanilla interactions + SVG charts. No dependencies. */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function grouped(n) {
    return Math.abs(Math.round(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  function fmtRs(n) {
    // Matches the server-side `inr` filter exactly ("Rs 15,002" / "Rs -2,800").
    var v = Number(n);
    if (!isFinite(v)) return "Rs 0";
    return "Rs " + (v < 0 ? "-" : "") + grouped(v);
  }
  function fmtSignedRs(n) {
    // Matches the server-side `inr_signed` filter ("Rs +1,650" / "Rs -2,800").
    var v = Number(n);
    if (!isFinite(v)) return "Rs 0";
    var sign = v > 0 ? "+" : (v < 0 ? "-" : "");
    return "Rs " + sign + grouped(v);
  }
  window.FinCoach = window.FinCoach || {};
  window.FinCoach.fmtRs = fmtRs;

  /* ---------- Toasts (JS-driven feedback) ---------- */
  function ensureToastBox() {
    var box = $("#toasts");
    if (!box) {
      box = document.createElement("div");
      box.id = "toasts";
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    return box;
  }
  function toast(message, kind) {
    var box = ensureToastBox();
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.setAttribute("role", "status");
    var span = document.createElement("span");
    span.textContent = message;
    var close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    close.textContent = "×";
    close.addEventListener("click", function () { el.remove(); });
    el.appendChild(span);
    el.appendChild(close);
    box.appendChild(el);
    window.setTimeout(function () {
      if (el.isConnected) el.remove();
    }, 6000);
  }
  window.FinCoach.toast = toast;

  /* ---------- Mobile nav ---------- */
  function initNav() {
    var btn = $("[data-nav-toggle]");
    var scrim = $("[data-nav-scrim]");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    if (scrim) scrim.addEventListener("click", function () {
      document.body.classList.remove("nav-open");
      btn.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        document.body.classList.remove("nav-open");
        closeDrawer();
      }
    });
    $$(".side nav a").forEach(function (a) {
      a.addEventListener("click", function () {
        document.body.classList.remove("nav-open");
      });
    });
  }

  /* ---------- Tabs ---------- */
  function initTabs() {
    $$("[data-tabs]").forEach(function (group) {
      var tabs = $$('[role="tab"]', group);
      var panels = $$(".tab-panel", group.parentElement || document);
      // Panels may live outside the tablist; resolve by aria-controls.
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          tabs.forEach(function (t) { t.setAttribute("aria-selected", "false"); });
          tab.setAttribute("aria-selected", "true");
          var id = tab.getAttribute("aria-controls");
          panels.forEach(function (p) { p.classList.remove("on"); });
          var panel = id && document.getElementById(id);
          if (panel) panel.classList.add("on");
        });
      });
    });
  }

  /* ---------- Dialogs ---------- */
  function initDialogs() {
    $$("[data-open-dialog]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var dlg = document.getElementById(btn.getAttribute("data-open-dialog"));
        if (!dlg) return;
        // Copy data-* presets into matching form fields.
        Object.keys(btn.dataset).forEach(function (key) {
          if (key === "openDialog") return;
          var field = dlg.querySelector('[name="' + key + '"]');
          if (field) field.value = btn.dataset[key];
        });
        var title = dlg.querySelector("[data-dialog-title]");
        if (title && btn.dataset.title) title.textContent = btn.dataset.title;
        if (typeof dlg.showModal === "function") dlg.showModal();
        var first = dlg.querySelector("input, select, textarea");
        if (first) first.focus();
      });
    });
    $$("dialog [data-close]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var dlg = btn.closest("dialog");
        if (dlg) dlg.close();
      });
    });
    $$("dialog").forEach(function (dlg) {
      dlg.addEventListener("click", function (ev) {
        if (ev.target === dlg) dlg.close();
      });
    });
  }

  /* ---------- Drawer ---------- */
  function closeDrawer() {
    var drawer = $("[data-drawer]");
    if (drawer) {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    }
    var scrim = $("[data-drawer-scrim]");
    if (scrim) scrim.style.display = "none";
  }
  function openDrawer() {
    var drawer = $("[data-drawer]");
    if (!drawer) return;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    var scrim = $("[data-drawer-scrim]");
    if (scrim) scrim.style.display = "block";
    var close = $("[data-drawer-close]", drawer);
    if (close) close.focus();
  }
  function initDrawer() {
    var drawer = $("[data-drawer]");
    if (!drawer) return;
    $$("[data-drawer-close]").forEach(function (b) {
      b.addEventListener("click", closeDrawer);
    });
    var scrim = $("[data-drawer-scrim]");
    if (scrim) scrim.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeDrawer();
    });
  }

  /* ---------- Dropzone ---------- */
  function initDropzones() {
    $$("[data-dropzone]").forEach(function (zone) {
      var input = $('input[type="file"]', zone);
      var nameEl = $("[data-dz-name]", zone);
      if (!input) return;
      zone.addEventListener("click", function (ev) {
        if (ev.target.closest("button, a, select, input")) return;
        input.click();
      });
      zone.addEventListener("keydown", function (ev) {
        if ((ev.key === "Enter" || ev.key === " ") && ev.target === zone) {
          ev.preventDefault();
          input.click();
        }
      });
      ["dragenter", "dragover"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.add("over");
        });
      });
      ["dragleave", "drop"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.remove("over");
        });
      });
      zone.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          input.files = e.dataTransfer.files;
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
      input.addEventListener("change", function () {
        if (nameEl) {
          nameEl.textContent = input.files.length ? input.files[0].name : "No file chosen yet";
        }
      });
    });
  }

  /* ---------- Flash on hash target (e.g. #source-3) ---------- */
  function initHashFlash() {
    function flash() {
      var id = window.location.hash;
      if (!id) return;
      var el = document.querySelector(id);
      if (!el) return;
      el.classList.add("flash");
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      window.setTimeout(function () { el.classList.remove("flash"); }, 2400);
    }
    window.addEventListener("hashchange", flash);
    if (window.location.hash) window.setTimeout(flash, 150);
  }

  /* ---------- Range fill ---------- */
  function paintRange(input) {
    var min = Number(input.min || 0);
    var max = Number(input.max || 100);
    var pct = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 50;
    input.style.setProperty("--fill", Math.max(0, Math.min(100, pct)) + "%");
  }
  function initRanges() {
    $$('input[type="range"]').forEach(function (r) {
      paintRange(r);
      r.addEventListener("input", function () { paintRange(r); });
    });
  }

  /* ---------- SVG charts ---------- */
  var SVG_NS = "http://www.w3.org/2000/svg";
  function el(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }
  function parseJSONAttr(node, attr) {
    try { return JSON.parse(node.getAttribute(attr) || "null"); }
    catch (e) { return null; }
  }
  function niceMax(value) {
    if (!(value > 0)) return 10;
    var exp = Math.floor(Math.log10(value));
    var base = Math.pow(10, exp);
    var ratio = value / base;
    var nice = ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 2.5 ? 2.5 : ratio <= 5 ? 5 : ratio <= 7.5 ? 7.5 : 10;
    return nice * base;
  }
  function shortRs(v) {
    var n = Number(v);
    if (!isFinite(n)) return "0";
    var a = Math.abs(n);
    if (a >= 100000) return (n / 100000).toFixed(a % 100000 ? 1 : 0) + "L";
    if (a >= 1000) return (n / 1000).toFixed(a % 1000 ? 1 : 0) + "k";
    return String(Math.round(n));
  }

  var SHORT_MONTHS = { "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec" };
  function shortLabel(label) {
    var m = /^(\d{4})-(\d{2})$/.exec(String(label || ""));
    if (m && SHORT_MONTHS[m[2]]) return SHORT_MONTHS[m[2]] + " '" + m[1].slice(2);
    return label;
  }

  var tip = null;
  function ensureTip() {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
    }
    return tip;
  }
  function bindTip(node, text) {
    node.addEventListener("mousemove", function (ev) {
      var t = ensureTip();
      t.textContent = text;
      t.classList.add("visible");
      var x = Math.min(window.innerWidth - 270, ev.clientX + 14);
      var y = Math.max(8, ev.clientY - 10 - t.offsetHeight);
      t.style.left = x + "px";
      t.style.top = y + "px";
    });
    node.addEventListener("mouseleave", function () {
      if (tip) tip.classList.remove("visible");
    });
    node.addEventListener("focus", function () {
      var t = ensureTip();
      t.textContent = text;
      var r = node.getBoundingClientRect();
      t.classList.add("visible");
      t.style.left = Math.min(window.innerWidth - 270, r.left + r.width / 2) + "px";
      t.style.top = Math.max(8, r.top - t.offsetHeight - 8) + "px";
    });
    node.addEventListener("blur", function () {
      if (tip) tip.classList.remove("visible");
    });
  }

  function emptyChart(host, message) {
    host.innerHTML = "";
    var div = document.createElement("div");
    div.className = "chart-empty";
    div.textContent = message || "Not enough data yet.";
    host.appendChild(div);
  }

  function renderGrouped(host) {
    var labels = parseJSONAttr(host, "data-labels") || [];
    var series = parseJSONAttr(host, "data-series") || [];
    if (!labels.length || !series.length) { emptyChart(host); return; }
    var W = 640, H = 260, padL = 52, padR = 12, padT = 12, padB = 34;
    var maxV = 0;
    series.forEach(function (s) {
      (s.values || []).forEach(function (v) { maxV = Math.max(maxV, Number(v) || 0); });
    });
    maxV = niceMax(maxV * 1.08);
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Bar chart" });
    var plotW = W - padL - padR, plotH = H - padT - padB;
    for (var g = 0; g <= 4; g++) {
      var y = padT + (plotH * g) / 4;
      svg.appendChild(el("line", { x1: padL, y1: y, x2: W - padR, y2: y, class: "grid-line" }));
      var t = el("text", { x: padL - 8, y: y + 4, class: "axis", "text-anchor": "end" });
      t.textContent = shortRs((maxV * (4 - g)) / 4);
      svg.appendChild(t);
    }
    var n = labels.length, m = series.length;
    var groupW = plotW / n;
    var barW = Math.min(34, (groupW - 18) / m);
    labels.forEach(function (label, i) {
      var disp = shortLabel(label);
      var cx = padL + groupW * i + groupW / 2;
      var tx = el("text", { x: cx, y: H - 10, class: "axis", "text-anchor": "middle" });
      tx.textContent = disp;
      svg.appendChild(tx);
      series.forEach(function (s, j) {
        var v = Number((s.values || [])[i]) || 0;
        var h = maxV ? (v / maxV) * plotH : 0;
        var x = cx + (j - (m - 1) / 2) * barW - barW / 2 + (barW * 0.08);
        var w = barW * 0.84;
        var rect = el("rect", {
          x: x.toFixed(1), y: (padT + plotH - h).toFixed(1),
          width: w.toFixed(1), height: Math.max(h, v > 0 ? 2 : 0).toFixed(1),
          rx: 4, fill: s.color || "#2ec4b6", class: "bar", tabindex: "0"
        });
        svg.appendChild(rect);
        bindTip(rect, label + "\n" + s.name + ": " + fmtRs(v));
      });
    });
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function renderArea(host) {
    var labels = parseJSONAttr(host, "data-labels") || [];
    var values = parseJSONAttr(host, "data-values") || [];
    var color = host.getAttribute("data-color") || "#178377";
    var name = host.getAttribute("data-name") || "Value";
    if (!labels.length || !values.length) { emptyChart(host); return; }
    var W = 640, H = 210, padL = 52, padR = 14, padT = 14, padB = 32;
    var nums = values.map(Number);
    var maxV = niceMax(Math.max.apply(null, nums.concat([0])) * 1.1);
    var minV = Math.min.apply(null, nums.concat([0]));
    if (minV > 0) minV = 0;
    var span = maxV - minV || 1;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Trend chart" });
    var plotW = W - padL - padR, plotH = H - padT - padB;
    function X(i) { return padL + (labels.length > 1 ? (plotW * i) / (labels.length - 1) : plotW / 2); }
    function Y(v) { return padT + plotH - ((v - minV) / span) * plotH; }
    for (var g = 0; g <= 3; g++) {
      var gy = padT + (plotH * g) / 3;
      svg.appendChild(el("line", { x1: padL, y1: gy, x2: W - padR, y2: gy, class: "grid-line" }));
      var gt = el("text", { x: padL - 8, y: gy + 4, class: "axis", "text-anchor": "end" });
      gt.textContent = shortRs(minV + (span * (3 - g)) / 3);
      svg.appendChild(gt);
    }
    labels.forEach(function (label, i) {
      if (labels.length > 8 && i % 2 === 1) return;
      var tx = el("text", { x: X(i), y: H - 10, class: "axis", "text-anchor": "middle" });
      tx.textContent = shortLabel(label);
      svg.appendChild(tx);
    });
    var gid = "area-" + Math.floor(Math.random() * 100000);
    var defs = el("defs", {});
    var grad = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": color, "stop-opacity": "0.35" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": color, "stop-opacity": "0.02" }));
    defs.appendChild(grad);
    svg.appendChild(defs);
    var line = nums.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); }).join(" ");
    var baseY = Y(minV);
    svg.appendChild(el("path", { d: line + " L" + X(nums.length - 1).toFixed(1) + "," + baseY + " L" + X(0).toFixed(1) + "," + baseY + " Z", fill: "url(#" + gid + ")" }));
    svg.appendChild(el("path", { d: line, fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    var zeroY = Y(0);
    if (minV < 0) svg.appendChild(el("line", { x1: padL, y1: zeroY, x2: W - padR, y2: zeroY, stroke: "#94a8a4", "stroke-dasharray": "4 3" }));
    nums.forEach(function (v, i) {
      var c = el("circle", { cx: X(i), cy: Y(v), r: 4.5, fill: "#fff", stroke: color, "stroke-width": 2.5, tabindex: "0" });
      svg.appendChild(c);
      bindTip(c, labels[i] + "\n" + name + ": " + fmtRs(v));
    });
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function renderDonut(host) {
    var slices = parseJSONAttr(host, "data-slices") || [];
    slices = slices.filter(function (s) { return Number(s.value) > 0; });
    if (!slices.length) { emptyChart(host, "No spending in this period yet."); return; }
    var total = slices.reduce(function (a, s) { return a + Number(s.value); }, 0);
    var size = 210, r = 78, circ = 2 * Math.PI * r;
    var svg = el("svg", { viewBox: "0 0 " + size + " " + size, role: "img", "aria-label": "Donut chart", style: "max-width:230px;margin:0 auto" });
    var cx = size / 2, cy = size / 2;
    svg.appendChild(el("circle", { cx: cx, cy: cy, r: r, fill: "none", stroke: "#e3edeb", "stroke-width": 30 }));
    var acc = 0;
    slices.forEach(function (s) {
      var frac = Number(s.value) / total;
      var seg = el("circle", {
        cx: cx, cy: cy, r: r, fill: "none",
        stroke: s.color || "#2ec4b6", "stroke-width": 30,
        "stroke-dasharray": (frac * circ).toFixed(1) + " " + circ.toFixed(1),
        "stroke-dashoffset": (-acc * circ).toFixed(1),
        transform: "rotate(-90 " + cx + " " + cy + ")",
        tabindex: "0"
      });
      svg.appendChild(seg);
      bindTip(seg, s.name + ": " + fmtRs(s.value) + " (" + (frac * 100).toFixed(1) + "%)");
      acc += frac;
    });
    var t1 = el("text", { x: cx, y: cy - 2, "text-anchor": "middle", fill: "#22313a", "font-weight": 800, "font-size": 21 });
    t1.textContent = shortRs(total);
    var t2 = el("text", { x: cx, y: cy + 18, "text-anchor": "middle", fill: "#5a6b73", "font-size": 11, "font-weight": 700 });
    t2.textContent = "TOTAL";
    svg.appendChild(t1);
    svg.appendChild(t2);
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function renderLine(host) {
    // Multi-series line chart over data-labels / data-series (simulator).
    var labels = parseJSONAttr(host, "data-labels") || [];
    var series = parseJSONAttr(host, "data-series") || [];
    if (!labels.length || !series.length) { emptyChart(host); return; }
    var W = 640, H = 300, padL = 52, padR = 14, padT = 14, padB = 32;
    var maxV = 0;
    series.forEach(function (s) {
      (s.values || []).forEach(function (v) { maxV = Math.max(maxV, Number(v) || 0); });
    });
    maxV = niceMax(maxV * 1.1);
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Line chart" });
    var plotW = W - padL - padR, plotH = H - padT - padB;
    function X(i) { return padL + (labels.length > 1 ? (plotW * i) / (labels.length - 1) : plotW / 2); }
    function Y(v) { return padT + plotH - (maxV ? (v / maxV) * plotH : 0); }
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (plotH * g) / 4;
      svg.appendChild(el("line", { x1: padL, y1: gy, x2: W - padR, y2: gy, class: "grid-line" }));
      var gt = el("text", { x: padL - 8, y: gy + 4, class: "axis", "text-anchor": "end" });
      gt.textContent = shortRs((maxV * (4 - g)) / 4);
      svg.appendChild(gt);
    }
    labels.forEach(function (label, i) {
      var tx = el("text", { x: X(i), y: H - 10, class: "axis", "text-anchor": "middle" });
      tx.textContent = shortLabel(label);
      svg.appendChild(tx);
    });
    series.forEach(function (s) {
      var color = s.color || "#178377";
      var nums = labels.map(function (_, i) { return Number((s.values || [])[i]) || 0; });
      var d = nums.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); }).join(" ");
      svg.appendChild(el("path", { d: d, fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      nums.forEach(function (v, i) {
        var c = el("circle", { cx: X(i), cy: Y(v), r: 4.5, fill: "#fff", stroke: color, "stroke-width": 2.5, tabindex: "0" });
        svg.appendChild(c);
        bindTip(c, labels[i] + "\n" + s.name + ": " + fmtRs(v));
      });
    });
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function renderSimPies(host, donut) {
    // Baseline vs Scenario side-by-side pies over Income/Expenses/Savings.
    var labels = parseJSONAttr(host, "data-labels") || [];
    var series = parseJSONAttr(host, "data-series") || [];
    if (!labels.length || !series.length) { emptyChart(host); return; }
    var palette = ["#178377", "#ff9f1c", "#2ec4b6"];
    var W = 640, H = 300;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": donut ? "Donut chart" : "Pie chart" });
    var shown = series.slice(0, 2);
    shown.forEach(function (s, si) {
      var cx = W * (shown.length > 1 ? (si === 0 ? 0.27 : 0.73) : 0.5);
      var cy = H / 2 + 8, r = donut ? 78 : 92;
      var vals = labels.map(function (_, i) { return Math.max(Number((s.values || [])[i]) || 0, 0); });
      var total = vals.reduce(function (a, v) { return a + v; }, 0) || 1;
      var title = el("text", { x: cx, y: 22, "text-anchor": "middle", fill: "#22313a", "font-weight": 800, "font-size": 14 });
      title.textContent = s.name;
      svg.appendChild(title);
      if (donut) svg.appendChild(el("circle", { cx: cx, cy: cy, r: r, fill: "none", stroke: "#e3edeb", "stroke-width": 30 }));
      var acc = 0;
      vals.forEach(function (v, i) {
        var frac = v / total;
        var color = palette[i % palette.length];
        if (donut) {
          var circ = 2 * Math.PI * r;
          var seg = el("circle", {
            cx: cx, cy: cy, r: r, fill: "none", stroke: color, "stroke-width": 30,
            "stroke-dasharray": (frac * circ).toFixed(1) + " " + circ.toFixed(1),
            "stroke-dashoffset": (-acc * circ).toFixed(1),
            transform: "rotate(-90 " + cx + " " + cy + ")", tabindex: "0"
          });
          svg.appendChild(seg);
          bindTip(seg, s.name + " " + labels[i] + ": " + fmtRs(v));
        } else if (frac > 0) {
          var a0 = acc * 2 * Math.PI - Math.PI / 2, a1 = (acc + frac) * 2 * Math.PI - Math.PI / 2;
          var large = frac > 0.5 ? 1 : 0;
          var d = "M" + cx + "," + cy + " L" + (cx + r * Math.cos(a0)).toFixed(1) + "," + (cy + r * Math.sin(a0)).toFixed(1) +
            " A" + r + "," + r + " 0 " + large + " 1 " + (cx + r * Math.cos(a1)).toFixed(1) + "," + (cy + r * Math.sin(a1)).toFixed(1) + " Z";
          var slice = el("path", { d: d, fill: color, stroke: "#fff", "stroke-width": 2, tabindex: "0" });
          svg.appendChild(slice);
          bindTip(slice, s.name + " " + labels[i] + ": " + fmtRs(v) + " (" + (frac * 100).toFixed(1) + "%)");
        }
        acc += frac;
      });
      var cap = el("text", { x: cx, y: H - 8, "text-anchor": "middle", fill: "#5a6b73", "font-size": 11, "font-weight": 700 });
      cap.textContent = "Total " + shortRs(total);
      svg.appendChild(cap);
    });
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function renderHistogram(host) {
    // Distribution of the baseline + scenario figures across 6 bins.
    var series = parseJSONAttr(host, "data-series") || [];
    var vals = [];
    series.forEach(function (s) {
      (s.values || []).forEach(function (v) { vals.push(Math.max(Number(v) || 0, 0)); });
    });
    if (!vals.length) { emptyChart(host); return; }
    var BINS = 6;
    var maxV = Math.max.apply(null, vals.concat([1]));
    var bins = [];
    for (var b = 0; b < BINS; b++) bins.push(0);
    vals.forEach(function (v) {
      var idx = Math.min(BINS - 1, Math.floor((v / maxV) * BINS));
      bins[idx]++;
    });
    var W = 640, H = 300, padL = 40, padR = 12, padT = 14, padB = 44;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Histogram" });
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var top = Math.max.apply(null, bins.concat([1]));
    for (var g = 0; g <= top; g++) {
      var y = padT + plotH - (plotH * g) / top;
      svg.appendChild(el("line", { x1: padL, y1: y, x2: W - padR, y2: y, class: "grid-line" }));
      var gt = el("text", { x: padL - 8, y: y + 4, class: "axis", "text-anchor": "end" });
      gt.textContent = String(g);
      svg.appendChild(gt);
    }
    var bw = plotW / BINS;
    bins.forEach(function (count, i) {
      var h = (count / top) * plotH;
      var x = padL + bw * i + bw * 0.12;
      var rect = el("rect", {
        x: x.toFixed(1), y: (padT + plotH - h).toFixed(1),
        width: (bw * 0.76).toFixed(1), height: Math.max(h, count ? 3 : 0).toFixed(1),
        rx: 4, fill: "#178377", class: "bar", tabindex: "0"
      });
      svg.appendChild(rect);
      var lo = shortRs((maxV * i) / BINS), hi = shortRs((maxV * (i + 1)) / BINS);
      bindTip(rect, lo + "–" + hi + ": " + count + " figure(s)");
      var tx = el("text", { x: padL + bw * i + bw / 2, y: H - 26, class: "axis", "text-anchor": "middle" });
      tx.textContent = lo + "–" + hi;
      svg.appendChild(tx);
    });
    var note = el("text", { x: padL + plotW / 2, y: H - 8, class: "axis", "text-anchor": "middle" });
    note.textContent = "Distribution of the " + vals.length + " baseline + scenario figures";
    svg.appendChild(note);
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function simChartType() {
    var sel = $("[data-sim-chart-type]");
    return sel ? sel.value : "bar";
  }
  function renderSimChart(host) {
    var type = simChartType();
    if (type === "line") renderLine(host);
    else if (type === "pie") renderSimPies(host, false);
    else if (type === "donut") renderSimPies(host, true);
    else if (type === "histogram") renderHistogram(host);
    else renderGrouped(host);
  }

  function initCharts() {
    $$("[data-chart]").forEach(function (host) {
      var kind = host.getAttribute("data-chart");
      if (host.hasAttribute("data-sim-chart")) renderSimChart(host);
      else if (kind === "grouped") renderGrouped(host);
      else if (kind === "area") renderArea(host);
      else if (kind === "donut") renderDonut(host);
    });
    var typeSel = $("[data-sim-chart-type]");
    if (typeSel) typeSel.addEventListener("change", function () {
      var host = $("[data-sim-chart]");
      if (host) renderSimChart(host);
    });
    function enlarge() {
      var host = $("[data-sim-chart]");
      var dlg = document.getElementById("sim-enlarged");
      var big = $("[data-sim-chart-big]");
      if (!host || !dlg || !big) return;
      ["data-labels", "data-series"].forEach(function (attr) {
        big.setAttribute(attr, host.getAttribute(attr) || "[]");
      });
      var type = simChartType();
      if (type === "line") renderLine(big);
      else if (type === "pie") renderSimPies(big, false);
      else if (type === "donut") renderSimPies(big, true);
      else if (type === "histogram") renderHistogram(big);
      else renderGrouped(big);
      if (typeof dlg.showModal === "function") dlg.showModal();
    }
    var chartHost = $("[data-sim-chart]");
    if (chartHost) {
      chartHost.addEventListener("click", enlarge);
      chartHost.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); enlarge(); }
      });
    }
    var enlargeBtn = $("[data-sim-enlarge]");
    if (enlargeBtn) enlargeBtn.addEventListener("click", enlarge);
  }
  window.FinCoach.renderGrouped = renderGrouped;
  window.FinCoach.renderArea = renderArea;
  window.FinCoach.renderDonut = renderDonut;
  window.FinCoach.renderLine = renderLine;
  window.FinCoach.renderSimPies = renderSimPies;
  window.FinCoach.renderHistogram = renderHistogram;
  window.FinCoach.renderSimChart = renderSimChart;

  /* ---------- Filterable / sortable tables ---------- */
  function initDataTables() {
    $$("[data-table]").forEach(function (wrap) {
      var table = $("table", wrap);
      if (!table) return;
      var tbody = $("tbody", table);
      if (!tbody) return;
      var rows = $$("tr", tbody);
      var search = $("[data-table-search]", wrap);
      var selects = $$("[data-table-filter]", wrap);
      var count = $("[data-table-count]", wrap);
      var emptyRow = $("[data-table-empty]", wrap);
      var sortState = { key: null, dir: 1 };

      function valueOf(row, key) {
        var cell = row.querySelector('[data-sort="' + key + '"]');
        var raw = cell ? (cell.getAttribute("data-sort-value") || cell.textContent) : "";
        var num = Number(String(raw).replace(/[^0-9.\-]/g, ""));
        if (raw !== "" && !isNaN(num) && String(raw).match(/[0-9]/)) return { t: "n", v: num };
        return { t: "s", v: String(raw).toLowerCase() };
      }
      function apply() {
        var q = search ? search.value.trim().toLowerCase() : "";
        var filters = {};
        selects.forEach(function (s) { filters[s.getAttribute("data-table-filter")] = s.value; });
        var visible = 0;
        var ordered = rows.slice();
        if (sortState.key) {
          ordered.sort(function (a, b) {
            var va = valueOf(a, sortState.key), vb = valueOf(b, sortState.key);
            var cmp = va.t === "n" && vb.t === "n" ? va.v - vb.v
              : String(va.v).localeCompare(String(vb.v));
            return cmp * sortState.dir;
          });
          ordered.forEach(function (r) { tbody.appendChild(r); });
        }
        ordered.forEach(function (row) {
          var show = true;
          if (q && row.textContent.toLowerCase().indexOf(q) === -1) show = false;
          if (show) {
            for (var k in filters) {
              if (filters[k] && row.getAttribute("data-" + k) !== filters[k]) { show = false; break; }
            }
          }
          row.style.display = show ? "" : "none";
          if (show) visible++;
        });
        if (count) count.textContent = visible + " of " + rows.length + " shown";
        if (emptyRow) emptyRow.style.display = visible ? "none" : "";
      }
      if (search) search.addEventListener("input", apply);
      selects.forEach(function (s) { s.addEventListener("change", apply); });
      $$("th.sortable", table).forEach(function (th) {
        th.setAttribute("tabindex", "0");
        th.setAttribute("role", "button");
        function toggle() {
          var key = th.getAttribute("data-sort-key");
          if (sortState.key === key) sortState.dir *= -1;
          else { sortState.key = key; sortState.dir = 1; }
          $$("th.sortable", table).forEach(function (h) {
            var arrow = $(".arrow", h);
            if (arrow) arrow.textContent = "";
            h.setAttribute("aria-sort", "none");
          });
          var arrow = $(".arrow", th);
          if (arrow) arrow.textContent = sortState.dir === 1 ? "▲" : "▼";
          th.setAttribute("aria-sort", sortState.dir === 1 ? "ascending" : "descending");
          apply();
        }
        th.addEventListener("click", toggle);
        th.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
        });
      });
      var reset = $("[data-table-reset]", wrap);
      if (reset) reset.addEventListener("click", function () {
        if (search) search.value = "";
        selects.forEach(function (s) { s.selectedIndex = 0; });
        sortState = { key: null, dir: 1 };
        $$("th.sortable .arrow", table).forEach(function (a) { a.textContent = ""; });
        apply();
      });
      apply();
    });
  }

  /* ---------- Transaction drawer ---------- */
  function initTxnDrawer() {
    var drawer = $("[data-drawer='txn']");
    if (!drawer) return;
    document.addEventListener("click", function (ev) {
      var row = ev.target.closest("tr[data-txn]");
      if (!row || ev.target.closest("form, button, select, a, input")) return;
      var data = null;
      try { data = JSON.parse(row.getAttribute("data-txn")); } catch (e) { return; }
      $$("[data-txn-field]", drawer).forEach(function (node) {
        var key = node.getAttribute("data-txn-field");
        var val = data[key];
        if (key === "amount") {
          node.textContent = fmtRs(val);
          node.className = "num " + (Number(val) >= 0 ? "amt-pos" : "amt-neg");
        } else if (key === "origin") {
          node.innerHTML = "";
          var b = document.createElement("span");
          b.className = "badge " + val;
          b.textContent = val;
          node.appendChild(b);
        } else {
          node.textContent = val === "" || val === null || val === undefined ? "—" : val;
        }
      });
      var form = $("[data-txn-form]", drawer);
      if (form && data.id) {
        form.action = form.getAttribute("data-action-template").replace("__ID__", data.id);
        var sel = $("select[name='category']", form);
        if (sel) sel.value = data.category || "other";
      }
      var title = $("[data-txn-title]", drawer);
      if (title) title.textContent = data.description || ("Transaction #" + data.id);
      openDrawer();
    });
    document.addEventListener("keydown", function (ev) {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches("tr[data-txn]")) {
        ev.preventDefault();
        ev.target.click();
      }
    });
  }

  /* ---------- Simulator (live via API, POST fallback) ---------- */
  function initSimulator() {
    var root = $("[data-simulator]");
    if (!root) return;
    var endpoint = root.getAttribute("data-simulator");
    var controls = $$("[data-sim-input]", root);
    var timer = null;
    var lastGood = null;

    function readInputs() {
      var out = {};
      controls.forEach(function (c) {
        if (c.type === "range" || c.type === "number") out[c.name] = Number(c.value) || 0;
      });
      return out;
    }
    function syncPair(changed) {
      var name = changed.name;
      var mates = $$('[name="' + name + '"]', root);
      mates.forEach(function (m) {
        if (m !== changed && (m.type === "range" || m.type === "number")) {
          m.value = changed.value;
          if (m.type === "range") paintRange(m);
        }
      });
      var out = $('output[data-for="' + name + '"]', root);
      if (out) {
        var v = Number(changed.value) || 0;
        out.textContent = name === "dining_pct" ? (v > 0 ? "+" + v + "%" : v + "%")
          : fmtSignedRs(v);
      }
    }
    function setStatus(text) {
      var s = $("[data-sim-status]", root);
      if (s) s.textContent = text;
    }
    function render(result, base) {
      lastGood = result;
      function set(id, text) {
        var n = document.getElementById(id);
        if (n) n.textContent = text;
      }
      set("sim-new-income", fmtRs(result.new_income));
      set("sim-new-expenses", fmtRs(result.new_expenses));
      set("sim-new-savings", fmtRs(result.new_savings));
      var delta = document.getElementById("sim-delta");
      if (delta) {
        var d = Number(result.delta_vs_base) || 0;
        delta.textContent = fmtSignedRs(d);
        delta.className = d >= 0 ? "delta-pos" : "delta-neg";
      }
      var warn = $("[data-sim-warning]", root);
      if (warn) {
        if (result.warning) { warn.style.display = ""; set("sim-warning-text", result.warning); }
        else warn.style.display = "none";
      }
      var list = $("[data-sim-assumptions]", root);
      if (list) {
        list.innerHTML = "";
        (result.assumptions || []).forEach(function (a) {
          var li = document.createElement("li");
          li.textContent = a;
          list.appendChild(li);
        });
      }
      var goals = $("[data-sim-goals]", root);
      if (goals) {
        goals.innerHTML = "";
        var impacts = result.goal_impacts || [];
        if (!impacts.length) {
          goals.innerHTML = '<p class="muted">No goals yet — scenario savings still apply once you create one.</p>';
        }
        impacts.forEach(function (g) {
          var card = document.createElement("div");
          card.className = "rec-card";
          var pill = g.on_track ? '<span class="pill ok">On track</span>' : '<span class="pill bad">Off track</span>';
          card.innerHTML = '<div class="rec-top"><span class="rec-merchant"></span>' + pill + "</div>" +
            '<div class="rec-meta"><span>Needs / month</span><b>' + fmtRs(g.required_monthly) + "</b>" +
            "<span>Projected</span><b>" + (g.projected_date || "n/a") + "</b></div>";
          card.querySelector(".rec-merchant").textContent = g.name;
          goals.appendChild(card);
        });
      }
      var chartHost = $("[data-sim-chart]", root);
      if (chartHost && base) {
        chartHost.setAttribute("data-labels", JSON.stringify(["Income", "Expenses", "Savings"]));
        chartHost.setAttribute("data-series", JSON.stringify([
          { name: "Baseline", color: "#94a8a4", values: [base.income, base.expenses, Math.max(base.savings, 0)] },
          { name: "Scenario", color: "#178377", values: [result.new_income, result.new_expenses, Math.max(result.new_savings, 0)] }
        ]));
        renderSimChart(chartHost);
        var note = $("[data-sim-chart-note]", root);
        if (note && (base.savings < 0 || result.new_savings < 0)) {
          note.textContent = "Negative savings are floored at zero in the chart; exact figures are in the table.";
          note.style.display = "";
        } else if (note) note.style.display = "none";
      }
      setStatus("Updated just now · figures from your data");
    }
    function refresh() {
      var payload = readInputs();
      var base = null;
      try { base = JSON.parse(root.getAttribute("data-base") || "null"); } catch (e) { /* keep null */ }
      setStatus("Recalculating…");
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify(payload)
      }).then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      }).then(function (result) {
        render(result, base);
      }).catch(function () {
        if (lastGood) setStatus("Could not refresh — showing last result. Use Run scenario to retry.");
        else setStatus("Could not reach the server — use Run scenario.");
      });
    }
    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 220);
    }
    controls.forEach(function (c) {
      syncPair(c);
      c.addEventListener("input", function () { syncPair(c); schedule(); });
      c.addEventListener("change", function () { syncPair(c); schedule(); });
    });
    var reset = $("[data-sim-reset]", root);
    if (reset) reset.addEventListener("click", function () {
      controls.forEach(function (c) {
        c.value = c.getAttribute("data-default") || 0;
        syncPair(c);
      });
      refresh();
    });
    refresh();
  }

  /* ---------- Assistant ---------- */
  function initAssistant() {
    var form = $("[data-assistant-form]");
    if (form) {
      var input = $("input[name='q']", form);
      var btn = $("button[type='submit']", form);
      $$("[data-suggest]").forEach(function (chip) {
        chip.addEventListener("click", function () {
          if (input) {
            input.value = chip.getAttribute("data-suggest");
            input.focus();
          }
        });
      });
      form.addEventListener("submit", function () {
        if (btn) { btn.disabled = true; btn.textContent = "Thinking…"; }
        var chat = $("[data-chat]");
        if (chat && input && input.value.trim()) {
          var q = document.createElement("div");
          q.className = "msg-q";
          q.textContent = input.value.trim();
          var p = document.createElement("div");
          p.className = "msg-pending";
          p.setAttribute("aria-label", "Assistant is thinking");
          p.innerHTML = "<i></i><i></i><i></i>";
          chat.appendChild(q);
          chat.appendChild(p);
          chat.scrollTop = chat.scrollHeight;
        }
      });
      var chat = $("[data-chat]");
      if (chat) chat.scrollTop = chat.scrollHeight;  // auto-scroll to latest
    }
  }

  /* ---------- Back to top (growing-graph button) ---------- */
  function initToTop() {
    var btn = $("[data-to-top]");
    if (!btn) return;
    function onScroll() {
      btn.classList.toggle("show", window.scrollY > 400);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  /* ---------- Profile menu: close on outside click / Escape ---------- */
  function initProfileMenu() {
    var menu = $("[data-profile-menu]");
    if (!menu) return;
    document.addEventListener("click", function (ev) {
      if (menu.open && !menu.contains(ev.target)) menu.open = false;
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && menu.open) menu.open = false;
    });
  }

  /* ---------- Recurring + generic card filter ---------- */
  function initCardFilters() {
    $$("[data-card-filter]").forEach(function (group) {
      var target = document.querySelector(group.getAttribute("data-card-filter"));
      if (!target) return;
      var cards = $$(":scope > *", target);
      var count = document.querySelector(group.getAttribute("data-count"));
      $$("[data-filter-value]", group).forEach(function (chip) {
        chip.addEventListener("click", function () {
          $$("[data-filter-value]", group).forEach(function (c) {
            c.setAttribute("aria-pressed", "false");
            c.classList.remove("on");
          });
          chip.setAttribute("aria-pressed", "true");
          chip.classList.add("on");
          var want = chip.getAttribute("data-filter-value");
          var visible = 0;
          cards.forEach(function (card) {
            var show = !want || card.getAttribute("data-status") === want;
            card.style.display = show ? "" : "none";
            if (show) visible++;
          });
          if (count) count.textContent = visible + " of " + cards.length + " shown";
          var empty = document.querySelector(group.getAttribute("data-empty"));
          if (empty) empty.style.display = visible ? "none" : "";
        });
      });
    });
  }

  /* ---------- Confirm destructive forms ---------- */
  function initConfirm() {
    $$("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        var msg = form.getAttribute("data-confirm") || "Are you sure?";
        if (!window.confirm(msg)) ev.preventDefault();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    initTabs();
    initDialogs();
    initDrawer();
    initDropzones();
    initHashFlash();
    initRanges();
    initCharts();
    initDataTables();
    initTxnDrawer();
    initSimulator();
    initAssistant();
    initCardFilters();
    initConfirm();
    initToTop();
    initProfileMenu();
  });
})();
