(function () {
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  async function cachePack() {
    if (!navigator.onLine || !window.RxVaultOffline) return;
    try {
      const res = await fetch("/api/doctor/offline-pack", { credentials: "same-origin" });
      if (!res.ok) return;
      const pack = await res.json();
      const secret = await window.RxVaultOffline.getWrapSecret();
      for (const appt of pack.appointments || []) {
        await window.RxVaultOffline.saveAppointment(appt, secret);
        if (appt.history) {
          await window.RxVaultOffline.saveHistory(appt.mobile, appt.history, secret);
        }
      }
      await window.RxVaultOffline.setMeta("doctor_pack_synced_at", new Date().toISOString());
      await window.RxVaultOffline.cleanupStale();
      if (window.RxVaultStatus) window.RxVaultStatus.render("online");
      renderCachedAppointments(pack.appointments || []);
    } catch (err) {
      /* keep local cache */
    }
  }

  function renderCachedAppointments(list) {
    const host = document.getElementById("offlineAppointments");
    if (!host) return;
    if (!list.length) {
      host.innerHTML = "<p class='muted'>No cached appointments yet. They appear here after an online sync.</p>";
      return;
    }
    host.innerHTML = list
      .map((a) => {
        const pending = a.prescription_status === "Pending" ? "Pending" : a.prescription_status || "";
        return (
          '<button type="button" class="offline-appt-chip" data-id="' +
          escapeHtml(a.appointment_id) +
          '">' +
          "<strong>" +
          escapeHtml(a.appointment_id) +
          "</strong> · " +
          escapeHtml(a.name || "Patient") +
          " · " +
          escapeHtml(a.date || "") +
          " " +
          escapeHtml(a.slot || "") +
          ' <span class="chip-status">' +
          escapeHtml(pending) +
          "</span></button>"
        );
      })
      .join("");
    host.querySelectorAll("[data-id]").forEach((btn) => {
      btn.addEventListener("click", () => openCached(btn.getAttribute("data-id")));
    });
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function openCached(appointmentId) {
    const appt = await window.RxVaultOffline.loadAppointment(appointmentId);
    if (!appt) {
      alert("This appointment is not available in the offline cache.");
      return;
    }
    const history = appt.mobile ? await window.RxVaultOffline.loadHistory(appt.mobile) : [];
    paintPatient(appt, history || [], true);
  }

  function paintPatient(patient, previousVisits, fromCache) {
    const mount = document.getElementById("offlinePatientMount");
    if (!mount) return;
    const visitsHtml = (previousVisits || [])
      .map((visit) => {
        const meds = (visit.medicine_list || [])
          .map(
            (med) =>
              "<span class='hist-pill'>" +
              escapeHtml(med.name) +
              " - " +
              escapeHtml(med.dosage) +
              " (" +
              escapeHtml(med.frequency) +
              ")</span>"
          )
          .join(" ");
        return (
          "<div class='hist-card'><strong>" +
          escapeHtml(visit.date) +
          "</strong> · " +
          escapeHtml(visit.hospital_name) +
          "<div>" +
          escapeHtml(visit.problem || "") +
          "</div><div>" +
          meds +
          "</div></div>"
        );
      })
      .join("");

    mount.innerHTML =
      (fromCache
        ? "<div class='alert-message'>Showing cached patient data (last synchronized copy).</div>"
        : "") +
      "<div class='summary-card'><h3 class='section-title'>Current Patient Appointment Record</h3>" +
      "<ul class='details-list'>" +
      row("Appointment ID", patient.appointment_id) +
      row("Patient Name", (patient.name || "") + " (" + (patient.gender || "") + ")") +
      row("Registered Mobile", patient.mobile) +
      row("Hospital & Department", (patient.hospital_name || "") + " · " + (patient.department || "")) +
      row("Schedule", (patient.date || "") + " (" + (patient.slot || "") + ")") +
      row("Chief Complaint / Problem", patient.problem || "General Checkup") +
      "</ul></div>" +
      (visitsHtml
        ? "<div class='summary-card' style='border-left:4px solid var(--secondary)'><h3 class='section-title'>Patient Medical History</h3>" +
          visitsHtml +
          "</div>"
        : "") +
      '<div class="form-card"><h3 class="section-title">Write Detailed Prescription</h3>' +
      (fromCache ? "<p class='pending-tag'>Will be saved as Pending Sync if you are offline.</p>" : "") +
      '<form id="offlineRxForm">' +
      '<input type="hidden" name="appointment_id" value="' +
      escapeHtml(patient.appointment_id) +
      '">' +
      document.getElementById("medicineTemplate") 
        ? "" 
        : "" ;

    const template = document.getElementById("rxFormTemplate");
    if (template) {
      mount.insertAdjacentHTML("beforeend", template.innerHTML.replace("__APPT__", escapeHtml(patient.appointment_id)));
      bindPrescriptionForm(mount.querySelector("form"));
    } else {
      mount.insertAdjacentHTML(
        "beforeend",
        '<form id="offlineRxForm" class="form-card"><input type="hidden" name="appointment_id" value="' +
          escapeHtml(patient.appointment_id) +
          '"><div id="medicineContainerOffline">' +
          medicineRowHtml(1) +
          "</div><button type='button' class='btn btn-outline' id='addMedOffline'>+ Add Another Medicine</button>" +
          '<div class="form-group"><label>Consultation notes</label><textarea name="notes"></textarea></div>' +
          '<div class="form-group"><label>General Advice</label><textarea name="instructions" required></textarea></div>' +
          '<button class="btn btn-block btn-success" type="submit">Save Prescription</button></form>'
      );
      bindPrescriptionForm(document.getElementById("offlineRxForm"));
      const addBtn = document.getElementById("addMedOffline");
      let n = 1;
      if (addBtn) {
        addBtn.addEventListener("click", () => {
          n += 1;
          document.getElementById("medicineContainerOffline").insertAdjacentHTML("beforeend", medicineRowHtml(n));
        });
      }
    }
    mount.scrollIntoView({ behavior: "smooth" });
  }

  function row(key, val) {
    return (
      '<li class="details-row"><span class="key">' +
      escapeHtml(key) +
      '</span><span class="val">' +
      escapeHtml(val) +
      "</span></li>"
    );
  }

  function medicineRowHtml(n) {
    return (
      '<div class="medicine-row" style="background:var(--slate-50);border:1.5px solid var(--slate-200);padding:1.25rem;border-radius:var(--radius);margin-bottom:1.25rem;">' +
      "<strong>Medicine #" +
      n +
      "</strong>" +
      '<div class="form-group"><label>Medicine Name & Strength</label><input class="form-control" name="medicine_name" required></div>' +
      '<div class="form-group"><label>Dosage</label><input class="form-control" name="dosage" required></div>' +
      '<div class="form-group"><label>Frequency</label><input class="form-control" name="frequency" required></div>' +
      '<div class="form-group"><label>How to Use</label><input class="form-control" name="usage_instructions" required></div>' +
      '<div class="form-group"><label>Duration (Days)</label><input class="form-control" type="number" name="duration_days" min="1" max="90" required></div></div>'
    );
  }

  function collectMedicines(form) {
    const names = [...form.querySelectorAll('[name="medicine_name"]')].map((i) => i.value.trim());
    const dosages = [...form.querySelectorAll('[name="dosage"]')].map((i) => i.value.trim());
    const freqs = [...form.querySelectorAll('[name="frequency"]')].map((i) => i.value.trim());
    const usages = [...form.querySelectorAll('[name="usage_instructions"]')].map((i) => i.value.trim());
    const days = [...form.querySelectorAll('[name="duration_days"]')].map((i) => i.value.trim());
    const items = [];
    names.forEach((name, i) => {
      if (!name) return;
      items.push({
        name,
        dosage: dosages[i] || "1 Unit",
        frequency: freqs[i] || "As directed",
        instructions: usages[i] || "Take as directed",
        days: parseInt(days[i] || "5", 10) || 5,
      });
    });
    return items;
  }

  async function saveLocalPrescription(form) {
    const appointmentId = (form.querySelector('[name="appointment_id"]') || {}).value;
    const instructions = (form.querySelector('[name="instructions"]') || {}).value || "";
    const notes = (form.querySelector('[name="notes"]') || {}).value || "";
    const medicines = collectMedicines(form);
    if (!appointmentId || !medicines.length) {
      alert("Please add at least one medicine.");
      return;
    }
    const record = {
      client_sync_id: uuid(),
      appointment_id: appointmentId.trim().toUpperCase(),
      medicines,
      instructions,
      notes,
      created_offline_at: new Date().toISOString(),
    };
    await window.RxVaultOffline.queuePending(record);
    if (window.RxVaultStatus) window.RxVaultStatus.render();
    alert("Saved locally as Pending Sync. It will upload when connectivity returns.");
    if (navigator.onLine) {
      await syncPending();
    }
  }

  function bindPrescriptionForm(form) {
    if (!form || form.dataset.rxBound) return;
    form.dataset.rxBound = "1";
    form.addEventListener("submit", async (event) => {
      if (!navigator.onLine) {
        event.preventDefault();
        await saveLocalPrescription(form);
        return;
      }
      const pending = await window.RxVaultOffline.listPending();
      if (pending.length) {
        event.preventDefault();
        await syncPending();
        form.submit();
      }
    });
  }

  function isQrTokenValid(appt) {
    if (!appt || !appt.qr_token) return { valid: false, message: "This QR pass is not recognized." };
    const status = (appt.qr_token_status || "ACTIVE").toUpperCase();
    if (status === "INVALIDATED") return { valid: false, message: "This QR pass was invalidated after consultation." };
    if (status === "EXPIRED") return { valid: false, message: "This QR pass has expired." };
    if (appt.qr_token_expires_at) {
      try {
        if (new Date() > new Date(appt.qr_token_expires_at)) {
          return { valid: false, message: "This QR pass has expired. Please verify the appointment at the help desk." };
        }
      } catch (e) {}
    }
    return { valid: true, message: "" };
  }

  function paintOfflineError(errorMsg) {
    const mount = document.getElementById("offlinePatientMount");
    if (!mount) return;
    mount.innerHTML = '<div class="alert-message alert-danger"><i class="ri-error-warning-line"></i> ' + escapeHtml(errorMsg) + '</div>';
    mount.scrollIntoView({ behavior: "smooth" });
  }

  async function rxvaultScanOffline(token) {
    if (!window.RxVaultOffline) return;
    const tokenClean = String(token || "").trim();
    const rows = await window.RxVaultOffline.getAll("appointments");
    let matchedAppt = null;
    const secret = await window.RxVaultOffline.getWrapSecret();
    for (const row of rows || []) {
      const appt = await window.RxVaultOffline.unwrapPayload(row, secret);
      if (appt) {
        if (appt.qr_token === tokenClean || appt.appointment_id === tokenClean.toUpperCase()) {
          matchedAppt = appt;
          break;
        }
      }
    }
    if (!matchedAppt) {
      paintOfflineError("This QR pass is invalid or was not issued by RxVault, or is not in the offline cache.");
      return;
    }
    const val = isQrTokenValid(matchedAppt);
    if (!val.valid) {
      paintOfflineError(val.message);
      return;
    }
    const history = matchedAppt.mobile ? await window.RxVaultOffline.loadHistory(matchedAppt.mobile, secret) : [];
    paintPatient(matchedAppt, history || [], true);
  }

  window.rxvaultScanOffline = rxvaultScanOffline;

  async function syncPending() {
    if (!navigator.onLine || !window.RxVaultOffline) return;
    const pending = await window.RxVaultOffline.listPending();
    if (!pending.length) {
      if (window.RxVaultStatus) window.RxVaultStatus.render("online");
      return;
    }
    if (window.RxVaultStatus) window.RxVaultStatus.setSyncing(true);
    for (const record of pending) {
      try {
        const res = await fetch("/api/doctor/sync-prescription", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(record),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && (data.status === "SYNCED" || data.status === "DUPLICATE" || data.status === "CONFLICT")) {
          await window.RxVaultOffline.markSynced(record.client_sync_id, data);
          if (data.status === "CONFLICT") {
            alert("Sync Conflict: A prescription already exists on the server for Appointment ID " + record.appointment_id + ". The local offline prescription was not overwritten.");
          }
        }
      } catch (err) {
        /* keep pending */
      }
    }
    if (window.RxVaultStatus) window.RxVaultStatus.setSyncing(false);
    if (window.RxVaultStatus) window.RxVaultStatus.render();
  }

  window.rxvaultSyncNow = syncPending;

  document.addEventListener("DOMContentLoaded", async () => {
    await cachePack();
    const cached = [];
    try {
      const rows = await window.RxVaultOffline.getAll("appointments");
      for (const row of rows || []) {
        const data = await window.RxVaultOffline.unwrapPayload(row, await window.RxVaultOffline.getWrapSecret());
        if (data) cached.push(data);
      }
    } catch (err) {
      /* ignore */
    }
    renderCachedAppointments(cached);
    document.querySelectorAll("form[action='/doctor/prescription']").forEach(bindPrescriptionForm);
    document.querySelectorAll("form[action='/doctor/search']").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        if (navigator.onLine) return;
        event.preventDefault();
        const input = form.querySelector('[name="appointment_id"]');
        const id = (input && input.value ? input.value : "").trim().toUpperCase();
        await openCached(id);
      });
    });
    document.querySelectorAll('a[href="/doctor/logout"]').forEach((link) => {
      link.addEventListener("click", async (event) => {
        event.preventDefault();
        try {
          await window.RxVaultOffline.clearAll();
        } catch (err) {
          /* ignore */
        }
        window.location.href = "/doctor/logout";
      });
    });
    if (navigator.onLine) await syncPending();
  });
})();
