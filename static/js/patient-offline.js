(function () {
  function isExpired(pack) {
    if (!pack || !pack.expiry_date) return false;
    const expiry = Date.parse(pack.expiry_date);
    if (Number.isNaN(expiry)) return false;
    return Date.now() > expiry;
  }

  function formatStamp(iso) {
    if (!iso) return "Never";
    try {
      return new Date(iso).toLocaleString();
    } catch (err) {
      return iso;
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderPack(pack, offline) {
    const host = document.getElementById("offlineRxView");
    if (!host || !pack) return;
    const expired = isExpired(pack);
    const banner = offline
      ? "<div class='alert-message alert-danger'>🔴 You are currently offline. Showing your last saved prescription.</div>"
      : "";
    const synced = "<p class='sync-meta'>Last Synced: " + escapeHtml(formatStamp(pack.last_synced)) + "</p>";
    if (expired) {
      host.innerHTML =
        banner +
        synced +
        "<div class='expired-lock'><h2>⚠️ Time of Usage Completed</h2>" +
        "<p>Prescribed duration has expired. Please visit your doctor again for a fresh consultation.</p>" +
        "<p class='muted'>Expiry is calculated from the locally stored prescription expiry date.</p></div>" +
        (offline
          ? "<div style='margin-top: 1.5rem;' class='no-print'>" +
            "<button type='button' class='btn btn-secondary' id='rxOfflineBackBtn'>" +
            "<i class='ri-arrow-left-line'></i> Back to Lookup" +
            "</button></div>"
          : "");
      return;
    }
    const rows = (pack.medicine_list || [])
      .map(
        (item, i) =>
          "<tr><td>" +
          (item.index || i + 1) +
          "</td><td>" +
          escapeHtml(item.name) +
          "</td><td>" +
          escapeHtml(item.dosage) +
          "</td><td>" +
          escapeHtml(item.frequency) +
          "</td><td>" +
          escapeHtml(item.instructions) +
          "</td><td>" +
          escapeHtml(item.days) +
          " Days</td></tr>"
      )
      .join("");
    host.innerHTML =
      banner +
      synced +
      (offline ? "<p class='muted'>Displayed data may be the last synchronized version.</p>" : "") +
      "<div class='rx-box'><table class='rx-table'><thead><tr><th>#</th><th>Medicine & Strength</th><th>Dosage</th><th>Frequency</th><th>How to Use</th><th>Duration</th></tr></thead><tbody>" +
      rows +
      "</tbody></table>" +
      "<p><strong>Prescription Date:</strong> " +
      escapeHtml(formatStamp(pack.prescription_date)) +
      "</p>" +
      "<p><strong>Expiry Date:</strong> " +
      escapeHtml(formatStamp(pack.expiry_date)) +
      "</p>" +
      "<p><strong>General Advice:</strong> " +
      escapeHtml(pack.instructions || "") +
      "</p></div>" +
      (offline
        ? "<div style='margin-top: 1.5rem;' class='no-print'>" +
          "<button type='button' class='btn btn-secondary' id='rxOfflineBackBtn'>" +
          "<i class='ri-arrow-left-line'></i> Back to Lookup" +
          "</button></div>"
        : "");
  }

  async function cacheFromPage() {
    const node = document.getElementById("rx-prescription-pack");
    if (!node || !window.RxVaultOffline) return;
    try {
      const pack = JSON.parse(node.textContent);
      if (!pack || !pack.appointment_id) return;
      pack.last_synced = new Date().toISOString();
      const secret = window.RxVaultOffline.setPatientWrap(pack.appointment_id, pack.mobile || "");
      await window.RxVaultOffline.savePrescription(pack, secret);
      await window.RxVaultOffline.setMeta("patient_last_synced", pack.last_synced);
      const stamp = document.getElementById("lastSyncedStamp");
      if (stamp) stamp.textContent = "Last Synced: " + new Date(pack.last_synced).toLocaleString();
    } catch (err) {
      /* ignore */
    }
  }

  async function showCachedIfOffline() {
    const form = document.querySelector("form[action='/my-prescription']");
    if (!form || navigator.onLine) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const appointmentId = (form.querySelector('[name="appointment_id"]') || {}).value;
      const mobile = (form.querySelector('[name="mobile"]') || {}).value;
      const secret = window.RxVaultOffline.setPatientWrap(appointmentId, mobile);
      const pack = await window.RxVaultOffline.loadPrescription(String(appointmentId || "").trim().toUpperCase(), secret);
      let host = document.getElementById("offlineRxView");
      if (!host) {
        host = document.createElement("div");
        host.id = "offlineRxView";
        form.parentNode.insertBefore(host, form.nextSibling);
      }
      if (!pack) {
        host.innerHTML =
          "<div class='alert-message alert-danger'>🔴 You are currently offline. No locally saved prescription was found for this Appointment ID.</div>" +
          "<div style='margin-top: 1.5rem;' class='no-print'>" +
          "<button type='button' class='btn btn-secondary' id='rxOfflineBackBtn'>" +
          "<i class='ri-arrow-left-line'></i> Back to Lookup" +
          "</button></div>";
        form.style.display = "none";
        document.getElementById("rxOfflineBackBtn").addEventListener("click", () => {
          host.innerHTML = "";
          form.style.display = "block";
        });
        return;
      }
      renderPack(pack, true);
      form.style.display = "none";
      const backBtn = document.getElementById("rxOfflineBackBtn");
      if (backBtn) {
        backBtn.addEventListener("click", () => {
          host.innerHTML = "";
          form.style.display = "block";
        });
      }
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    await cacheFromPage();
    await showCachedIfOffline();
    if (!navigator.onLine) {
      const packs = window.RxVaultOffline ? await window.RxVaultOffline.listPrescriptions() : [];
      const host = document.getElementById("offlineRxView");
      if (host && packs.length === 1) renderPack(packs[0], true);
      const banner = document.getElementById("offlinePatientBanner");
      if (banner) banner.style.display = "block";
    }
    document.querySelectorAll('a[href="/doctor/logout"]').forEach((link) => {
      link.addEventListener("click", async (event) => {
        event.preventDefault();
        try {
          if (window.RxVaultOffline) await window.RxVaultOffline.clearAll();
        } catch (err) {
          /* ignore */
        }
        window.location.href = "/doctor/logout";
      });
    });
  });
})();
