(function () {
  const overlay = document.getElementById("run-overlay");
  const titleEl = document.getElementById("run-title");
  const subtitleEl = document.getElementById("run-subtitle");
  const countEl = document.getElementById("run-count");
  const elapsedEl = document.getElementById("run-elapsed");
  const barFill = document.getElementById("run-bar-fill");
  const logEl = document.getElementById("run-log");
  if (!overlay) return;

  let elapsedTimer = 0;
  let pollTimer = 0;

  function formatElapsed(ms) {
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return sec + "s";
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + "m " + String(s).padStart(2, "0") + "s";
  }

  function setLog(text) {
    if (!logEl) return;
    const raw = String(text || "").trim();
    if (!raw) {
      logEl.hidden = true;
      logEl.textContent = "";
      return;
    }
    const lines = raw.split(/\r?\n/);
    const tail = lines.slice(-18).join("\n");
    logEl.hidden = false;
    logEl.textContent = tail;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setProgress(current, total, message, logText) {
    const cur = Number(current) || 0;
    const tot = Number(total) || 0;
    if (tot > 0) {
      countEl.textContent = cur + " / " + tot;
      const pct = Math.max(0, Math.min(100, (100 * cur) / tot));
      barFill.classList.remove("indeterminate");
      barFill.style.width = pct + "%";
    } else {
      countEl.textContent = cur > 0 ? String(cur) : "…";
      barFill.classList.add("indeterminate");
      barFill.style.width = "";
    }
    if (message) subtitleEl.textContent = message;
    if (logText !== undefined) setLog(logText);
  }

  function showRunning(label) {
    const startedAt = Date.now();
    titleEl.textContent = label || "Running…";
    subtitleEl.textContent = "Starting…";
    countEl.textContent = "0 / ?";
    elapsedEl.textContent = "0s";
    barFill.classList.add("indeterminate");
    barFill.style.width = "";
    setLog("");
    overlay.hidden = false;
    document.body.style.overflow = "hidden";

    clearInterval(elapsedTimer);
    clearInterval(pollTimer);
    elapsedTimer = setInterval(function () {
      elapsedEl.textContent = formatElapsed(Date.now() - startedAt);
    }, 250);
  }

  function finishWithHtml(html) {
    clearInterval(elapsedTimer);
    clearInterval(pollTimer);
    document.open();
    document.write(html);
    document.close();
  }

  function pollJob(jobId) {
    pollTimer = setInterval(async function () {
      try {
        const res = await fetch("/api/jobs/" + encodeURIComponent(jobId), {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return;
        const data = await res.json();
        setProgress(data.current, data.total, data.message || "", data.log || "");
        if (data.status === "done" || data.status === "error") {
          clearInterval(pollTimer);
          if (data.result_html) {
            finishWithHtml(data.result_html);
          } else {
            subtitleEl.textContent = data.error || data.message || "Finished";
          }
        }
      } catch (err) {
        subtitleEl.textContent = "Progress check failed — still running…";
      }
    }, 400);
  }

  function wireForms() {
    document.querySelectorAll('form[action^="/run/"]').forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();

        const btn = form.querySelector('button[type="submit"], button:not([type])');
        const label =
          form.getAttribute("data-run-label") ||
          (document.querySelector(".brand") && document.querySelector(".brand").textContent) ||
          "Running…";
        showRunning(String(label).trim().replace(/\…$/, "") + "…");
        if (btn) {
          btn.disabled = true;
          btn.textContent = "Running…";
        }

        const body = new FormData(form);
        fetch(form.action, {
          method: "POST",
          body: body,
          headers: { "X-Async-Job": "1", Accept: "application/json" },
        })
          .then(function (res) {
            if (!res.ok) throw new Error("HTTP " + res.status);
            return res.json();
          })
          .then(function (data) {
            if (!data.job_id) throw new Error("No job id returned");
            setProgress(0, 0, "Job started…");
            pollJob(data.job_id);
          })
          .catch(function (err) {
            subtitleEl.textContent = "Failed to start: " + err;
            if (btn) {
              btn.disabled = false;
              btn.textContent = "Run";
            }
          });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireForms);
  } else {
    wireForms();
  }
})();
