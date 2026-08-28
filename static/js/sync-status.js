(function () {
  function ensureBar() {
    let bar = document.getElementById("rx-sync-bar");
    if (bar) return bar;
    bar = document.createElement("div");
    bar.id = "rx-sync-bar";
    bar.className = "rx-sync-bar rx-sync-online";
    bar.setAttribute("role", "status");
    bar.setAttribute("aria-live", "polite");
    document.body.prepend(bar);
    document.body.classList.add("has-sync-bar");
    return bar;
  }

  function labelFor(state, pendingCount) {
    if (state === "offline-pending") {
      const n = pendingCount || 0;
      return n
        ? "🟠 Offline – " + n + " record" + (n === 1 ? "" : "s") + " waiting to sync"
        : "🟠 Offline – Pending Changes";
    }
    if (state === "offline") return "🔴 Offline Mode";
    if (state === "syncing") return "🟡 Synchronizing...";
    if (state === "synced") return "🟢 Synced";
    return "🟢 Online & Synced";
  }

  async function pendingCount() {
    try {
      if (!window.RxVaultOffline) return 0;
      const pending = await window.RxVaultOffline.listPending();
      return pending.length;
    } catch (err) {
      return 0;
    }
  }

  async function render(state) {
    const bar = ensureBar();
    const count = await pendingCount();
    const online = navigator.onLine;
    let next = state;
    if (!next) {
      if (!online && count > 0) next = "offline-pending";
      else if (!online) next = "offline";
      else if (window.__rxSyncing) next = "syncing";
      else if (count > 0) next = "syncing";
      else next = "online";
    }
    bar.className = "rx-sync-bar rx-sync-" + next;
    bar.textContent = labelFor(next, count);
    window.__rxSyncState = next;
  }

  window.RxVaultStatus = {
    render,
    setSyncing(flag) {
      window.__rxSyncing = !!flag;
      render(flag ? "syncing" : null);
    },
  };

  window.addEventListener("online", () => {
    render("syncing");
    if (typeof window.rxvaultSyncNow === "function") {
      window.rxvaultSyncNow();
    }
  });
  window.addEventListener("offline", () => render());
  document.addEventListener("DOMContentLoaded", () => render());
})();
