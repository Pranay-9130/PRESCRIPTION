/**
 * RxVault IndexedDB store with optional AES-GCM wrapping.
 *
 * Browser storage is not a substitute for server authorization. Encryption
 * protects casual disk inspection; XSS or physical access to an unlocked
 * browser can still expose keys. Logout clears this database.
 */
(function (global) {
  const DB_NAME = "rxvault-offline";
  const DB_VERSION = 1;
  const STORES = ["appointments", "history", "pending", "prescriptions", "meta"];

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        STORES.forEach((name) => {
          if (!db.objectStoreNames.contains(name)) {
            db.createObjectStore(name, { keyPath: "id" });
          }
        });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function tx(storeName, mode, fn) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(storeName, mode);
      const store = transaction.objectStore(storeName);
      const result = fn(store);
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error);
    });
  }

  function reqToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function put(store, record) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(store, "readwrite");
      transaction.objectStore(store).put(record);
      transaction.oncomplete = () => resolve(record);
      transaction.onerror = () => reject(transaction.error);
    });
  }

  async function get(store, id) {
    const db = await openDb();
    return reqToPromise(db.transaction(store, "readonly").objectStore(store).get(id));
  }

  async function getAll(store) {
    const db = await openDb();
    return reqToPromise(db.transaction(store, "readonly").objectStore(store).getAll());
  }

  async function remove(store, id) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(store, "readwrite");
      transaction.objectStore(store).delete(id);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  }

  async function clearAll() {
    const db = await openDb();
    await Promise.all(
      STORES.map(
        (name) =>
          new Promise((resolve, reject) => {
            const transaction = db.transaction(name, "readwrite");
            transaction.objectStore(name).clear();
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => reject(transaction.error);
          })
      )
    );
    sessionStorage.removeItem("rxvault_wrap_key");
  }

  async function importKeyMaterial(secret) {
    const enc = new TextEncoder();
    const hash = await crypto.subtle.digest("SHA-256", enc.encode(String(secret || "rxvault-offline")));
    return crypto.subtle.importKey("raw", hash, "AES-GCM", false, ["encrypt", "decrypt"]);
  }

  async function wrapPayload(obj, secret) {
    try {
      const key = await importKeyMaterial(secret);
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const encoded = new TextEncoder().encode(JSON.stringify(obj));
      const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, encoded);
      return {
        enc: true,
        iv: Array.from(iv),
        data: arrayBufferToB64(cipher),
      };
    } catch (err) {
      return { enc: false, data: obj };
    }
  }

  async function unwrapPayload(record, secret) {
    if (!record) return null;
    if (!record.enc) return record.data || record;
    try {
      const key = await importKeyMaterial(secret);
      const iv = new Uint8Array(record.iv);
      const bytes = b64ToArrayBuffer(record.data);
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, bytes);
      return JSON.parse(new TextDecoder().decode(plain));
    } catch (err) {
      return null;
    }
  }

  function arrayBufferToB64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    bytes.forEach((b) => (binary += String.fromCharCode(b)));
    return btoa(binary);
  }

  function b64ToArrayBuffer(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  async function getWrapSecret() {
    let cached = sessionStorage.getItem("rxvault_wrap_key");
    if (cached) return cached;
    try {
      const res = await fetch("/api/me", { credentials: "same-origin" });
      if (res.ok) {
        const me = await res.json();
        if (me && me.offline_key_material) {
          sessionStorage.setItem("rxvault_wrap_key", me.offline_key_material);
          return me.offline_key_material;
        }
      }
    } catch (err) {
      /* offline */
    }
    return sessionStorage.getItem("rxvault_patient_wrap") || "rxvault-public-offline";
  }

  function setPatientWrap(appointmentId, mobile) {
    const material = "patient:" + String(appointmentId || "").toUpperCase() + ":" + String(mobile || "");
    sessionStorage.setItem("rxvault_patient_wrap", material);
    return material;
  }

  async function saveAppointment(appt, secret) {
    const wrapped = await wrapPayload(appt, secret || (await getWrapSecret()));
    return put("appointments", { id: appt.appointment_id, ...wrapped, cached_at: Date.now() });
  }

  async function loadAppointment(appointmentId, secret) {
    const row = await get("appointments", appointmentId);
    if (!row) return null;
    return unwrapPayload(row, secret || (await getWrapSecret()));
  }

  async function saveHistory(mobile, items, secret) {
    const wrapped = await wrapPayload(items, secret || (await getWrapSecret()));
    return put("history", { id: "hist:" + mobile, ...wrapped, cached_at: Date.now() });
  }

  async function loadHistory(mobile, secret) {
    const row = await get("history", "hist:" + mobile);
    if (!row) return null;
    return unwrapPayload(row, secret || (await getWrapSecret()));
  }

  async function queuePending(record) {
    record.id = record.client_sync_id;
    record.status = "PENDING_SYNC";
    record.updated_at = Date.now();
    return put("pending", record);
  }

  async function listPending() {
    const rows = await getAll("pending");
    return (rows || []).filter((r) => r.status !== "SYNCED");
  }

  async function markSynced(clientSyncId, serverInfo) {
    const row = await get("pending", clientSyncId);
    if (!row) return;
    row.status = "SYNCED";
    row.server = serverInfo || {};
    row.synced_at = Date.now();
    return put("pending", row);
  }

  async function savePrescription(pack, secret) {
    const wrapped = await wrapPayload(pack, secret || (await getWrapSecret()));
    return put("prescriptions", {
      id: pack.appointment_id,
      ...wrapped,
      cached_at: Date.now(),
      expiry_date: pack.expiry_date,
    });
  }

  async function loadPrescription(appointmentId, secret) {
    const row = await get("prescriptions", appointmentId);
    if (!row) return null;
    return unwrapPayload(row, secret || (await getWrapSecret()));
  }

  async function listPrescriptions(secret) {
    const rows = await getAll("prescriptions");
    const out = [];
    for (const row of rows || []) {
      const data = await unwrapPayload(row, secret || (await getWrapSecret()));
      if (data) out.push(data);
    }
    return out;
  }

  async function setMeta(key, value) {
    return put("meta", { id: key, value, updated_at: Date.now() });
  }

  async function getMeta(key) {
    const row = await get("meta", key);
    return row ? row.value : null;
  }

  async function cleanupStale(maxAgeMs) {
    const cutoff = Date.now() - (maxAgeMs || 14 * 24 * 60 * 60 * 1000);
    const appointments = await getAll("appointments");
    for (const row of appointments || []) {
      if ((row.cached_at || 0) < cutoff) await remove("appointments", row.id);
    }
    const prescriptions = await getAll("prescriptions");
    for (const row of prescriptions || []) {
      const expiry = row.expiry_date ? Date.parse(row.expiry_date) : 0;
      if (expiry && Date.now() > expiry + 7 * 24 * 60 * 60 * 1000) {
        await remove("prescriptions", row.id);
      }
    }
    const pending = await getAll("pending");
    for (const row of pending || []) {
      if (row.status === "SYNCED" && (row.synced_at || 0) < cutoff) {
        await remove("pending", row.id);
      }
    }
  }

  global.RxVaultOffline = {
    put,
    get,
    getAll,
    remove,
    clearAll,
    wrapPayload,
    unwrapPayload,
    getWrapSecret,
    setPatientWrap,
    saveAppointment,
    loadAppointment,
    saveHistory,
    loadHistory,
    queuePending,
    listPending,
    markSynced,
    savePrescription,
    loadPrescription,
    listPrescriptions,
    setMeta,
    getMeta,
    cleanupStale,
  };
})(window);
