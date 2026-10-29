(function () {
  "use strict";

  const GROUPS = window.CHALLENGE_GROUPS || [];
  const ROW_LABELS = window.CHALLENGE_ROW_LABELS || {};
  const STORAGE_KEY = "edhparse.challenge.v1";
  const PIP = {
    W: ["#fbf8e1", "#cfc79a"],
    U: ["#5aa3e0", "#1f5f9e"],
    B: ["#6b5b73", "#231a28"],
    R: ["#ee6a4f", "#a82a17"],
    G: ["#4fb877", "#1b6e3c"],
    C: ["#d7dbe3", "#8d95a5"],
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const img = (url) => (url ? "/api/img?u=" + encodeURIComponent(url) : "");

  function defaultState() {
    return {
      version: 1,
      title: "32 Color Identity Challenge",
      subtitle: "",
      theme: "midnight",
      accent: "#e0b252",
      width: 2400,
      scale: 2,
      showThemes: true,
      showNames: false,
      cardStyle: "card",
      perRow: 7,
      showArtist: false,
      hideEmpty: false,
      showCredit: true,
      entries: [],
    };
  }

  let state = defaultState();
  let selectedId = null;
  let saveTimer = 0;

  // ------------------------------------------------------------ helpers
  function setStatus(text, isError) {
    const el = $("#ch-status");
    el.textContent = text || "";
    el.classList.toggle("err", !!isError);
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      /* non-JSON error body */
    }
    if (!res.ok) throw new Error((data && data.error) || res.status + " " + res.statusText);
    return data;
  }

  // Full-card image for a face; derived from the art-crop URL when older data has no `img`.
  function fullImg(f) {
    if (!f) return "";
    if (f.img) return f.img;
    return f.art ? f.art.replace("/art_crop/", "/large/") : "";
  }

  function frontName(card) {
    return (card.faces && card.faces[0] && card.faces[0].name) || card.name || "";
  }

  function displayName(entry) {
    return entry.cards.map(frontName).join(" & ");
  }

  function backNames(entry) {
    const out = [];
    entry.cards.forEach((c) => {
      if (c.faces && c.faces.length > 1) out.push(c.faces.slice(1).map((f) => f.name).join(" / "));
    });
    return out.join(" · ");
  }

  function visibleArts(entry) {
    const arts = [];
    entry.cards.forEach((c) => {
      const faces = c.faces || [];
      (entry.face_mode === "front" ? faces.slice(0, 1) : faces).forEach((f) => arts.push(f));
    });
    return arts;
  }

  function hasMultipleFaces(entry) {
    return entry.cards.reduce((n, c) => n + (c.faces ? c.faces.length : 1), 0) > 1;
  }

  function pipSvg(color) {
    const [light, dark] = PIP[color] || PIP.C;
    const id = "g" + color + Math.random().toString(36).slice(2, 7);
    return (
      '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><defs><radialGradient id="' + id + '" cx="35%" cy="30%" r="80%">' +
      '<stop offset="0" stop-color="' + light + '"/><stop offset="1" stop-color="' + dark + '"/></radialGradient></defs>' +
      '<circle cx="12" cy="12" r="10.5" fill="url(#' + id + ')" stroke="rgba(0,0,0,0.55)" stroke-width="1.5"/></svg>'
    );
  }

  function pipsHtml(group) {
    const colors = group.colors && group.colors.length ? group.colors : ["C"];
    return '<span class="pips">' + colors.map(pipSvg).join("") + "</span>";
  }

  // ------------------------------------------------------------ infographic
  function entriesByIdentity() {
    const map = {};
    state.entries.forEach((e) => {
      (map[e.identity] = map[e.identity] || []).push(e);
    });
    return map;
  }

  function tileHtml(entry, group) {
    const arts = visibleArts(entry);
    const art = state.cardStyle === "art";
    const imgs = arts
      .map((a) => '<img src="' + esc(img(art ? a.art : fullImg(a))) + '" alt="" crossorigin="anonymous" onerror="this.classList.add(\'broken\')">')
      .join("");
    const multi = hasMultipleFaces(entry);
    const back = backNames(entry);
    const artist = state.showArtist && arts[0] && arts[0].artist ? '<span class="artist">Art: ' + esc(arts[0].artist) + "</span>" : "";
    const showTheme = state.showThemes && entry.theme;
    const capText =
      (state.showNames ? '<div class="nm">' + esc(displayName(entry)) + "</div>" + (back ? '<div class="nm2">⇄ ' + esc(back) + "</div>" : "") : "") +
      artist;
    const cap =
      '<div class="cap"><div class="cap-row">' + (group ? pipsHtml(group) : "") +
      (showTheme ? '<span class="th">' + esc(entry.theme) + "</span>" : "") + "</div>" + capText + "</div>";
    return (
      '<div class="tile' + (art ? " mode-art" : "") + (entry.id === selectedId ? " sel" : "") + '" data-id="' + esc(entry.id) + '">' +
      '<div class="faces">' + imgs + "</div>" +
      (multi ? '<span class="flip" title="Double-faced / partner pair">⇄</span>' : "") +
      cap + "</div>"
    );
  }

  function renderInfographic() {
    const el = $("#infographic");
    el.dataset.theme = state.theme;
    el.style.width = state.width + "px";
    el.style.setProperty("--accent", state.accent);

    const byId = entriesByIdentity();
    const rows = [0, 1, 2, 3];

    let html =
      '<div class="ig-head"><div><h1 class="ig-title">' + esc(state.title) + "</h1>" +
      (state.subtitle ? '<p class="ig-sub">' + esc(state.subtitle) + "</p>" : "") +
      "</div></div>" +
      '<div class="ig-rule"></div>';

    const gap = 13;
    const pad = 56;
    const cols = Math.max(3, Math.min(12, Number(state.perRow) || 7));
    const inner = state.width - pad * 2;
    const card = Math.floor((inner - gap * (cols - 1)) / cols);
    const u = Math.max(0.6, card / 300);
    const ordered = [];
    rows.forEach((r) => GROUPS.filter((g) => g.row === r).forEach((g) => ordered.push(g)));
    const filledGroups = ordered.filter((g) => (byId[g.key] || []).length);
    const openGroups = ordered.filter((g) => !(byId[g.key] || []).length);
    html += '<div class="ig-grid" style="--card:' + card + "px;--u:" + u.toFixed(4) + ";--gap:" + gap + "px;--cols:" + cols + '">';
    filledGroups.forEach((g) => {
      byId[g.key].forEach((entry) => {
        const span = Math.min(cols, visibleArts(entry).length || 1);
        html +=
          '<div class="cell" style="grid-column: span ' + span + '">' +
          tileHtml(entry, g) + "</div>";
      });
    });
    html += "</div>";
    if (openGroups.length && !state.hideEmpty) {
      html +=
        '<div class="ig-open" style="--u:' + u.toFixed(4) + '"><span class="ig-open-label">Still open</span>' +
        openGroups.map((g) => '<span class="grp empty"><span class="grp-head">' + pipsHtml(g) + '<span class="grp-name">' + esc(g.name) + "</span></span></span>").join("") +
        "</div>";
    }

    if (state.showCredit) {
      html +=
        '<div class="ig-foot"><span>Card art &amp; data: Scryfall · Archetypes: EDHREC</span>' +
        "<span>Magic: The Gathering is © Wizards of the Coast</span></div>";
    }
    el.innerHTML = html;
    fitStage();
  }

  function fitStage() {
    const stage = $("#stage");
    const scaler = $("#scaler");
    const ig = $("#infographic");
    const avail = stage.clientWidth || 1000;
    const k = Math.min(1, avail / state.width);
    scaler.style.transform = "scale(" + k + ")";
    scaler.style.width = state.width + "px";
    stage.style.height = Math.ceil(ig.offsetHeight * k) + "px";
  }

  // ------------------------------------------------------------ inspector
  function selectedEntry() {
    return state.entries.find((e) => e.id === selectedId) || null;
  }

  function renderInspector() {
    const box = $("#inspector");
    const e = selectedEntry();
    if (!e) {
      box.innerHTML = '<h2>Selected commander</h2><p class="ch-hint">Click a commander on the board to choose its art and archetype.</p>';
      return;
    }
    const group = GROUPS.find((g) => g.key === e.identity);
    const themeChips = (e.themes || [])
      .map(
        (t) =>
          '<button type="button" class="ch-chip' + (e.theme === t.label ? " on" : "") + '" data-theme="' + esc(t.label) + '">' +
          esc(t.label) + (t.count ? "<small>" + t.count.toLocaleString() + "</small>" : "") + "</button>"
      )
      .join("");
    const customActive = e.theme && !(e.themes || []).some((t) => t.label === e.theme);
    const artRows = e.cards
      .map((c, ci) => {
        const first = c.faces && c.faces[0];
        return (
          '<div class="ch-art-row"><img src="' + esc(img(first && first.art)) + '" alt="">' +
          '<div class="meta"><b>' + esc(frontName(c)) + "</b><span>" + esc((c.set || "").toUpperCase()) +
          (first && first.artist ? " · " + esc(first.artist) : "") + "</span></div>" +
          '<button type="button" class="btn btn-ghost" data-art="' + ci + '">Choose art</button></div>'
        );
      })
      .join("");
    box.innerHTML =
      '<h2>Selected commander</h2>' +
      '<p class="ch-sel-title">' + esc(e.title) + "</p>" +
      '<p class="ch-sel-sub">' + esc(group ? group.name : e.identity) + " (" + esc(e.identity || "colorless") + ") · " +
      '<a href="' + esc(e.edhrec_url) + '" target="_blank" rel="noopener">EDHREC</a></p>' +
      '<label>Archetype (EDHREC top ' + (e.themes || []).length + ")</label>" +
      '<div class="ch-chips">' + themeChips +
      '<button type="button" class="ch-chip' + (!e.theme ? " on" : "") + '" data-theme="">None</button></div>' +
      '<label for="insp-custom">…or type your own</label>' +
      '<input type="text" id="insp-custom" value="' + (customActive ? esc(e.theme) : "") + '" placeholder="e.g. Treasure Tokens">' +
      "<label>Art</label>" + artRows +
      (hasMultipleFaces(e)
        ? '<label for="insp-faces">Faces / cards shown</label><select id="insp-faces"><option value="all"' +
          (e.face_mode !== "front" ? " selected" : "") + '>All faces (front + back / both cards)</option><option value="front"' +
          (e.face_mode === "front" ? " selected" : "") + ">Front only</option></select>"
        : "") +
      '<div class="ch-row-actions">' +
      '<button type="button" class="btn btn-ghost" id="insp-up">↑ Move up</button>' +
      '<button type="button" class="btn btn-ghost" id="insp-down">↓ Move down</button>' +
      '<button type="button" class="btn btn-ghost ch-danger" id="insp-remove">Remove</button></div>';
  }

  function changed(opts) {
    renderInfographic();
    if (!opts || opts.inspector !== false) renderInspector();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(autosave, 400);
  }

  function select(id) {
    selectedId = id;
    renderInfographic();
    renderInspector();
  }

  function moveWithinGroup(delta) {
    const e = selectedEntry();
    if (!e) return;
    const idxs = state.entries.map((x, i) => (x.identity === e.identity ? i : -1)).filter((i) => i >= 0);
    const pos = idxs.indexOf(state.entries.indexOf(e));
    const target = idxs[pos + delta];
    if (target === undefined) return;
    const from = state.entries.indexOf(e);
    [state.entries[from], state.entries[target]] = [state.entries[target], state.entries[from]];
    changed();
  }

  // ------------------------------------------------------------ art picker
  const artModal = $("#art-modal");
  async function openArtPicker(entry, cardIndex) {
    const card = entry.cards[cardIndex];
    $("#art-title").textContent = "Choose art — " + frontName(card);
    const grid = $("#art-grid");
    const msg = $("#art-msg");
    grid.innerHTML = "";
    msg.classList.remove("err");
    msg.textContent = "Loading artworks from Scryfall…";
    artModal.hidden = false;
    if (!card.oracle_id) {
      msg.textContent = "This card has no oracle id, so alternate art can't be listed.";
      return;
    }
    try {
      const data = await api("/api/challenge/prints?oracle_id=" + encodeURIComponent(card.oracle_id));
      const prints = data.prints || [];
      msg.textContent = prints.length + " artwork" + (prints.length === 1 ? "" : "s") + " — click one to use it" +
        (card.faces.length > 1 ? " (applies to both faces)." : ".");
      grid.innerHTML = prints
        .map(
          (p, i) =>
            '<button type="button" class="ch-print' + (p.id === card.print_id ? " on" : "") + '" data-i="' + i + '">' +
            '<div class="arts">' + p.faces.map((f) => '<img loading="lazy" src="' + esc(img(fullImg(f).replace("/large/", "/normal/"))) + '" alt="">').join("") + "</div>" +
            '<div class="cap">' + esc(p.set_name || "") + "<span>" + esc((p.set || "").toUpperCase()) + " · " +
            esc((p.released_at || "").slice(0, 4)) + (p.artist ? " · " + esc(p.artist) : "") +
            (p.style ? '<b class="ch-style">' + esc(p.style) + "</b>" : "") + "</span></div></button>"
        )
        .join("");
      grid.onclick = (ev) => {
        const btn = ev.target.closest(".ch-print");
        if (!btn) return;
        const p = prints[Number(btn.dataset.i)];
        card.print_id = p.id;
        card.set = p.set;
        card.faces = card.faces.map((f, fi) => {
          const nf = p.faces[fi] || p.faces[0];
          return Object.assign({}, f, { art: nf.art, img: fullImg(nf), artist: nf.artist || f.artist });
        });
        artModal.hidden = true;
        changed();
      };
    } catch (err) {
      msg.classList.add("err");
      msg.textContent = "Couldn't load artworks: " + err.message;
    }
  }
  $("#art-close").addEventListener("click", () => (artModal.hidden = true));
  artModal.addEventListener("click", (ev) => {
    if (ev.target === artModal) artModal.hidden = true;
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") artModal.hidden = true;
  });

  // ------------------------------------------------------------ loading / adding commanders
  function mergeEntries(newEntries, replace) {
    if (replace) {
      state.entries = newEntries.slice();
    } else {
      const have = new Set(state.entries.map((e) => e.id));
      newEntries.forEach((e) => {
        if (!have.has(e.id)) state.entries.push(e);
      });
    }
  }

  function showLoadResult(data, verb) {
    const errs = data.errors || [];
    const msg = $("#load-msg");
    msg.classList.toggle("err", errs.length > 0);
    msg.innerHTML =
      esc(verb + " " + (data.entries || []).length + " commander(s).") +
      (errs.length ? "<ul>" + errs.map((e) => "<li>" + esc(e) + "</li>").join("") + "</ul>" : "");
  }

  async function loadList(ev) {
    if (ev) ev.preventDefault();
    const path = $("#list-path").value.trim();
    if (!path) return;
    setStatus("Loading " + path + "… (first run fetches EDHREC pages, this can take a moment)");
    try {
      const data = await api("/api/challenge/load?path=" + encodeURIComponent(path));
      mergeEntries(data.entries || [], true);
      selectedId = null;
      showLoadResult(data, "Loaded");
      setStatus("Loaded " + state.entries.length + " commanders.");
      changed();
    } catch (err) {
      setStatus(err.message, true);
      $("#load-msg").textContent = err.message;
      $("#load-msg").classList.add("err");
    }
  }

  async function addPasted() {
    const text = $("#add-text").value;
    if (!text.trim()) return;
    setStatus("Looking up commanders…");
    try {
      const data = await api("/api/challenge/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      mergeEntries(data.entries || [], false);
      showLoadResult(data, "Added");
      $("#add-text").value = "";
      setStatus("Added.");
      changed();
    } catch (err) {
      setStatus(err.message, true);
      $("#load-msg").textContent = err.message;
      $("#load-msg").classList.add("err");
    }
  }

  // ------------------------------------------------------------ persistence
  function autosave() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* storage unavailable: fine */
    }
  }

  function applyState(next) {
    state = Object.assign(defaultState(), next || {});
    state.entries = Array.isArray(state.entries) ? state.entries : [];
    selectedId = null;
    syncControls();
    renderInfographic();
    renderInspector();
  }

  async function saveLayout() {
    const name = (state.title || "challenge").trim();
    try {
      const data = await api("/api/challenge/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, state }),
      });
      setStatus("Saved " + data.path);
      refreshSaves();
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function refreshSaves() {
    try {
      const data = await api("/api/challenge/saves");
      const sel = $("#open-select");
      sel.innerHTML = '<option value="">Open saved…</option>' + data.saves.map((s) => '<option value="' + esc(s) + '">' + esc(s) + "</option>").join("");
    } catch (e) {
      /* ignore */
    }
  }

  // ------------------------------------------------------------ export
  async function renderBlob() {
    const node = $("#infographic");
    if (!window.htmlToImage) throw new Error("PNG exporter failed to load");
    const prevSel = selectedId;
    node.classList.add("exporting");
    const imgs = Array.from(node.querySelectorAll("img"));
    await Promise.all(imgs.map((i) => (i.decode ? i.decode().catch(() => {}) : Promise.resolve())));
    try {
      const blob = await window.htmlToImage.toBlob(node, {
        pixelRatio: Number(state.scale) || 2,
        width: node.offsetWidth,
        height: node.offsetHeight,
        cacheBust: false,
        includeQueryParams: true,
        style: { transform: "none", margin: "0" },
      });
      if (!blob) throw new Error("the browser returned an empty image");
      return blob;
    } finally {
      node.classList.remove("exporting");
      selectedId = prevSel;
    }
  }

  function fileBase() {
    return (state.title || "challenge").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "challenge";
  }

  async function exportPng() {
    setStatus("Rendering image…");
    try {
      const blob = await renderBlob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileBase() + ".png";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setStatus("Exported " + a.download + " (" + (blob.size / 1048576).toFixed(1) + " MB)");
      try {
        const saved = await api("/api/challenge/export?name=" + encodeURIComponent(fileBase()), { method: "POST", body: blob });
        setStatus("Exported " + a.download + " — also saved to " + saved.path);
      } catch (e) {
        /* the download already worked */
      }
    } catch (err) {
      setStatus("Export failed: " + err.message, true);
    }
  }

  async function copyPng() {
    setStatus("Rendering image…");
    try {
      const blob = await renderBlob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      setStatus("Copied to clipboard — paste it into Discord.");
    } catch (err) {
      setStatus("Copy failed (" + err.message + "). Use Export PNG instead.", true);
    }
  }

  // ------------------------------------------------------------ controls wiring
  const bind = [
    ["#opt-title", "title", "value"],
    ["#opt-subtitle", "subtitle", "value"],
    ["#opt-theme", "theme", "value"],
    ["#opt-accent", "accent", "value"],
    ["#opt-width", "width", "number"],
    ["#opt-scale", "scale", "number"],
    ["#opt-style", "cardStyle", "value"],
    ["#opt-perrow", "perRow", "number"],
    ["#opt-themes", "showThemes", "checked"],
    ["#opt-names", "showNames", "checked"],
    ["#opt-artist", "showArtist", "checked"],
    ["#opt-hide-empty", "hideEmpty", "checked"],
    ["#opt-credit", "showCredit", "checked"],
  ];

  function syncControls() {
    bind.forEach(([sel, key, kind]) => {
      const el = $(sel);
      if (kind === "checked") el.checked = !!state[key];
      else el.value = state[key];
    });
  }

  bind.forEach(([sel, key, kind]) => {
    const el = $(sel);
    el.addEventListener(kind === "checked" || el.tagName === "SELECT" ? "change" : "input", () => {
      state[key] = kind === "checked" ? el.checked : kind === "number" ? Number(el.value) : el.value;
      changed({ inspector: false });
    });
  });

  $("#load-form").addEventListener("submit", loadList);
  $("#btn-add").addEventListener("click", addPasted);
  $("#btn-save").addEventListener("click", saveLayout);
  $("#btn-export").addEventListener("click", exportPng);
  $("#btn-copy").addEventListener("click", copyPng);
  $("#open-select").addEventListener("change", async (ev) => {
    const name = ev.target.value;
    if (!name) return;
    try {
      const data = await api("/api/challenge/open?name=" + encodeURIComponent(name));
      applyState(data.state);
      setStatus("Opened " + name);
      autosave();
    } catch (err) {
      setStatus(err.message, true);
    }
    ev.target.value = "";
  });

  $("#infographic").addEventListener("click", (ev) => {
    const tile = ev.target.closest(".tile");
    select(tile ? tile.dataset.id : null);
  });

  $("#inspector").addEventListener("click", (ev) => {
    const e = selectedEntry();
    if (!e) return;
    const chip = ev.target.closest("[data-theme]");
    if (chip) {
      e.theme = chip.dataset.theme;
      changed();
      return;
    }
    const artBtn = ev.target.closest("[data-art]");
    if (artBtn) return openArtPicker(e, Number(artBtn.dataset.art));
    if (ev.target.id === "insp-up") return moveWithinGroup(-1);
    if (ev.target.id === "insp-down") return moveWithinGroup(1);
    if (ev.target.id === "insp-remove") {
      state.entries = state.entries.filter((x) => x.id !== e.id);
      selectedId = null;
      changed();
    }
  });
  $("#inspector").addEventListener("input", (ev) => {
    const e = selectedEntry();
    if (e && ev.target.id === "insp-custom") {
      e.theme = ev.target.value;
      changed({ inspector: false });
    }
  });
  $("#inspector").addEventListener("change", (ev) => {
    const e = selectedEntry();
    if (e && ev.target.id === "insp-faces") {
      e.face_mode = ev.target.value;
      changed();
    }
  });

  window.addEventListener("resize", fitStage);

  // ------------------------------------------------------------ boot
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) state = Object.assign(defaultState(), JSON.parse(saved));
  } catch (e) {
    /* ignore corrupt/blocked storage */
  }
  if (!Array.isArray(state.entries)) state.entries = [];
  syncControls();
  renderInfographic();
  renderInspector();
  refreshSaves();
  if (state.entries.length) setStatus("Restored your last session (" + state.entries.length + " commanders).");

  // exposed for tests / debugging
  window.__challenge = { get state() { return state; }, applyState, select };
})();
