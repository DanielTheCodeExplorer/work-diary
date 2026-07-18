const APP_TIME_ZONE = "Europe/London";

function today() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: APP_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

const state = {
  activeView: "dashboard",
  taskBucket: "inbox",
  tasks: [],
  entries: [],
  evidence: [],
  achievements: [],
  projects: [],
  googleIntegration: null,
  workHubTab: "projects",
  selectedProjectId: "",
  projectSuggestions: {},
  projectFormVisible: false,
  confirmingProjectId: "",
  projectSearch: "",
  projectPickerExpanded: false,
  collapsedProjectRecommendationIds: [],
  hiddenDashboardTaskIds: [],
  hiddenDashboardEntryIds: [],
  hiddenUpcomingTaskIds: [],
  hiddenEntryIds: [],
  showHiddenUpcoming: false,
  showHiddenEntries: false,
  taskComposerAdvanced: false,
  taskListExpanded: false,
  plannerFocus: "today",
  activeDateTarget: "",
  selectedDateValue: today(),
  activeTimeTarget: "",
  selectedTimeValue: "09:00",
  lockedScrollY: 0,
  options: {
    projects: [],
    skills: [],
    tags: [],
    evidence_types: [],
  },
  selectedTask: null,
  selectedEntry: null,
  selectedPhotoFile: null,
  pendingEvidence: [],
  calendarMonth: today().slice(0, 7),
  calendarSelectedDate: today(),
  settings: {
    density: "comfortable",
    accentColor: "#5DD4C0",
    showTaskDetails: true,
    defaultView: "dashboard",
    clockFormat: "24",
    reducedMotion: false,
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const on = (selector, event, handler, root = document) => {
  const element = $(selector, root);
  if (element) {
    element.addEventListener(event, handler);
  }
  return element;
};
const API_BASE_URL = window.API_BASE_URL || "";
const APP_ASSET_VERSION = "20260718-timezone-init-fix";
const API_REQUEST_TIMEOUT_MS = 10000;
const SETTINGS_KEY = "workDiarySettings";
const HIDDEN_DASHBOARD_TASKS_KEY = "workDiaryHiddenDashboardTasks";
const HIDDEN_DASHBOARD_ENTRIES_KEY = "workDiaryHiddenDashboardEntries";
const HIDDEN_UPCOMING_TASKS_KEY = "workDiaryHiddenUpcomingTasks";
const HIDDEN_ENTRIES_KEY = "workDiaryHiddenEntries";
const COLLAPSED_PROJECT_RECOMMENDATIONS_KEY = "workDiaryCollapsedProjectRecommendations";
const PLANNER_FOCUS_KEY = "workDiaryPlannerFocus";
const PHONE_REMINDERS_KEY = "workDiaryPhoneReminders";
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const wheelAnimationFrames = new WeakMap();
let pageScrollAnimationFrame = 0;

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  try {
    return await navigator.serviceWorker.register(`/service-worker.js?v=${APP_ASSET_VERSION}`);
  } catch {
    // Ignore failures; the app is still usable without offline caching or push.
  }
  return null;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char];
  });
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function splitList(value) {
  const seen = new Set();
  return String(value ?? "")
    .split(/[,;\n]/)
    .map((item) => compact(item))
    .filter((item) => {
      const key = item.toLowerCase();
      if (!item || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function formatDate(value) {
  if (!value) return "";
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function formatDateWithWeekday(value, options = {}) {
  if (!value) return "";
  const dateOptions = {
    weekday: options.weekday || "long",
    day: "2-digit",
    month: "short",
  };
  if (options.year !== false) {
    dateOptions.year = "numeric";
  }
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", dateOptions);
}

function formatWeekday(value) {
  if (!value) return "";
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
  });
}

function monthStart(monthValue) {
  return new Date(`${monthValue}-01T00:00:00`);
}

function dateInputValue(date) {
  const local = new Date(date);
  local.setMinutes(local.getMinutes() - local.getTimezoneOffset());
  return local.toISOString().slice(0, 10);
}

function monthInputValue(date) {
  return dateInputValue(date).slice(0, 7);
}

function shiftMonth(monthValue, offset) {
  const date = monthStart(monthValue);
  date.setMonth(date.getMonth() + offset);
  return monthInputValue(date);
}

function monthTitle(monthValue) {
  return monthStart(monthValue).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
}

function formatTime(value) {
  if (!value) return "";
  const [hour, minute] = value.split(":");
  if (!hour || !minute) return value;
  const hourNumber = Number(hour);
  return `${hourNumber}:${String(Number(minute)).padStart(2, "0")}`;
}

function londonTimeValue() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: APP_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const hour = parts.find((part) => part.type === "hour")?.value || "00";
  const minute = parts.find((part) => part.type === "minute")?.value || "00";
  return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

function dateDaysAgo(days) {
  const date = new Date(`${today()}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function truncate(value, maxLength = 280) {
  const text = compact(value);
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1).trim()}...`;
}

async function api(path, options = {}) {
  const request = {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
    },
  };
  const token = localStorage.getItem("workDiaryToken");
  if (token) {
    request.headers.Authorization = `Bearer ${token}`;
  }
  if (options.body) {
    request.body = JSON.stringify(options.body);
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || API_REQUEST_TIMEOUT_MS);
  request.signal = controller.signal;
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, request);
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("The request took too long. Check your connection and try again.", { cause: error });
    }
    throw new Error("The server could not be reached. Check your connection and try again.", { cause: error });
  } finally {
    window.clearTimeout(timeout);
  }
  const responseText = await response.text();
  let payload = {};
  if (responseText) {
    try {
      payload = JSON.parse(responseText);
    } catch {
      payload = {
        error: response.ok
          ? "The server returned an invalid response."
          : `Request failed: ${response.status}`,
      };
    }
  }
  if (response.status === 401) {
    localStorage.removeItem("workDiaryToken");
    window.location.assign("/login.html");
    throw new Error("Login required.");
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function setFormPending(form, pending) {
  if (!form) return false;
  if (pending && form.getAttribute("aria-busy") === "true") return false;
  form.setAttribute("aria-busy", pending ? "true" : "false");
  const submit = form.querySelector('[type="submit"]');
  if (submit) submit.disabled = pending;
  return true;
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => {
    toast.classList.add("hidden");
  }, 2600);
}

function pushSupported() {
  return Boolean(
    window.isSecureContext &&
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
  );
}

function setPushStatus(message) {
  const status = $("#pushStatus");
  if (status) status.textContent = message;
}

function setPushToggle(subscribed = false, disabled = false) {
  const toggle = $("#phoneReminderToggle");
  if (!toggle) return;
  toggle.checked = subscribed;
  toggle.disabled = disabled;
}

function setGoogleStatus(message) {
  const status = $("#googleIntegrationStatus");
  if (status) status.textContent = message;
}

function formatSyncDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderGoogleIntegration() {
  const status = state.googleIntegration || {};
  const connectButton = $("#connectGoogle");
  const disconnectButton = $("#disconnectGoogle");
  const retryButton = $("#retryGoogleSync");
  const failedCount = Number(status.failed_task_count || 0);
  const setupReady = Boolean(status.ready);

  if (!status.configured) {
    setGoogleStatus("Google sync is not configured on the server yet.");
    if (connectButton) connectButton.disabled = true;
    if (disconnectButton) disconnectButton.disabled = true;
    if (retryButton) retryButton.classList.add("hidden");
    return;
  }

  if (!status.connected) {
    setGoogleStatus("Google is not connected. Connect once; future task changes sync automatically.");
    if (connectButton) {
      connectButton.textContent = "Connect Google";
      connectButton.disabled = false;
      connectButton.classList.remove("hidden");
    }
    if (disconnectButton) disconnectButton.disabled = true;
    if (retryButton) retryButton.classList.add("hidden");
    return;
  }

  if (status.needs_reauthorization) {
    setGoogleStatus("Reconnect Google once to allow tasks to sync to My Tasks.");
    if (connectButton) {
      connectButton.textContent = "Reconnect Google";
      connectButton.disabled = false;
      connectButton.classList.remove("hidden");
    }
    if (disconnectButton) disconnectButton.disabled = false;
    if (retryButton) retryButton.classList.add("hidden");
    return;
  }

  if (!setupReady) {
    const error = status.last_error ? ` ${status.last_error}` : "";
    setGoogleStatus(`Google authorization succeeded, but setup is incomplete.${error}`);
    if (connectButton) connectButton.classList.add("hidden");
    if (disconnectButton) disconnectButton.disabled = false;
    if (retryButton) {
      retryButton.textContent = "Finish Google setup";
      retryButton.classList.remove("hidden");
    }
    return;
  }

  const lastSync = status.last_sync_at ? ` Last sync: ${formatSyncDateTime(status.last_sync_at)}.` : "";
  const error = status.last_error ? ` Error: ${status.last_error}` : "";
  const failed = failedCount ? ` ${failedCount} task${failedCount === 1 ? "" : "s"} need retry.` : "";
  setGoogleStatus(`Google is connected. All tasks go to My Tasks; Google displays dated tasks on its Tasks calendar.${lastSync}${failed}${error}`);
  if (connectButton) {
    connectButton.textContent = "Connect Google";
    connectButton.classList.add("hidden");
  }
  if (disconnectButton) disconnectButton.disabled = false;
  if (retryButton) {
    retryButton.textContent = "Retry failed sync";
    retryButton.classList.toggle("hidden", failedCount === 0);
  }
}

async function refreshGoogleIntegrationStatus() {
  try {
    state.googleIntegration = await api("/api/integrations/google/status");
  } catch (error) {
    state.googleIntegration = { configured: false, connected: false, last_error: error.message };
  }
  renderGoogleIntegration();
}

async function connectGoogleIntegration() {
  try {
    const returnUrl = `${window.location.origin}${window.location.pathname}`;
    const result = await api("/api/integrations/google/connect", {
      method: "POST",
      body: { return_url: returnUrl },
    });
    if (!result.auth_url) {
      showToast("Google connection did not return a sign-in URL.");
      return;
    }
    window.location.assign(result.auth_url);
  } catch (error) {
    showToast(error.message || "Google connection failed.");
    await refreshGoogleIntegrationStatus();
  }
}

async function disconnectGoogleIntegration() {
  if (!window.confirm("Disconnect Google sync? Existing Google Calendar and Tasks items will be left as they are.")) {
    return;
  }
  try {
    state.googleIntegration = await api("/api/integrations/google/disconnect", {
      method: "POST",
      body: {},
    });
    await loadData();
    renderGoogleIntegration();
    showToast("Google sync disconnected.");
  } catch (error) {
    showToast(error.message || "Could not disconnect Google.");
  }
}

async function retryGoogleIntegrationSync() {
  try {
    const result = await api("/api/integrations/google/retry", {
      method: "POST",
      body: {},
    });
    state.googleIntegration = result.status || state.googleIntegration;
    await loadData();
    renderGoogleIntegration();
    showToast(result.failed ? "Some Google sync retries failed." : "Google sync retried.");
  } catch (error) {
    showToast(error.message || "Google retry failed.");
    await refreshGoogleIntegrationStatus();
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = `${base64String}${padding}`.replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function byteArraysMatch(left, right) {
  if (!left || !right || left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return false;
  }
  return true;
}

function pushSubscriptionUsesKey(subscription, applicationServerKey) {
  const currentKey = subscription?.options?.applicationServerKey;
  if (!currentKey) return false;
  return byteArraysMatch(new Uint8Array(currentKey), applicationServerKey);
}

async function pushRegistration() {
  const registration = await registerServiceWorker();
  return registration || navigator.serviceWorker.ready;
}

async function syncPushSubscription(subscription) {
  await api("/api/push/subscribe", {
    method: "POST",
    body: {
      subscription: subscription.toJSON(),
      user_agent: navigator.userAgent,
    },
  });
}

async function syncReminderSchedules() {
  try {
    return await api("/api/reminders/sync", { method: "POST", body: {} });
  } catch {
    return null;
  }
}

async function ensurePushReminders({ prompt = false, toast = false } = {}) {
  if (!pushSupported()) {
    setPushStatus("Phone reminders need Android Chrome, HTTPS, and notification support.");
    setPushToggle(false, true);
    if (toast) showToast("Use Android Chrome over HTTPS for phone reminders.");
    return false;
  }

  let permission = Notification.permission;
  if (permission === "default" && prompt) {
    permission = await Notification.requestPermission();
  }

  if (permission === "denied") {
    setPushStatus("Notifications are blocked for this site in Chrome settings.");
    setPushToggle(false, false);
    return false;
  }

  if (permission !== "granted") {
    setPushStatus("Tick phone reminders and allow notifications once.");
    setPushToggle(false, false);
    return false;
  }

  const keyPayload = await api("/api/push/public-key");
  if (!keyPayload.publicKey) {
    setPushStatus("Reminder public key is missing on the server.");
    setPushToggle(false, true);
    if (toast) showToast("Reminder keys are not configured yet.");
    return false;
  }
  if (keyPayload.privateKeyConfigured === false || keyPayload.webpushInstalled === false) {
    setPushStatus("Reminder sending is not fully configured on the server.");
    setPushToggle(false, true);
    if (toast) showToast("Reminder sending is not fully configured.");
    return false;
  }

  const registration = await pushRegistration();
  const applicationServerKey = urlBase64ToUint8Array(keyPayload.publicKey);
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !pushSubscriptionUsesKey(subscription, applicationServerKey)) {
    try {
      await api("/api/push/unsubscribe", {
        method: "POST",
        body: {
          endpoint: subscription.endpoint,
        },
      });
    } catch {
      // The local browser subscription still needs replacing if the VAPID key changed.
    }
    await subscription.unsubscribe();
    subscription = null;
  }
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
  }

  await syncPushSubscription(subscription);
  await syncReminderSchedules();
  setPushStatus("Phone reminders are on.");
  setPushToggle(true, false);
  if (toast) showToast("Phone reminders are on.");
  return true;
}

async function refreshPushStatus() {
  if (!pushSupported()) {
    setPushStatus("Phone reminders need Android Chrome, HTTPS, and notification support.");
    setPushToggle(false, true);
    return;
  }
  if (Notification.permission === "denied") {
    setPushStatus("Notifications are blocked for this site in Chrome settings.");
    setPushToggle(false, false);
    return;
  }
  try {
    const registration = await pushRegistration();
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      try {
        await syncPushSubscription(subscription);
      } catch {
        setPushStatus("Reminders are allowed in Chrome, but this device could not sync with the server.");
        setPushToggle(false, false);
        return;
      }
      localStorage.setItem(PHONE_REMINDERS_KEY, "enabled");
      setPushStatus("Phone reminders are on.");
      setPushToggle(true, false);
      return;
    }
    if (
      Notification.permission === "granted" &&
      localStorage.getItem(PHONE_REMINDERS_KEY) === "enabled"
    ) {
      await ensurePushReminders();
      return;
    }
    setPushStatus("Tick phone reminders and allow notifications once.");
    setPushToggle(false, false);
  } catch {
    setPushStatus("Reminder status could not be checked.");
    setPushToggle(false, false);
  }
}

async function enablePushReminders() {
  const enabled = await ensurePushReminders({ prompt: true, toast: true });
  localStorage.setItem(PHONE_REMINDERS_KEY, enabled ? "enabled" : "disabled");
}

async function sendTestReminder() {
  const enabled = await ensurePushReminders({ prompt: true });
  if (!enabled) {
    showToast("Phone reminders are not ready.");
    return;
  }
  localStorage.setItem(PHONE_REMINDERS_KEY, "enabled");
  const result = await api("/api/push/test", { method: "POST", body: {} });
  const sent = Number(result.sent || 0);
  const failed = Number(result.failed || 0);
  const expired = Number(result.expired || 0);
  setPushStatus(`Test result: ${sent} sent, ${failed} failed, ${expired} expired.`);
  if (sent > 0) {
    showToast("Test reminder sent.");
  } else if (failed > 0) {
    showToast("Test reminder failed on the server.");
  } else {
    showToast("No phone subscription found.");
  }
}

async function disablePushReminders() {
  if (!pushSupported()) return;
  localStorage.setItem(PHONE_REMINDERS_KEY, "disabled");
  const registration = await pushRegistration();
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) {
    await api("/api/push/unsubscribe", {
      method: "POST",
      body: {
        endpoint: subscription.endpoint,
      },
    });
    await subscription.unsubscribe();
  }
  showToast("Phone reminders disabled.");
  setPushStatus("Phone reminders are off.");
  setPushToggle(false, false);
}

async function handlePhoneReminderToggle(event) {
  if (event.target.checked) {
    await enablePushReminders();
  } else {
    await disablePushReminders();
  }
}

function normalizeSavedView(view) {
  if (view === "diary" || view === "achievements") return "workhub";
  return ["dashboard", "planner", "workhub"].includes(view) ? view : "";
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    state.settings = {
      ...state.settings,
      ...saved,
      accentColor: saved.accentColor || state.settings.accentColor,
      showTaskDetails: saved.showTaskDetails !== false,
      defaultView: normalizeSavedView(saved.defaultView) || state.settings.defaultView,
      clockFormat: "24",
    };
  } catch {
    localStorage.removeItem(SETTINGS_KEY);
  }
}

function hexToRgb(hex) {
  const value = String(hex || "").replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(value)) return null;
  return {
    r: parseInt(value.slice(0, 2), 16),
    g: parseInt(value.slice(2, 4), 16),
    b: parseInt(value.slice(4, 6), 16),
  };
}

function shadeHex(hex, amount) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const channel = (value) => Math.max(0, Math.min(255, value + amount)).toString(16).padStart(2, "0");
  return `#${channel(rgb.r)}${channel(rgb.g)}${channel(rgb.b)}`;
}

function applySettings() {
  const settings = state.settings;
  const root = document.documentElement;
  const accent = /^#[0-9a-f]{6}$/i.test(settings.accentColor) ? settings.accentColor : "#5DD4C0";
  const rgb = hexToRgb(accent);
  const luminance = rgb ? (0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b) / 255 : 0;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-dark", shadeHex(accent, -22));
  root.style.setProperty("--accent-ink", luminance > 0.58 ? "#081211" : "#FFFFFF");
  if (rgb) {
    root.style.setProperty("--accent-soft", `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`);
  }
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) {
    themeMeta.setAttribute("content", accent);
  }
  document.body.dataset.density = settings.density === "compact" ? "compact" : "comfortable";
  document.body.dataset.clockFormat = "24";
  document.body.classList.toggle("hide-task-details", !settings.showTaskDetails);
  document.body.classList.toggle("reduce-motion", Boolean(settings.reducedMotion));
  updateTimeButtons();
}

function saveSettings(nextSettings) {
  state.settings = {
    ...state.settings,
    ...nextSettings,
  };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  applySettings();
  updateSettingsForm();
}

function updateSettingsForm() {
  const density = $("#settingDensity");
  const accentColor = $("#settingAccentColor");
  const defaultView = $("#settingDefaultView");
  const showTaskDetails = $("#settingShowTaskDetails");
  const reducedMotion = $("#settingReducedMotion");
  if (density) density.value = state.settings.density;
  if (accentColor) accentColor.value = state.settings.accentColor;
  if (defaultView) defaultView.value = state.settings.defaultView;
  if (showTaskDetails) showTaskDetails.checked = state.settings.showTaskDetails;
  if (reducedMotion) reducedMotion.checked = state.settings.reducedMotion;
}

function loadHiddenDashboardTasks() {
  try {
    const saved = JSON.parse(localStorage.getItem(HIDDEN_DASHBOARD_TASKS_KEY) || "[]");
    state.hiddenDashboardTaskIds = Array.isArray(saved) ? saved.map(String) : [];
  } catch {
    localStorage.removeItem(HIDDEN_DASHBOARD_TASKS_KEY);
    state.hiddenDashboardTaskIds = [];
  }
}

function saveHiddenDashboardTasks() {
  localStorage.setItem(HIDDEN_DASHBOARD_TASKS_KEY, JSON.stringify(state.hiddenDashboardTaskIds));
}

function hideDashboardTask(taskId) {
  const id = String(taskId);
  if (!state.hiddenDashboardTaskIds.includes(id)) {
    state.hiddenDashboardTaskIds.push(id);
    saveHiddenDashboardTasks();
  }
  renderDashboardRecent();
  showToast("Hidden from Dashboard.");
}

function restoreHiddenDashboardTasks() {
  state.hiddenDashboardTaskIds = [];
  state.hiddenDashboardEntryIds = [];
  saveHiddenDashboardTasks();
  saveLocalIdList(HIDDEN_DASHBOARD_ENTRIES_KEY, state.hiddenDashboardEntryIds);
  renderDashboardRecent();
  showToast("Dashboard progress restored.");
}

function loadLocalIdList(key) {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(saved) ? saved.map(String) : [];
  } catch {
    localStorage.removeItem(key);
    return [];
  }
}

function saveLocalIdList(key, ids) {
  localStorage.setItem(key, JSON.stringify(ids.map(String)));
}

function addHiddenId(key, stateKey, id) {
  const value = String(id || "");
  if (!value) return;
  if (!state[stateKey].includes(value)) {
    state[stateKey].push(value);
    saveLocalIdList(key, state[stateKey]);
  }
}

function removeHiddenId(key, stateKey, id) {
  const value = String(id || "");
  state[stateKey] = state[stateKey].filter((item) => item !== value);
  saveLocalIdList(key, state[stateKey]);
}

function hideDashboardEntry(entryId) {
  addHiddenId(HIDDEN_DASHBOARD_ENTRIES_KEY, "hiddenDashboardEntryIds", entryId);
  renderDashboardRecent();
  showToast("Hidden from Dashboard.");
}

function hideUpcomingTask(taskId) {
  addHiddenId(HIDDEN_UPCOMING_TASKS_KEY, "hiddenUpcomingTaskIds", taskId);
  renderTasks();
  renderTaskDetail();
  showToast("Hidden from Upcoming.");
}

function unhideUpcomingTask(taskId) {
  removeHiddenId(HIDDEN_UPCOMING_TASKS_KEY, "hiddenUpcomingTaskIds", taskId);
  renderTasks();
  renderTaskDetail();
  showToast("Task restored.");
}

function toggleHiddenUpcoming() {
  state.showHiddenUpcoming = !state.showHiddenUpcoming;
  renderTasks();
  renderTaskDetail();
}

function hideEntry(entryId) {
  addHiddenId(HIDDEN_ENTRIES_KEY, "hiddenEntryIds", entryId);
  renderEntries();
  renderDashboardRecent();
  showToast("Hidden from CV.");
}

function toggleHiddenEntries() {
  state.showHiddenEntries = !state.showHiddenEntries;
  renderEntries();
}

function loadProjectUiState() {
  state.collapsedProjectRecommendationIds = loadLocalIdList(COLLAPSED_PROJECT_RECOMMENDATIONS_KEY);
}

function saveProjectRecommendationState() {
  saveLocalIdList(COLLAPSED_PROJECT_RECOMMENDATIONS_KEY, state.collapsedProjectRecommendationIds);
}

function projectRecommendationsCollapsed(projectId) {
  return state.collapsedProjectRecommendationIds.map(String).includes(String(projectId));
}

function setProjectRecommendationsCollapsed(projectId, collapsed) {
  const id = String(projectId);
  state.collapsedProjectRecommendationIds = collapsed
    ? Array.from(new Set([...state.collapsedProjectRecommendationIds.map(String), id]))
    : state.collapsedProjectRecommendationIds.filter((value) => String(value) !== id);
  saveProjectRecommendationState();
  renderProjects();
}

function loadHiddenUiState() {
  loadHiddenDashboardTasks();
  state.hiddenDashboardEntryIds = loadLocalIdList(HIDDEN_DASHBOARD_ENTRIES_KEY);
  state.hiddenUpcomingTaskIds = loadLocalIdList(HIDDEN_UPCOMING_TASKS_KEY);
  state.hiddenEntryIds = loadLocalIdList(HIDDEN_ENTRIES_KEY);
}

async function loadData() {
  let bootstrap;
  try {
    bootstrap = await api("/api/bootstrap");
  } catch (error) {
    if (!String(error.message || "").toLowerCase().includes("route not found")) {
      throw error;
    }
    const [tasks, entries, evidence, options, achievements, projects] = await Promise.all([
      api("/api/tasks"),
      api("/api/entries"),
      api("/api/evidence"),
      api("/api/options"),
      api("/api/achievements").catch((achievementError) => {
        if (String(achievementError.message || "").toLowerCase().includes("route not found")) {
          return [];
        }
        throw achievementError;
      }),
      api("/api/projects").catch((projectError) => {
        if (String(projectError.message || "").toLowerCase().includes("route not found")) {
          return [];
        }
        throw projectError;
      }),
    ]);
    bootstrap = { tasks, entries, evidence, options, achievements, projects };
  }
  const tasks = bootstrap?.tasks;
  const entries = bootstrap?.entries;
  const evidence = bootstrap?.evidence;
  const achievements = bootstrap?.achievements;
  const projects = bootstrap?.projects;
  const options = bootstrap?.options;
  state.googleIntegration = bootstrap?.google_integration || state.googleIntegration || null;
  state.tasks = Array.isArray(tasks) ? tasks : [];
  state.entries = Array.isArray(entries) ? entries : Array.isArray(entries?.entries) ? entries.entries : [];
  state.evidence = Array.isArray(evidence) ? evidence : Array.isArray(evidence?.evidence) ? evidence.evidence : [];
  state.achievements = Array.isArray(achievements)
    ? achievements
    : Array.isArray(achievements?.achievements)
      ? achievements.achievements
      : [];
  state.projects = Array.isArray(projects)
    ? projects
    : Array.isArray(projects?.projects)
      ? projects.projects
      : [];
  if (state.selectedProjectId && !state.projects.some((project) => String(project.id) === String(state.selectedProjectId))) {
    state.selectedProjectId = "";
  }
  state.options = {
    projects: Array.isArray(options?.projects) ? options.projects : [],
    skills: Array.isArray(options?.skills) ? options.skills : [],
    tags: Array.isArray(options?.tags) ? options.tags : [],
    evidence_types: Array.isArray(options?.evidence_types) ? options.evidence_types : [],
  };
  populateControls();
  renderGoogleIntegration();
  render();
}

function optionList(items, currentValue, labeler = (value) => value) {
  return items
    .map((item) => {
      const value = typeof item === "string" ? item : item.value;
      const label = typeof item === "string" ? labeler(item) : item.label;
      const selected = value === currentValue ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function setSelectOptions(select, baseLabel, items, currentValue = "") {
  select.innerHTML = `<option value="">${escapeHtml(baseLabel)}</option>${optionList(items, currentValue)}`;
}

function projectNameById(projectId) {
  const project = state.projects.find((item) => String(item.id) === String(projectId));
  return project?.name || "";
}

function projectIdByName(projectName) {
  const name = compact(projectName).toLowerCase();
  const project = state.projects.find((item) => compact(item.name).toLowerCase() === name);
  return project ? String(project.id) : "";
}

function projectSelectOptions(currentValue = "") {
  return `<option value="">No project</option>${state.projects
    .map((project) => {
      const selected = String(project.id) === String(currentValue) ? " selected" : "";
      return `<option value="${escapeHtml(project.id)}"${selected}>${escapeHtml(project.name)}</option>`;
    })
    .join("")}`;
}

function setProjectSelect(select, currentValue = "") {
  if (!select) return;
  select.innerHTML = projectSelectOptions(currentValue);
}

function syncProjectHidden(selectId, hiddenId) {
  const select = $(`#${selectId}`);
  const hidden = $(`#${hiddenId}`);
  if (!select || !hidden) return;
  hidden.value = projectNameById(select.value);
}

function selectedProjectPayload(selectId) {
  const projectId = $(`#${selectId}`)?.value || "";
  return {
    project_id: projectId,
    project: projectNameById(projectId),
  };
}

function populateControls() {
  setSelectOptions($("#evidenceType"), "Website link", state.options.evidence_types, $("#evidenceType").value || "website");
  [
    "taskProjectId",
    "editTaskProjectId",
    "entryProjectId",
    "inlineQuickProjectId",
    "drawerPhotoProjectId",
  ].forEach((id) => {
    const select = $(`#${id}`);
    if (select) {
      const currentValue = select.value;
      setProjectSelect(select, currentValue);
    }
  });
}

function normalizeView(view) {
  if (view === "diary") {
    state.workHubTab = "cv";
    return "workhub";
  }
  if (view === "achievements") {
    state.workHubTab = "achievements";
    return "workhub";
  }
  return ["dashboard", "planner", "workhub", "taskDetail"].includes(view) ? view : "dashboard";
}

function switchView(view) {
  const nextView = normalizeView(view);
  state.activeView = nextView;
  const tabView = nextView === "taskDetail" ? "planner" : nextView;
  $$(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === tabView);
  });
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `${nextView}View`);
  });
  render();
}

function openWorkHub(tab = "projects") {
  state.workHubTab = tab;
  switchView("workhub");
}

function getDiaryFilters() {
  return {
    search: compact($("#searchInput").value).toLowerCase(),
  };
}

function entryMatches(entry, filters) {
  if (filters.search) {
    const text = [
      entry.title,
      entry.what_i_did,
      entry.project,
      entry.outcome,
      entry.reflection_notes,
      ...(entry.skills_used || []),
      ...(entry.tags || []),
    ]
      .join(" ")
      .toLowerCase();
    if (!text.includes(filters.search)) return false;
  }
  return true;
}

function chips(items) {
  if (!items || items.length === 0) return "";
  return `<div class="chip-row">${items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>`;
}

function taskFallsOnDate(task, dateValue) {
  const startDate = task.start_date || "";
  const dueDate = task.due_date || "";
  if (startDate && dueDate) return startDate <= dateValue && dateValue <= dueDate;
  if (dueDate) return dueDate === dateValue;
  if (startDate) return startDate <= dateValue;
  return false;
}

function taskIsActiveOn(task, dateValue) {
  if (task.completed) return false;
  const startDate = task.start_date || "";
  const dueDate = task.due_date || "";
  if (startDate && dueDate) return startDate <= dateValue;
  if (dueDate) return dueDate <= dateValue;
  if (startDate) return startDate <= dateValue;
  return false;
}

function taskIsLate(task) {
  if (task.completed || !task.due_date) return false;
  const dueDate = task.due_date || "";
  const currentDate = today();
  if (dueDate < currentDate) return true;
  if (dueDate > currentDate || !task.due_time) return false;
  return task.due_time < londonTimeValue();
}

function taskHasDate(task) {
  return Boolean(task.start_date || task.due_date);
}

function taskDateLabel(task, options = {}) {
  const startDate = task.start_date || "";
  const dueDate = task.due_date || "";
  const startTime = task.start_time && !options.short ? ` ${formatTime(task.start_time)}` : "";
  const dueTime = task.due_time && !options.short ? ` ${formatTime(task.due_time)}` : "";
  const displayDate = (value) => options.short
    ? formatDateWithWeekday(value, { year: false, weekday: "short" })
    : formatDateWithWeekday(value);
  if (startDate && dueDate && startDate !== dueDate) {
    return options.short
      ? `${displayDate(startDate)} - ${displayDate(dueDate)}${dueTime}`
      : `From ${displayDate(startDate)}${startTime} to ${displayDate(dueDate)}${dueTime}`;
  }
  if (startDate && dueDate) {
    const timeRange = startTime && dueTime ? `${startTime}–${formatTime(task.due_time)}` : startTime || dueTime;
    return `${options.short ? "" : "On "}${displayDate(startDate)}${timeRange}`.trim();
  }
  if (dueDate) {
    return `${options.short ? "" : "Date "}${displayDate(dueDate)}${dueTime}`.trim();
  }
  if (startDate) return `${options.short ? "" : "Starts "}${displayDate(startDate)}${startTime}`.trim();
  return "";
}

function taskScheduleValidationMessage(startDate, endDate, startTime, endTime) {
  if (startDate && endDate && startDate > endDate) {
    return "Start date must be on or before end date.";
  }
  const effectiveStartDate = startDate || endDate;
  const effectiveEndDate = endDate || startDate;
  if (startTime && endTime && effectiveStartDate === effectiveEndDate && startTime > endTime) {
    return "Start time must be on or before end time.";
  }
  return "";
}

function taskTimeLabel(task) {
  if (task.start_time && task.due_time) {
    return `${formatTime(task.start_time)}–${formatTime(task.due_time)}`;
  }
  if (task.start_time) return `Starts ${formatTime(task.start_time)}`;
  return task.due_time ? formatTime(task.due_time) : "";
}

function taskMeta(task) {
  const meta = [];
  if (task.project) {
    meta.push(task.project);
  }
  const dateLabel = taskDateLabel(task);
  if (dateLabel) {
    meta.push(dateLabel);
  }
  if (taskIsLate(task)) {
    meta.push("Late");
  }
  if (task.reminder_at) {
    meta.push(`Reminder ${formatDateTime(task.reminder_at)}`);
  }
  if (task.repeat_rule && task.repeat_rule !== "none") {
    const days = Number(task.repeat_interval_days || 1);
    const repeatLabel = task.repeat_rule === "interval"
      ? `Repeats every ${Number.isFinite(days) && days > 0 ? days : 1} day${days === 1 ? "" : "s"}`
      : `Repeats ${task.repeat_rule}`;
    meta.push(task.repeat_until ? `${repeatLabel} until ${formatDate(task.repeat_until)}` : repeatLabel);
  }
  if (task.location) {
    meta.push(task.location);
  }
  if (task.completed && task.completed_at) {
    meta.push("Done");
  }
  return meta;
}

function taskSortValue(task) {
  const date = task.start_date || task.due_date || "9999-12-31";
  const dueDate = task.due_date || "9999-12-31";
  const time = task.start_time || task.due_time || "23:59";
  return `${date}T${time}:${dueDate}:${String(task.id).padStart(8, "0")}`;
}

function sortTasks(tasks) {
  return [...tasks].sort((a, b) => taskSortValue(a).localeCompare(taskSortValue(b)));
}

function taskBuckets() {
  const tasks = Array.isArray(state.tasks) ? state.tasks : [];
  const openTasks = tasks.filter((task) => !task.completed);
  const completedTasks = tasks.filter((task) => task.completed);
  const inboxTasks = openTasks.filter((task) => !taskHasDate(task));
  const todayTasks = openTasks.filter((task) => taskIsActiveOn(task, today()));
  const upcomingTasks = openTasks.filter((task) => taskHasDate(task) && !taskIsActiveOn(task, today()));
  return {
    incomplete: sortTasks(openTasks),
    complete: sortCompletedTasks(completedTasks),
    openTasks,
    completedTasks: sortCompletedTasks(completedTasks),
    inbox: sortTasks(inboxTasks),
    today: sortTasks(todayTasks),
    upcoming: sortTasks(upcomingTasks),
  };
}

function completedDate(task) {
  return task.completed_at ? String(task.completed_at).slice(0, 10) : "";
}

function taskProgressDate(task) {
  return String(task.updated_at || task.created_at || today()).slice(0, 10);
}

function completedSortValue(task) {
  return task.completed_at || task.updated_at || task.created_at || "";
}

function sortCompletedTasks(tasks) {
  return [...tasks].sort((a, b) => completedSortValue(b).localeCompare(completedSortValue(a)));
}

function noteExplainsIncompleteOutcome(note) {
  const text = compact(note).toLowerCase();
  if (!text) return false;
  return [
    /\b(?:didn't|did not|couldn't|could not|wasn't able to|not able to|unable to)\s+(?:fully\s+)?(?:complete|finish|do)\b/,
    /\b(?:not|never)\s+(?:fully\s+)?(?:completed|complete|finished|finish|done)\b/,
    /\b(?:blocked|stuck|paused|deferred|postponed|delayed)\b/,
    /\b(?:couldn't|could not|didn't|did not)\s+(?:because|due to|as)\b/,
  ].some((pattern) => pattern.test(text));
}

function completedTaskAchievement(task) {
  const note = compact(task.notes);
  const incompleteOutcome = noteExplainsIncompleteOutcome(note);
  const prefix = incompleteOutcome ? "Closed task without completion" : "Completed task";
  const chip = incompleteOutcome ? "Closed with blocker" : "Completed task";
  return {
    kind: "task",
    id: `task-${task.id}`,
    taskId: task.id,
    date: completedDate(task) || today(),
    title: note
      ? `${prefix}: ${task.title} - ${truncate(note, 150)}`
      : `${prefix}: ${task.title}`,
    meta: [task.project, incompleteOutcome ? "Closed" : "Completed"].filter(Boolean).join(" / "),
    chips: [chip],
    searchText: [task.title, task.project, task.notes, incompleteOutcome ? "closed blocker incomplete task" : "completed task"].join(" "),
  };
}

function taskNoteProgressAchievement(task) {
  const note = compact(task.notes);
  if (!note) return null;
  return {
    kind: "task",
    id: `task-progress-${task.id}`,
    taskId: task.id,
    date: taskProgressDate(task),
    title: `Progress on ${task.title}: ${truncate(note, 170)}`,
    meta: [task.project, taskDateLabel(task, { short: true }) || "Open task"].filter(Boolean).join(" / "),
    chips: ["Task note"],
    searchText: [task.title, task.project, task.notes, "task progress", "task note"].join(" "),
  };
}

function generatedAchievement(item) {
  return {
    kind: "achievement",
    id: `achievement-${item.id}`,
    entryId: item.source_entry_id,
    date: item.achieved_at,
    title: item.bullet,
    meta: [item.project, ...(item.skills_used || [])].filter(Boolean).join(" / "),
    chips: [item.project, ...(item.skills_used || [])].filter(Boolean),
    searchText: [item.bullet, item.project, item.source, ...(item.skills_used || []), ...(item.tags || [])].join(" "),
  };
}

function progressRows() {
  const achievements = (Array.isArray(state.achievements) ? state.achievements : []).map(generatedAchievement);
  const taskProgress = (Array.isArray(state.tasks) ? state.tasks : [])
    .map((task) => task.completed ? completedTaskAchievement(task) : taskNoteProgressAchievement(task))
    .filter(Boolean);
  return [...achievements, ...taskProgress].sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

function achievementSearchTerms(search) {
  return compact(search).toLowerCase().split(/\s+/).filter(Boolean);
}

function achievementMatches(item, search) {
  const terms = achievementSearchTerms(search);
  if (!terms.length) return true;
  const text = String(item.searchText || "").toLowerCase();
  return terms.every((term) => text.includes(term));
}

function achievementAction(item) {
  return item.kind === "task" ? "edit-task" : "edit-entry";
}

function achievementDataAttributes(item) {
  return item.kind === "task"
    ? `data-task-id="${escapeHtml(item.taskId)}"`
    : `data-entry-id="${escapeHtml(item.entryId || "")}"`;
}

function renderAchievementMemoryItem(item) {
  return `
    <article class="progress-item" ${achievementDataAttributes(item)}>
      <span class="date-pill">${escapeHtml(formatDate(item.date))}</span>
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        ${item.meta ? `<div class="entry-meta">${escapeHtml(item.meta)}</div>` : ""}
      </div>
      <div class="progress-actions">
        <button class="link-button" type="button" data-action="${achievementAction(item)}">${item.kind === "task" ? "Edit task" : "Edit source"}</button>
      </div>
    </article>
  `;
}

function findProject(projectId) {
  return state.projects.find((project) => String(project.id) === String(projectId));
}

function projectStatusLabel(status) {
  return {
    planned: "Planned",
    active: "Active",
    paused: "Paused",
    complete: "Complete",
  }[status] || "Active";
}

function itemMatchesProjectRecord(item, project) {
  if (!project) return false;
  return String(item.project_id || "") === String(project.id) || compact(item.project).toLowerCase() === compact(project.name).toLowerCase();
}

function linkedProjectTasks(project) {
  return (Array.isArray(state.tasks) ? state.tasks : []).filter((task) => itemMatchesProjectRecord(task, project));
}

function linkedProjectEntries(project) {
  return (Array.isArray(state.entries) ? state.entries : []).filter((entry) => itemMatchesProjectRecord(entry, project));
}

function linkedProjectAchievements(project) {
  const name = compact(project?.name).toLowerCase();
  return progressRows().filter((item) => compact(item.meta).toLowerCase().includes(name) || compact(item.searchText).toLowerCase().includes(name));
}

function renderProjectMiniList(items, emptyText, renderer) {
  if (!items.length) {
    return `<div class="empty-state compact-empty">${escapeHtml(emptyText)}</div>`;
  }
  return `<div class="project-mini-list">${items.map((item, index) => renderer(item, index, items)).join("")}</div>`;
}

function projectTaskSortKey(task) {
  const order = Number(task.project_order || 0);
  return [
    order > 0 ? 0 : 1,
    order > 0 ? order : 999999,
    task.start_date || task.due_date || "9999-12-31",
    task.due_date || "9999-12-31",
    task.start_time || task.due_time || "23:59",
    task.created_at || "",
  ];
}

function sortProjectTasks(tasks) {
  return [...tasks].sort((a, b) => {
    const left = projectTaskSortKey(a);
    const right = projectTaskSortKey(b);
    for (let index = 0; index < left.length; index += 1) {
      if (left[index] < right[index]) return -1;
      if (left[index] > right[index]) return 1;
    }
    return 0;
  });
}

function renderProjectTaskRow(task, index = 0, items = []) {
  const canMove = !task.completed && items.length > 1;
  const late = taskIsLate(task);
  return `
    <article class="project-mini-item ${late ? "late" : ""}" data-task-id="${escapeHtml(task.id)}">
      <div class="project-mini-title">${escapeHtml(task.title)}</div>
      <div class="project-mini-meta">
        ${task.completed ? `<span>Done ${escapeHtml(formatDate(completedDate(task) || task.updated_at?.slice(0, 10) || ""))}</span>` : `<span>${escapeHtml(taskDateLabel(task, { short: true }) || "Inbox")}</span>`}
        ${late ? `<span class="late-text">Late</span>` : ""}
      </div>
      <div class="project-task-actions">
        ${canMove ? `
          <button class="icon-button compact-icon" type="button" data-action="move-project-task" data-direction="up" aria-label="Move task up" title="Move up" ${index === 0 ? "disabled" : ""}>
            <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"></path></svg>
          </button>
          <button class="icon-button compact-icon" type="button" data-action="move-project-task" data-direction="down" aria-label="Move task down" title="Move down" ${index === items.length - 1 ? "disabled" : ""}>
            <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M12 5v14M19 12l-7 7-7-7"></path></svg>
          </button>
        ` : ""}
        <button class="link-button" type="button" data-action="edit-task">Edit task</button>
      </div>
    </article>
  `;
}

function renderProjectEntryRow(entry) {
  return `
    <article class="project-mini-item" data-entry-id="${escapeHtml(entry.id)}">
      <div class="project-mini-title">${escapeHtml(entry.title)}</div>
      <div class="project-mini-meta">
        <span>${escapeHtml(formatDate(entry.entry_date))}</span>
        <span>${escapeHtml(entry.evidence_count || 0)} evidence</span>
      </div>
      <button class="link-button" type="button" data-action="edit-entry">Edit CV note</button>
    </article>
  `;
}

function renderProjectAchievementRow(item) {
  return `
    <article class="project-mini-item" ${item.taskId ? `data-task-id="${escapeHtml(item.taskId)}"` : `data-entry-id="${escapeHtml(item.entryId || "")}"`}>
      <div class="project-mini-title">${escapeHtml(item.title)}</div>
      <div class="project-mini-meta">
        <span>${escapeHtml(formatDate(item.date))}</span>
      </div>
    </article>
  `;
}

function renderProjectSuggestions(projectId) {
  const suggestions = state.projectSuggestions[String(projectId)] || [];
  if (!suggestions.length) {
    return "";
  }
  const collapsed = projectRecommendationsCollapsed(projectId);
  return `
    <section class="project-suggestions ${collapsed ? "collapsed" : ""}" aria-label="Suggested next steps">
      <div class="todo-head">
        <div>
          <p class="eyebrow">AI assist</p>
          <h3>Suggested next steps</h3>
        </div>
        <button class="ghost-button compact-button" type="button" data-action="${collapsed ? "show-project-suggestions" : "hide-project-suggestions"}">
          ${collapsed ? "Show recommendations" : "Hide recommendations"}
        </button>
      </div>
      ${collapsed ? "" : `<div class="suggestion-list">
        ${suggestions
          .map((suggestion, index) => {
            return `
              <article class="suggestion-card">
                <div>
                  <strong>${escapeHtml(suggestion.title)}</strong>
                  <p>${escapeHtml(suggestion.guidance || suggestion.notes || "")}</p>
                </div>
                <button class="secondary-button" type="button" data-action="add-suggestion-task" data-index="${index}">Add to Planner</button>
              </article>
            `;
          })
          .join("")}
      </div>`}
    </section>
  `;
}

function filteredProjects(projects) {
  const search = compact(state.projectSearch).toLowerCase();
  if (!search) return projects;
  return projects.filter((project) => compact(project.name).toLowerCase().includes(search));
}

function renderProjectList(projects) {
  const list = $("#projectList");
  if (!list) return;
  const visibleProjects = filteredProjects(projects);
  const selectedProject = findProject(state.selectedProjectId);
  if (selectedProject && !state.projectPickerExpanded) {
    list.innerHTML = `
      <div class="selected-project-strip is-opening" data-project-id="${escapeHtml(selectedProject.id)}">
        <div>
          <span class="eyebrow">Selected project</span>
          <strong>${escapeHtml(selectedProject.name)}</strong>
        </div>
        <button class="project-strip-toggle" type="button" data-action="deselect-project" aria-label="Deselect project" title="Deselect project">
          <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
            <path d="M18 15l-6-6-6 6"></path>
          </svg>
        </button>
      </div>
    `;
    return;
  }
  if (!projects.length) {
    list.innerHTML = `<div class="empty-state">No projects yet.</div>`;
    return;
  }
  if (!visibleProjects.length) {
    list.innerHTML = `<div class="empty-state">No matching projects.</div>`;
    return;
  }
  list.innerHTML = visibleProjects
    .map((project) => {
      const active = String(project.id) === String(state.selectedProjectId);
      return `
        <button class="project-row ${active ? "active" : ""}" type="button" data-action="select-project" data-project-id="${escapeHtml(project.id)}">
          <span>${escapeHtml(project.name)}</span>
          <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
            <path d="M9 18l6-6-6-6"></path>
          </svg>
        </button>
      `;
    })
    .join("");
}

function renderProjectTaskSection(title, tasks, emptyText, open = true) {
  return `
    <details class="project-task-section" ${open ? "open" : ""}>
      <summary>
        <span>${escapeHtml(title)}</span>
        <span class="todo-count">${tasks.length} ${tasks.length === 1 ? "item" : "items"}</span>
      </summary>
      ${renderProjectMiniList(tasks, emptyText, renderProjectTaskRow)}
    </details>
  `;
}

function renderProjectCompleteSheet(project, openTasks, completedTasks) {
  if (String(state.confirmingProjectId) !== String(project.id)) return "";
  return `
    <section class="project-complete-sheet" aria-label="Confirm project completion">
      <div>
        <p class="eyebrow">Confirm completion</p>
        <h3>Complete ${escapeHtml(project.name)}?</h3>
        <p>This saves the project as an achievement. It will not complete or delete linked Planner tasks.</p>
      </div>
      ${project.goal ? `<p class="project-text"><strong>Goal:</strong> ${escapeHtml(project.goal)}</p>` : ""}
      <div class="project-complete-summary">
        <span>${countMarkup(openTasks.length, "open next steps")}</span>
        <span>${countMarkup(completedTasks.length, "completed")}</span>
      </div>
      ${openTasks.length ? `
        <details class="project-task-section project-complete-tasks">
          <summary>
            <span>View open tasks</span>
            <span class="todo-count">${openTasks.length} ${openTasks.length === 1 ? "item" : "items"}</span>
          </summary>
          ${renderProjectMiniList(openTasks, "No open next steps.", (task) => renderProjectTaskRow(task, 0, []))}
        </details>
      ` : ""}
      <div class="section-actions">
        <button class="ghost-button" type="button" data-action="cancel-complete-project">Cancel</button>
        <button class="primary-button" type="button" data-action="complete-project-now">Complete project</button>
      </div>
    </section>
  `;
}

function renderProjects() {
  const projects = Array.isArray(state.projects) ? state.projects : [];
  const detail = $("#projectDetail");
  const form = $("#projectForm");
  renderProjectList(projects);
  if (form) {
    form.classList.toggle("hidden", !state.projectFormVisible);
    const title = $("#projectFormTitle");
    if (title) {
      title.textContent = $("#projectId")?.value ? "Edit project" : "New project";
    }
  }
  if (!detail) return;
  if (!projects.length) {
    detail.innerHTML = `<div class="empty-state">Create a project, then add next steps into Planner.</div>`;
    return;
  }

  const project = findProject(state.selectedProjectId);
  if (!project) {
    detail.innerHTML = `<div class="empty-state">Search or tap a project to manage next steps.</div>`;
    return;
  }
  const tasks = linkedProjectTasks(project);
  const openTasks = sortProjectTasks(tasks.filter((task) => !task.completed));
  const completedTasks = sortCompletedTasks(tasks.filter((task) => task.completed));
  const isComplete = project.status === "complete";
  const suggestions = state.projectSuggestions[String(project.id)] || [];
  detail.innerHTML = `
    <section class="project-detail-card is-opening" data-project-id="${escapeHtml(project.id)}">
      <div class="project-detail-head">
        <div>
          <p class="eyebrow">${escapeHtml(projectStatusLabel(project.status))}</p>
          <h2>${escapeHtml(project.name)}</h2>
          ${project.deadline ? `<div class="entry-meta">Deadline ${escapeHtml(formatDateWithWeekday(project.deadline))}</div>` : ""}
          ${isComplete && project.completed_at ? `<div class="entry-meta">Completed ${escapeHtml(formatDate(project.completed_at.slice(0, 10)))}</div>` : ""}
        </div>
        <div class="section-actions project-actions">
          ${isComplete ? "" : `<button class="ghost-button project-action-button" type="button" data-action="start-complete-project">Complete</button>`}
          <button class="ghost-button project-action-button" type="button" data-action="edit-project">Edit</button>
          <button class="icon-button project-refresh-button" type="button" data-action="suggest-project" aria-label="${suggestions.length ? "Refresh recommendations" : "Get recommendations"}" title="${suggestions.length ? "Refresh recommendations" : "Get recommendations"}">
            <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
              <path d="M21 12a9 9 0 0 1-15.4 6.4"></path>
              <path d="M3 12a9 9 0 0 1 15.4-6.4"></path>
              <path d="M16 5h3V2"></path>
              <path d="M8 19H5v3"></path>
            </svg>
          </button>
        </div>
      </div>
      ${project.goal ? `<p class="project-text">${escapeHtml(project.goal)}</p>` : `<p class="project-text muted">Add a goal so suggestions know what this project is trying to achieve.</p>`}
      ${project.notes ? `<p class="project-text">${escapeHtml(project.notes)}</p>` : ""}
      <form class="project-next-step-form" data-project-id="${escapeHtml(project.id)}">
        <label>
          Next step
          <input id="projectNextStepTitle" type="text" placeholder="Add a short Planner task" autocomplete="off">
        </label>
        <button class="primary-button" type="submit">Add to Planner</button>
      </form>
      ${renderProjectSuggestions(project.id)}
      ${renderProjectCompleteSheet(project, openTasks, completedTasks)}
      <div class="project-sections">
        ${renderProjectTaskSection("Next steps", openTasks, "No open next steps yet.", true)}
        ${renderProjectTaskSection("Completed", completedTasks, "No completed project tasks yet.", false)}
      </div>
    </section>
  `;
}

function renderWorkHub() {
  $$(".workhub-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.workhubTab === state.workHubTab);
  });
  $$(".workhub-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.workhubPanel === state.workHubTab);
  });
  renderProjects();
}

function taskBucketTitle(bucket) {
  if (bucket === "incomplete") return "Incomplete";
  if (bucket === "complete") return "Complete";
  if (bucket === "today") return "Today";
  if (bucket === "upcoming") return "Upcoming";
  return "Inbox";
}

function taskBucketCountLabel(bucket, count) {
  if (bucket === "incomplete") return `${count} open`;
  if (bucket === "complete") return `${count} done`;
  if (bucket === "today") return `${count} today`;
  if (bucket === "upcoming") return `${count} items`;
  return `${count} undated`;
}

function taskBucketForTask(task) {
  if (task.completed) return "complete";
  if (taskIsActiveOn(task, today())) return "today";
  if (taskHasDate(task)) return "upcoming";
  return "inbox";
}

function taskBucketLabel(task) {
  return taskBucketTitle(taskBucketForTask(task));
}

function googleSyncChip(task) {
  if (!state.googleIntegration?.connected) return "";
  if (task.google_sync_error) {
    return `<span class="task-chip late" title="${escapeHtml(task.google_sync_error)}">Sync failed</span>`;
  }
  if (task.google_sync_target === "calendar_event" && task.google_synced_at) {
    return `<span class="task-chip">Google Calendar</span>`;
  }
  if (task.google_sync_target === "calendar_and_task" && task.google_synced_at) {
    return `<span class="task-chip">Calendar + Tasks</span>`;
  }
  if (task.google_sync_target === "google_task" && task.google_synced_at) {
    return `<span class="task-chip">Google Tasks</span>`;
  }
  return "";
}

function renderTaskCard(task, options = {}) {
  const expanded = Boolean(options.expanded);
  const bucket = taskBucketForTask(task);
  const late = taskIsLate(task);
  const isHiddenUpcoming = bucket === "upcoming" && state.hiddenUpcomingTaskIds.includes(String(task.id));
  const hideUpcomingButton = options.allowHideUpcoming && bucket === "upcoming"
    ? `<button class="link-button subtle" type="button" data-action="${isHiddenUpcoming ? "unhide-upcoming-task" : "hide-upcoming-task"}">${isHiddenUpcoming ? "Unhide" : "Hide"}</button>`
    : "";
  const meta = taskMeta(task)
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join("");
  return `
    <article class="task-item ${task.completed ? "completed" : ""} ${late ? "late" : ""}" data-task-id="${task.id}">
      <button class="task-checkbox ${task.completed ? "checked" : ""}" type="button" data-action="toggle-task" aria-label="${task.completed ? "Mark task open" : "Mark task done"}">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M20 6 9 17l-5-5"></path>
        </svg>
      </button>
      <div class="task-main">
        <div class="task-title-row">
          <div class="task-title">${escapeHtml(task.title)}</div>
          ${late ? `<span class="task-chip late">Late</span>` : ""}
          ${googleSyncChip(task)}
        </div>
        ${meta ? `<div class="task-meta">${meta}</div>` : ""}
        ${task.notes ? `<p class="task-notes">${escapeHtml(expanded ? task.notes : truncate(task.notes, 120))}</p>` : ""}
      </div>
      <div class="task-actions">
        ${hideUpcomingButton}
        <button class="link-button" type="button" data-action="edit-task">Edit</button>
        <button class="ghost-button danger" type="button" data-action="delete-task">Delete</button>
      </div>
    </article>
  `;
}

function renderTaskAgendaItem(task, options = {}) {
  const bucket = taskBucketForTask(task);
  const late = taskIsLate(task);
  const isHiddenUpcoming = bucket === "upcoming" && state.hiddenUpcomingTaskIds.includes(String(task.id));
  const hideUpcomingButton = options.allowHideUpcoming && bucket === "upcoming"
    ? `<button class="link-button subtle" type="button" data-action="${isHiddenUpcoming ? "unhide-upcoming-task" : "hide-upcoming-task"}">${isHiddenUpcoming ? "Unhide" : "Hide"}</button>`
    : "";
  const repeatChip = task.repeat_rule && task.repeat_rule !== "none"
    ? `<span class="task-chip repeat">Repeats</span>`
    : "";
  return `
    <article class="task-agenda-item ${task.completed ? "completed" : ""} ${late ? "late" : ""}" data-task-id="${escapeHtml(task.id)}">
      <button class="task-checkbox ${task.completed ? "checked" : ""}" type="button" data-action="toggle-task" aria-label="${task.completed ? "Mark task open" : "Mark task done"}">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M20 6 9 17l-5-5"></path>
        </svg>
      </button>
      <div class="task-main">
        <div class="task-title-row">
          <div class="task-title">${escapeHtml(task.title)}</div>
        </div>
        <div class="task-chip-row">
          <span class="task-chip ${escapeHtml(bucket)}">${escapeHtml(taskBucketLabel(task))}</span>
          ${late ? `<span class="task-chip late">Late</span>` : ""}
          ${taskDateLabel(task, { short: true }) ? `<span class="task-chip">${escapeHtml(taskDateLabel(task, { short: true }))}</span>` : ""}
          ${taskTimeLabel(task) ? `<span class="task-chip time">${escapeHtml(taskTimeLabel(task))}</span>` : ""}
          ${repeatChip}
          ${googleSyncChip(task)}
        </div>
      </div>
      <div class="task-actions">
        ${hideUpcomingButton}
        <button class="link-button" type="button" data-action="edit-task">Edit</button>
        <button class="ghost-button danger" type="button" data-action="delete-task">Delete</button>
      </div>
    </article>
  `;
}

function visibleAgendaTasks(tasks) {
  const hiddenUpcomingIds = new Set(state.hiddenUpcomingTaskIds.map(String));
  return sortTasks(tasks).filter((task) => {
    return state.showHiddenUpcoming || taskBucketForTask(task) !== "upcoming" || !hiddenUpcomingIds.has(String(task.id));
  });
}

function setPlannerFocus(focus) {
  if (!["today", "next", "all"].includes(focus)) return;
  state.plannerFocus = focus;
  state.taskListExpanded = false;
  localStorage.setItem(PLANNER_FOCUS_KEY, focus);
  renderTasks();
}

function loadPlannerFocus() {
  const saved = localStorage.getItem(PLANNER_FOCUS_KEY);
  state.plannerFocus = ["today", "next", "all"].includes(saved) ? saved : "today";
}

function countMarkup(value, label) {
  return `<span class="count-emphasis">${escapeHtml(value)}</span> <span>${escapeHtml(label)}</span>`;
}

function renderPlannerAgenda(buckets) {
  const hiddenUpcomingIds = new Set(state.hiddenUpcomingTaskIds.map(String));
  const hiddenCount = buckets.upcoming.filter((task) => hiddenUpcomingIds.has(String(task.id))).length;
  const visibleTasks = visibleAgendaTasks(buckets.openTasks);
  const attentionTasks = visibleAgendaTasks(buckets.today);
  const focus = state.plannerFocus;
  const focusedTasks = focus === "today"
    ? attentionTasks
    : focus === "next"
      ? (attentionTasks.length ? attentionTasks : visibleTasks).slice(0, 1)
      : visibleTasks;
  const initialLimit = plannerInitialTaskLimit();
  const shownTasks = focus === "all" && !state.taskListExpanded
    ? focusedTasks.slice(0, initialLimit)
    : focusedTasks;
  const hiddenButton = focus === "all" && hiddenCount > 0
    ? `<button class="planner-box-link" type="button" data-action="toggle-hidden-upcoming">${state.showHiddenUpcoming ? "Hide hidden" : "Show hidden"}</button>`
    : "";
  const showMoreButton = focus === "all" && visibleTasks.length > shownTasks.length
    ? `<button class="ghost-button full-width" type="button" data-action="toggle-task-list-expanded">Show ${visibleTasks.length - shownTasks.length} more</button>`
    : focus === "all" && state.taskListExpanded && visibleTasks.length > initialLimit
      ? `<button class="ghost-button full-width" type="button" data-action="toggle-task-list-expanded">Show less</button>`
      : "";
  const heading = focus === "today" ? "Today" : focus === "next" ? "Do this next" : "All open tasks";
  const emptyText = focus === "today" ? "Nothing needs your attention today." : "No open tasks here.";
  return `
    <section class="planner-agenda" aria-label="Open tasks">
      <header class="planner-agenda-head">
        <div>
          <p class="eyebrow">Open tasks</p>
          <h3>${escapeHtml(heading)}</h3>
        </div>
        <div class="planner-counts" aria-label="Task counts">
          <span>${countMarkup(buckets.today.length, "today")}</span>
          <span>${countMarkup(buckets.inbox.length, "inbox")}</span>
          <span>${countMarkup(visibleAgendaTasks(buckets.upcoming).length, "items")}</span>
          ${hiddenButton}
        </div>
      </header>
      <div class="planner-focus" role="group" aria-label="Choose planner focus">
        ${["today", "next", "all"].map((value) => `
          <button class="${focus === value ? "active" : ""}" type="button" data-action="set-planner-focus" data-focus="${value}" aria-pressed="${focus === value}">
            ${value === "today" ? "Today" : value === "next" ? "Next" : "All"}
          </button>
        `).join("")}
      </div>
      <div class="planner-agenda-list">
        ${
          shownTasks.length
            ? shownTasks.map((task) => renderTaskAgendaItem(task, { allowHideUpcoming: true })).join("")
            : `<div class="planner-empty">${escapeHtml(emptyText)}</div>`
        }
        ${showMoreButton}
      </div>
    </section>
  `;
}

function plannerInitialTaskLimit() {
  return window.matchMedia?.("(min-width: 900px)").matches ? 5 : 2;
}

function renderTasks() {
  const buckets = taskBuckets();
  const list = $("#taskList");

  $("#todoCount").innerHTML = countMarkup(buckets.openTasks.length, "open");
  list.innerHTML = renderPlannerAgenda(buckets);
}

function renderOverview() {
  const buckets = taskBuckets();
  const cards = [
    {
      action: "overview-open",
      label: "Incomplete",
      value: buckets.openTasks.length,
      detail: buckets.openTasks.length === 1 ? "task not done" : "tasks not done",
    },
    {
      action: "overview-completed",
      label: "Complete",
      value: buckets.completedTasks.length,
      detail: buckets.completedTasks.length === 1 ? "task done" : "tasks done",
    },
  ];

  $("#overviewCards").innerHTML = cards
    .map((card) => {
      return `
        <button class="overview-card ${card.wide ? "wide" : ""}" type="button" data-action="${escapeHtml(card.action)}" ${card.bucket ? `data-bucket="${escapeHtml(card.bucket)}"` : ""}>
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <small>${escapeHtml(card.detail)}</small>
        </button>
      `;
    })
    .join("");
  renderDashboardRecent();
  renderDashboardAchievementSearch();
}

function renderDashboardAchievementSearch() {
  const input = $("#dashboardAchievementSearchInput");
  const results = $("#dashboardAchievementSearchResults");
  if (!input || !results) return;
  const search = compact(input.value);
  if (!search) {
    results.innerHTML = "";
    return;
  }

  const matches = progressRows().filter((item) => achievementMatches(item, search));
  if (!matches.length) {
    results.innerHTML = `<div class="empty-state compact-empty">No matching achievements found.</div>`;
    return;
  }

  const latest = matches[0];
  results.innerHTML = `
    <section class="achievement-memory-summary" aria-label="Latest matching achievement">
      <span class="date-pill">${escapeHtml(formatDate(latest.date))}</span>
      <div>
        <p class="eyebrow">Last match</p>
        <strong>${escapeHtml(latest.title)}</strong>
        ${latest.meta ? `<div class="entry-meta">${escapeHtml(latest.meta)}</div>` : ""}
      </div>
    </section>
    <div class="progress-list achievement-memory-list">
      ${matches.slice(0, 3).map(renderAchievementMemoryItem).join("")}
    </div>
    <div class="section-actions achievement-memory-actions">
      <span class="todo-count">${matches.length} ${matches.length === 1 ? "match" : "matches"}</span>
      <button class="ghost-button compact-button" type="button" data-action="open-achievements-search">Open achievements</button>
    </div>
  `;
}

function renderDashboardRecent() {
  const list = $("#dashboardRecent");
  const hiddenTaskIds = new Set(state.hiddenDashboardTaskIds.map(String));
  const hiddenEntryIds = new Set(state.hiddenDashboardEntryIds.map(String));
  const entries = (Array.isArray(state.entries) ? state.entries : []).map((entry) => ({
        kind: "entry",
        date: entry.entry_date,
        title: entry.title,
        meta: [entry.project, `${entry.evidence_count} evidence`].filter(Boolean).join(" / "),
        entryId: entry.id,
      }));
  const hiddenCount =
    progressRows().filter((row) => row.kind === "task" && hiddenTaskIds.has(String(row.taskId))).length +
    entries.filter((row) => hiddenEntryIds.has(String(row.entryId))).length;
  $("#restoreHiddenProgress").classList.toggle("hidden", hiddenCount === 0);
  const rows = [
    ...progressRows().filter((row) => row.kind !== "task" || !hiddenTaskIds.has(String(row.taskId))),
    ...entries.filter((row) => !hiddenEntryIds.has(String(row.entryId))),
  ]
    .sort((a, b) => String(b.date).localeCompare(String(a.date)))
    .slice(0, 6);

  if (rows.length === 0) {
    list.innerHTML = `<div class="empty-state">No progress logged yet.</div>`;
    return;
  }

  list.innerHTML = rows
    .map((row) => {
      const action = row.kind === "task" ? "edit-task" : "edit-entry";
      const data = row.kind === "task" ? `data-task-id="${escapeHtml(row.taskId)}"` : `data-entry-id="${escapeHtml(row.entryId)}"`;
      const hideAction = row.kind === "task" ? "hide-dashboard-task" : "hide-dashboard-entry";
      return `
        <article class="progress-item" ${data}>
          <span class="date-pill">${escapeHtml(formatDate(row.date))}</span>
          <div>
            <strong>${escapeHtml(row.title)}</strong>
            ${row.meta ? `<div class="entry-meta">${escapeHtml(row.meta)}</div>` : ""}
          </div>
          <div class="progress-actions">
            <button class="link-button subtle" type="button" data-action="${hideAction}">Hide</button>
            <button class="link-button" type="button" data-action="${action}">Edit</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderTaskDetail() {
  const buckets = taskBuckets();
  const hiddenUpcomingIds = new Set(state.hiddenUpcomingTaskIds.map(String));
  const rawTasks = buckets[state.taskBucket] || buckets.inbox;
  const tasks = state.taskBucket === "upcoming" && !state.showHiddenUpcoming
    ? rawTasks.filter((task) => !hiddenUpcomingIds.has(String(task.id)))
    : rawTasks;
  const title = taskBucketTitle(state.taskBucket);
  $("#taskDetailHeading").textContent = title;
  $("#taskDetailCount").innerHTML = countMarkup(
    tasks.length,
    state.taskBucket === "incomplete" ? "open" : state.taskBucket === "complete" ? "done" : state.taskBucket === "upcoming" ? "items" : state.taskBucket === "today" ? "today" : "undated"
  );
  $("#taskDetailList").innerHTML = tasks.length
    ? tasks.map((task) => renderTaskAgendaItem(task, { expanded: true, allowHideUpcoming: state.taskBucket === "upcoming" })).join("")
    : `<div class="empty-state">No tasks here.</div>`;
}

function openTaskBucket(bucket) {
  state.taskBucket = bucket;
  switchView("taskDetail");
}

function renderEntries() {
  const filters = getDiaryFilters();
  const hiddenEntryIds = new Set(state.hiddenEntryIds.map(String));
  const allEntries = Array.isArray(state.entries) ? state.entries.filter((entry) => entryMatches(entry, filters)) : [];
  const hiddenCount = allEntries.filter((entry) => hiddenEntryIds.has(String(entry.id))).length;
  const entries = state.showHiddenEntries ? allEntries : allEntries.filter((entry) => !hiddenEntryIds.has(String(entry.id)));
  const list = $("#entryList");
  if (entries.length === 0) {
    list.innerHTML = `
      ${hiddenCount ? `<button class="ghost-button" type="button" data-action="toggle-hidden-entries">${state.showHiddenEntries ? "Hide hidden" : "Show hidden"}</button>` : ""}
      <div class="empty-state">No CV notes found.</div>
    `;
    return;
  }

  list.innerHTML = `
    ${hiddenCount ? `<button class="ghost-button" type="button" data-action="toggle-hidden-entries">${state.showHiddenEntries ? "Hide hidden" : "Show hidden"}</button>` : ""}
    ${entries
    .map((entry) => {
      const hidden = hiddenEntryIds.has(String(entry.id));
      const meta = [
        entry.project ? escapeHtml(entry.project) : "",
        entry.difficulty ? escapeHtml(entry.difficulty) : "",
        `${entry.evidence_count} evidence`,
      ]
        .filter(Boolean)
        .join(" / ");
      const tags = [...(entry.skills_used || []), ...(entry.tags || [])];
      return `
        <article class="entry-card ${entry.source_mode === "quick_log" ? "quick" : ""} ${hidden ? "is-hidden-local" : ""}" data-entry-id="${entry.id}">
          <div class="card-top">
            <div>
              <h3>${escapeHtml(entry.title)}</h3>
              <div class="entry-meta">${meta}</div>
            </div>
            <span class="date-pill">${escapeHtml(formatDate(entry.entry_date))}</span>
          </div>
          <p class="entry-text">${escapeHtml(truncate(entry.what_i_did))}</p>
          ${entry.outcome ? `<p class="entry-text"><strong>Outcome:</strong> ${escapeHtml(entry.outcome)}</p>` : ""}
          ${chips(tags)}
          <div class="card-actions">
            ${
              hidden
                ? `<button class="secondary-button" type="button" data-action="unhide-entry">Unhide</button>`
                : `<button class="secondary-button" type="button" data-action="hide-entry">Hide</button>`
            }
            <button class="secondary-button" type="button" data-action="edit-entry">Edit</button>
            <button class="ghost-button danger" type="button" data-action="delete-entry">Delete</button>
          </div>
        </article>
      `;
    })
    .join("")}
  `;
}

function renderAchievements() {
  const search = compact($("#achievementSearchInput").value).toLowerCase();
  const achievements = progressRows().filter((item) =>
    achievementMatches(item, search)
  );
  const list = $("#achievementList");
  if (achievements.length === 0) {
    list.innerHTML = `<div class="empty-state">No achievements found. Complete a task, add a task progress note, or save a CV note to build your career log.</div>`;
    return;
  }

  list.innerHTML = achievements
    .map((item) => {
      const tags = item.chips || [];
      const action = item.kind === "task" ? "edit-task" : "edit-entry";
      const data = item.kind === "task" ? `data-task-id="${escapeHtml(item.taskId)}"` : `data-entry-id="${escapeHtml(item.entryId)}"`;
      return `
        <article class="achievement-card" ${data}>
          <span class="date-pill">${escapeHtml(formatDate(item.date))}</span>
          <div class="achievement-main">
            <p class="achievement-bullet">${escapeHtml(item.title)}</p>
            ${item.meta ? `<div class="entry-meta">${escapeHtml(item.meta)}</div>` : ""}
            ${chips(tags)}
          </div>
          <button class="link-button" type="button" data-action="${action}">${item.kind === "task" ? "Edit task" : "Edit source"}</button>
        </article>
      `;
    })
    .join("");
}

function evidenceDate(item) {
  if (item.entry_date) return item.entry_date;
  if (item.created_at) return String(item.created_at).slice(0, 10);
  const entry = findEntry(item.work_entry_id);
  return entry?.entry_date || "";
}

function calendarActivity(date) {
  const tasks = (Array.isArray(state.tasks) ? state.tasks : []).filter((task) => taskFallsOnDate(task, date));
  const entries = (Array.isArray(state.entries) ? state.entries : []).filter((entry) => entry.entry_date === date);
  const achievements = (Array.isArray(state.achievements) ? state.achievements : []).filter((item) => item.achieved_at === date);
  const evidence = (Array.isArray(state.evidence) ? state.evidence : []).filter((item) => evidenceDate(item) === date);
  return { tasks, entries, achievements, evidence };
}

function calendarMarkers(activity) {
  return [
    activity.tasks.length ? `<span class="calendar-dot task" title="${activity.tasks.length} tasks"></span>` : "",
    activity.entries.length ? `<span class="calendar-dot entry" title="${activity.entries.length} CV notes"></span>` : "",
    activity.achievements.length ? `<span class="calendar-dot achievement" title="${activity.achievements.length} achievements"></span>` : "",
    activity.evidence.length ? `<span class="calendar-dot evidence" title="${activity.evidence.length} evidence items"></span>` : "",
  ].join("");
}

function renderCalendarAgenda() {
  const date = state.calendarSelectedDate;
  const activity = calendarActivity(date);
  const rows = [
    ...activity.tasks.map((task) => ({
      action: "calendar-open-task",
      title: task.title,
      type: task.completed ? "Done task" : taskIsLate(task) ? "Late task" : "Task",
      meta: [task.project, taskIsLate(task) ? "Late" : "", taskDateLabel(task, { short: true }), taskTimeLabel(task)].filter(Boolean).join(" / "),
      id: task.id,
    })),
    ...activity.entries.map((entry) => ({
      action: "calendar-open-entry",
      title: entry.title,
      type: "CV",
      meta: entry.project || "",
      id: entry.id,
    })),
    ...activity.achievements.map((item) => ({
      action: "calendar-open-achievement",
      title: item.bullet,
      type: "Achievement",
      meta: item.project || "",
      id: item.source_entry_id,
    })),
    ...activity.evidence.map((item) => ({
      action: "calendar-open-evidence",
      title: item.title || item.description || "Image evidence",
      type: item.evidence_type_label || "Evidence",
      meta: item.description || "",
      id: item.work_entry_id,
    })),
  ];

  $("#calendarAgenda").innerHTML = `
    <div class="calendar-agenda-head">
      <span class="date-pill">${escapeHtml(formatDate(date))}</span>
      <strong>${rows.length} item${rows.length === 1 ? "" : "s"}</strong>
    </div>
    ${
      rows.length
        ? rows
            .map((row) => {
              return `
                <button class="agenda-item" type="button" data-action="${escapeHtml(row.action)}" data-id="${escapeHtml(row.id || "")}">
                  <span>${escapeHtml(row.type)}</span>
                  <strong>${escapeHtml(row.title)}</strong>
                  ${row.meta ? `<small>${escapeHtml(row.meta)}</small>` : ""}
                </button>
              `;
            })
            .join("")
        : `<div class="empty-state compact-empty">No work recorded for this date.</div>`
    }
  `;
}

function renderCalendar() {
  $("#calendarMonthLabel").textContent = monthTitle(state.calendarMonth);
  const tomorrowDate = new Date();
  tomorrowDate.setDate(tomorrowDate.getDate() + 1);
  const tomorrow = dateInputValue(tomorrowDate);
  $("#calendarToday").classList.toggle("active", state.calendarSelectedDate === today());
  $("#calendarTomorrow").classList.toggle("active", state.calendarSelectedDate === tomorrow);
  const first = monthStart(state.calendarMonth);
  const firstWeekday = first.getDay();
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
  const days = [];
  for (let index = 0; index < firstWeekday; index += 1) {
    days.push(`<span class="calendar-pad" aria-hidden="true"></span>`);
  }
  for (let day = 1; day <= last.getDate(); day += 1) {
    const date = dateInputValue(new Date(first.getFullYear(), first.getMonth(), day));
    const activity = calendarActivity(date);
    const count = activity.tasks.length + activity.entries.length + activity.achievements.length + activity.evidence.length;
    days.push(`
      <button class="calendar-day ${date === today() ? "today" : ""} ${date === state.calendarSelectedDate ? "selected" : ""}" type="button" data-date="${escapeHtml(date)}" aria-label="${escapeHtml(`${formatDate(date)}, ${count} items`)}">
        <span>${day}</span>
        <div class="calendar-markers">${calendarMarkers(activity)}</div>
      </button>
    `);
  }
  $("#calendarGrid").innerHTML = days.join("");
  renderCalendarAgenda();
}

function render() {
  renderOverview();
  renderTasks();
  renderTaskDetail();
  renderWorkHub();
  renderEntries();
  renderAchievements();
  renderCalendar();
  renderGoogleIntegration();
}

function setDrawerContent(mode) {
  const details = mode === "details";
  const task = mode === "task";
  const photo = mode === "photo";
  $("#drawerPhotoForm").classList.toggle("hidden", !photo);
  $("#taskEditForm").classList.toggle("hidden", !task);
  $("#entryForm").classList.toggle("hidden", !details);
}

function findEntry(entryId) {
  return (Array.isArray(state.entries) ? state.entries : []).find((entry) => String(entry.id) === String(entryId));
}

function openDrawer(entry = null, mode = "photo") {
  const details = mode === "details";
  state.selectedEntry = entry;
  state.selectedTask = null;
  state.pendingEvidence = [];
  lockPageScroll();
  $("#entryDrawer").classList.remove("hidden");
  $("#entryDrawer").setAttribute("aria-hidden", "false");
  $("#drawerMode").textContent = details ? "Edit journal" : "New evidence";
  $("#drawerTitle").textContent = details ? entry?.title || "Journal entry" : "Image evidence";

  if (details) {
    fillEntryForm(entry);
  } else {
    clearPhotoForm(entry);
  }
  setDrawerContent(mode);
}

function openTaskEditor(task) {
  state.selectedEntry = null;
  state.selectedTask = task;
  state.pendingEvidence = [];
  lockPageScroll();
  $("#entryDrawer").classList.remove("hidden");
  $("#entryDrawer").setAttribute("aria-hidden", "false");
  $("#drawerMode").textContent = "Edit task";
  $("#drawerTitle").textContent = task?.title || "Planner task";
  fillTaskForm(task);
  setDrawerContent("task");
}

function closeDrawer() {
  const drawer = $("#entryDrawer");
  if (drawer) {
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }
  state.selectedEntry = null;
  state.selectedTask = null;
  state.selectedPhotoFile = null;
  state.pendingEvidence = [];
  unlockPageScrollIfClear();
}

function fillEntryForm(entry) {
  $("#entryId").value = entry?.id || "";
  $("#entryDate").value = entry?.entry_date || today();
  $("#entryDifficulty").value = entry?.difficulty || "";
  $("#entryTitle").value = entry?.title || "";
  $("#entryWhat").value = entry?.what_i_did || "";
  setProjectSelect($("#entryProjectId"), entry?.project_id || projectIdByName(entry?.project || ""));
  syncProjectHidden("entryProjectId", "entryProject");
  $("#entrySkills").value = (entry?.skills_used || []).join(", ");
  $("#entryOutcome").value = entry?.outcome || "";
  $("#entryTags").value = (entry?.tags || []).join(", ");
  $("#entryReflection").value = entry?.reflection_notes || "";
  $("#entryBullet").value = entry?.cv_bullet_draft || "";
  $("#deleteEntry").style.display = entry ? "" : "none";
  clearEvidenceDraft();
  renderEntryEvidenceList();
}

function entryPayload() {
  const project = selectedProjectPayload("entryProjectId");
  return {
    entry_date: $("#entryDate").value,
    title: $("#entryTitle").value,
    what_i_did: $("#entryWhat").value,
    project_id: project.project_id,
    project: project.project,
    skills_used: splitList($("#entrySkills").value),
    outcome: $("#entryOutcome").value,
    tags: splitList($("#entryTags").value),
    difficulty: $("#entryDifficulty").value,
    reflection_notes: $("#entryReflection").value,
    cv_bullet_draft: $("#entryBullet").value,
    source_mode: state.selectedEntry?.source_mode || "detailed",
  };
}

function fillTaskForm(task) {
  const repeatRule = task?.repeat_rule && task.repeat_rule !== "none" ? "interval" : "none";
  const repeatDays = Number(task?.repeat_interval_days || (task?.repeat_rule === "weekly" ? 7 : task?.repeat_rule === "monthly" ? 30 : 1));
  $("#editTaskId").value = task?.id || "";
  $("#editTaskTitle").value = task?.title || "";
  setProjectSelect($("#editTaskProjectId"), task?.project_id || projectIdByName(task?.project || ""));
  syncProjectHidden("editTaskProjectId", "editTaskProject");
  $("#editTaskStartDate").value = task?.start_date || "";
  updateDateButton("editTaskStartDate");
  $("#editTaskStartTime").value = task?.start_time || "";
  updateTimeButton("editTaskStartTime");
  $("#editTaskDueDate").value = task?.due_date || "";
  updateDateButton("editTaskDueDate");
  $("#editTaskDueTime").value = task?.due_time || "";
  updateTimeButton("editTaskDueTime");
  $("#editTaskReminder").value = task?.reminder_at || "";
  $("#editTaskRepeatRule").value = repeatRule;
  $("#editTaskRepeatIntervalDays").value = Number.isFinite(repeatDays) && repeatDays > 0 ? repeatDays : 1;
  $("#editTaskRepeatUntil").value = task?.repeat_until || "";
  updateDateButton("editTaskRepeatUntil");
  $("#editTaskLocation").value = task?.location || "";
  $("#editTaskNotes").value = task?.notes || "";
  $("#editTaskCompleted").checked = Boolean(task?.completed);
  updateRepeatControls("edit");
}

function taskEditPayload() {
  const repeatRule = $("#editTaskRepeatRule").value;
  const project = selectedProjectPayload("editTaskProjectId");
  return {
    title: $("#editTaskTitle").value,
    project_id: project.project_id,
    project: project.project,
    start_date: $("#editTaskStartDate").value,
    start_time: $("#editTaskStartTime").value,
    due_date: $("#editTaskDueDate").value,
    due_time: $("#editTaskDueTime").value,
    reminder_at: $("#editTaskReminder").value,
    repeat_rule: repeatRule,
    repeat_interval_days: repeatRule === "none" ? "" : $("#editTaskRepeatIntervalDays").value,
    repeat_until: repeatRule === "none" ? "" : $("#editTaskRepeatUntil").value,
    location: $("#editTaskLocation").value,
    notes: $("#editTaskNotes").value,
    completed: $("#editTaskCompleted").checked,
  };
}

function updateRepeatControls(prefix) {
  const isEdit = prefix === "edit";
  const ruleInput = $(isEdit ? "#editTaskRepeatRule" : "#taskRepeatRule");
  const intervalWrap = $(isEdit ? "#editTaskRepeatIntervalWrap" : "#taskRepeatIntervalWrap");
  const untilWrap = $(isEdit ? "#editTaskRepeatUntilWrap" : "#taskRepeatUntilWrap");
  if (!ruleInput || !intervalWrap || !untilWrap) return;
  const rule = ruleInput.value;
  const show = rule !== "none";
  intervalWrap.classList.toggle("hidden", !show);
  untilWrap.classList.toggle("hidden", !show);
}

function timeLabelId(targetId) {
  return {
    taskStartTime: "taskStartTimeLabel",
    taskDueTime: "taskDueTimeLabel",
    editTaskStartTime: "editTaskStartTimeLabel",
    editTaskDueTime: "editTaskDueTimeLabel",
  }[targetId] || "";
}

function dateLabelIds(targetId) {
  return {
    taskDate: ["taskDateLabel"],
    taskStartDate: ["taskStartDateLabel"],
    taskDueDate: ["taskEndDateLabel"],
    editTaskStartDate: ["editTaskStartDateLabel"],
    editTaskDueDate: ["editTaskDueDateLabel"],
    taskRepeatUntil: ["taskRepeatUntilLabel"],
    editTaskRepeatUntil: ["editTaskRepeatUntilLabel"],
  }[targetId] || [];
}

function updateDateButton(targetId) {
  const input = $(`#${targetId}`);
  const labels = dateLabelIds(targetId).map((labelId) => $(`#${labelId}`)).filter(Boolean);
  if (!input || !labels.length) return;
  const shouldShowWeekday = ["taskDate", "taskStartDate", "taskDueDate", "editTaskStartDate", "editTaskDueDate"].includes(targetId);
  const text = input.value
    ? shouldShowWeekday
      ? formatDateWithWeekday(input.value)
      : formatDate(input.value)
    : "No date";
  labels.forEach((label) => { label.textContent = text; });
}

function updateDateButtons() {
  ["taskDate", "taskStartDate", "taskDueDate", "editTaskStartDate", "editTaskDueDate", "taskRepeatUntil", "editTaskRepeatUntil"].forEach(updateDateButton);
}

function syncQuickTaskDateFromRange() {
  const quickDate = $("#taskDate");
  const startDate = $("#taskStartDate").value;
  const endDate = $("#taskDueDate").value;
  if (!quickDate) return;
  quickDate.value = startDate && startDate === endDate ? startDate : "";
  const label = $("#taskDateLabel");
  if (!label) return;
  label.textContent = quickDate.value
    ? formatDateWithWeekday(quickDate.value)
    : startDate || endDate
      ? "Custom dates"
      : "No date";
}

function updateTimeButton(targetId) {
  const input = $(`#${targetId}`);
  const label = $(`#${timeLabelId(targetId)}`);
  if (!input || !label) return;
  label.textContent = input.value ? formatTime(input.value) : "No time";
}

function updateTimeButtons() {
  ["taskStartTime", "taskDueTime", "editTaskStartTime", "editTaskDueTime"].forEach(updateTimeButton);
}

function parseTimeValue(value) {
  const match = String(value || "").match(/^(\d{2}):(\d{2})$/);
  if (!match) {
    return { hour24: 9, minute: 0 };
  }
  return {
    hour24: Math.max(0, Math.min(23, Number(match[1]))),
    minute: Math.max(0, Math.min(59, Number(match[2]))),
  };
}

function wheelScrollBehavior() {
  return document.body.classList.contains("reduce-motion") ? "auto" : "smooth";
}

function fastScrollToElement(selector, options = {}) {
  const element = $(selector);
  if (!element) return;
  const headerOffset = ($("#topbar")?.getBoundingClientRect().height || 0) + 10;
  const target = Math.max(0, window.scrollY + element.getBoundingClientRect().top - headerOffset);
  if (pageScrollAnimationFrame) {
    cancelAnimationFrame(pageScrollAnimationFrame);
    pageScrollAnimationFrame = 0;
  }
  if (document.body.classList.contains("reduce-motion")) {
    window.scrollTo(0, target);
    return;
  }

  const start = window.scrollY || window.pageYOffset || 0;
  const distance = target - start;
  if (Math.abs(distance) < 2) return;

  const duration = options.duration || 240;
  const startedAt = performance.now();
  const easeOutCubic = (value) => 1 - Math.pow(1 - value, 3);

  function step(now) {
    const progress = Math.min(1, (now - startedAt) / duration);
    window.scrollTo(0, start + distance * easeOutCubic(progress));
    if (progress < 1) {
      pageScrollAnimationFrame = requestAnimationFrame(step);
    } else {
      pageScrollAnimationFrame = 0;
    }
  }

  pageScrollAnimationFrame = requestAnimationFrame(step);
}

function wheelScrollPositions(selector) {
  return $$(".wheel-column > div", $(selector)).map((column) => column.scrollTop);
}

function restoreWheelScrollPositions(selector, positions) {
  $$(".wheel-column > div", $(selector)).forEach((column, index) => {
    if (Number.isFinite(positions[index])) {
      column.scrollTop = positions[index];
    }
  });
}

function animateWheelScroll(column, targetTop, behavior = wheelScrollBehavior()) {
  const maxTop = Math.max(0, column.scrollHeight - column.clientHeight);
  const target = Math.max(0, Math.min(maxTop, targetTop));
  const start = column.scrollTop;
  const distance = target - start;
  const previousFrame = wheelAnimationFrames.get(column);
  if (previousFrame) {
    cancelAnimationFrame(previousFrame);
    wheelAnimationFrames.delete(column);
  }

  if (Math.abs(distance) < 1 || behavior === "auto") {
    column.scrollTop = target;
    return;
  }

  const duration = Math.min(260, Math.max(120, Math.abs(distance) * 0.45));
  const startedAt = performance.now();
  const easeOutCubic = (progress) => 1 - Math.pow(1 - progress, 3);

  const step = (time) => {
    const progress = Math.min(1, (time - startedAt) / duration);
    column.scrollTop = start + distance * easeOutCubic(progress);
    if (progress < 1) {
      wheelAnimationFrames.set(column, requestAnimationFrame(step));
    } else {
      wheelAnimationFrames.delete(column);
      column.scrollTop = target;
    }
  };

  wheelAnimationFrames.set(column, requestAnimationFrame(step));
}

function scrollWheelButton(button, behavior = wheelScrollBehavior()) {
  const column = button.parentElement;
  if (!column) {
    button.scrollIntoView({ block: "center", inline: "nearest", behavior });
    return;
  }
  const columnRect = column.getBoundingClientRect();
  const buttonRect = button.getBoundingClientRect();
  const targetTop = column.scrollTop + buttonRect.top - columnRect.top - column.clientHeight / 2 + buttonRect.height / 2;
  animateWheelScroll(column, targetTop, behavior);
}

function updateWheelPartSelection(selector, part, value, shouldScroll = false) {
  const root = $(selector);
  if (!root) return;
  let activeButton = null;
  $$(`[data-wheel-part="${part}"]`, root).forEach((button) => {
    const active = String(button.dataset.value) === String(value);
    button.classList.toggle("active", active);
    if (active) activeButton = button;
  });
  if (shouldScroll && activeButton) {
    scrollWheelButton(activeButton);
  }
}

function setSelectedTimeValue(value, options = {}) {
  const parsed = parseTimeValue(value);
  state.selectedTimeValue = `${String(parsed.hour24).padStart(2, "0")}:${String(parsed.minute).padStart(2, "0")}`;
  if (options.render === false) {
    updateTimePickerSelection(options.scrollPart || "");
  } else {
    renderTimePicker();
  }
}

function wheelButton(value, label, selected, part) {
  return `<button class="${selected ? "active" : ""}" type="button" data-wheel-part="${escapeHtml(part)}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`;
}

function dayWheelButton(year, month, day, selected) {
  const value = dateInputValue(new Date(year, month, day));
  const weekday = formatWeekday(value);
  return `
    <button class="wheel-day-button ${selected ? "active" : ""}" type="button" data-wheel-part="day" data-value="${escapeHtml(day)}" aria-label="${escapeHtml(`${weekday} ${day}`)}">
      <span class="wheel-day-number">${escapeHtml(day)}</span>
      <small class="wheel-day-weekday">${escapeHtml(weekday)}</small>
    </button>
  `;
}

function renderTimePicker(options = {}) {
  const preserveScroll = options.preserveScroll !== false;
  const scrollPositions = preserveScroll ? wheelScrollPositions("#timeWheel") : [];
  const parsed = parseTimeValue(state.selectedTimeValue);
  const hourOptions = Array.from({ length: 24 }, (_, index) => index);
  const scrollBehavior = options.scrollBehavior || wheelScrollBehavior();
  const display = formatTime(state.selectedTimeValue);
  $("#timePreview").textContent = display;
  $("#timePreviewPeriod").textContent = "Europe/London";
  $("#timeWheel").classList.add("is-24");
  $("#timeWheel").innerHTML = `
    <div class="wheel-column" aria-label="Hour">
      <span>Hour</span>
      <div>${hourOptions.map((hour) => wheelButton(String(hour), String(hour), hour === parsed.hour24, "hour")).join("")}</div>
    </div>
    <div class="wheel-column" aria-label="Minute">
      <span>Min</span>
      <div>${Array.from({ length: 60 }, (_, minute) => wheelButton(String(minute), String(minute).padStart(2, "0"), minute === parsed.minute, "minute")).join("")}</div>
    </div>
  `;
  if (preserveScroll) {
    restoreWheelScrollPositions("#timeWheel", scrollPositions);
  }
  centerWheelSelections("#timeWheel", scrollBehavior);
}

function updateTimePickerSelection(scrollPart = "") {
  const parsed = parseTimeValue(state.selectedTimeValue);
  $("#timePreview").textContent = formatTime(state.selectedTimeValue);
  $("#timePreviewPeriod").textContent = "Europe/London";
  updateWheelPartSelection("#timeWheel", "hour", parsed.hour24, scrollPart === "hour");
  updateWheelPartSelection("#timeWheel", "minute", parsed.minute, scrollPart === "minute");
}

function handleTimeWheel(part, value) {
  const parsed = parseTimeValue(state.selectedTimeValue);
  let hour24 = parsed.hour24;
  let minute = parsed.minute;
  if (part === "hour") {
    hour24 = Number(value);
  }
  if (part === "minute") {
    minute = Number(value);
  }
  setSelectedTimeValue(`${String(hour24).padStart(2, "0")}:${String(minute).padStart(2, "0")}`, {
    render: false,
    scrollPart: part,
  });
}

function parseDateParts(value) {
  const date = value && !Number.isNaN(new Date(`${value}T00:00:00`).getTime())
    ? new Date(`${value}T00:00:00`)
    : new Date(`${today()}T00:00:00`);
  return {
    year: date.getFullYear(),
    month: date.getMonth(),
    day: date.getDate(),
  };
}

function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

function setSelectedDateParts(nextParts, options = {}) {
  const current = parseDateParts(state.selectedDateValue);
  const year = nextParts.year ?? current.year;
  const month = nextParts.month ?? current.month;
  const maxDay = daysInMonth(year, month);
  const day = Math.min(nextParts.day ?? current.day, maxDay);
  const needsRender = year !== current.year || month !== current.month;
  state.selectedDateValue = dateInputValue(new Date(year, month, day));
  if (needsRender) {
    renderDatePicker();
  } else {
    updateDatePickerSelection(options.scrollPart || "");
  }
}

function renderDatePicker(options = {}) {
  const preserveScroll = options.preserveScroll !== false;
  const scrollPositions = preserveScroll ? wheelScrollPositions("#dateWheel") : [];
  const parts = parseDateParts(state.selectedDateValue);
  const currentYear = new Date().getFullYear();
  const scrollBehavior = options.scrollBehavior || wheelScrollBehavior();
  const firstYear = Math.min(currentYear - 1, parts.year - 1);
  const lastYear = Math.max(currentYear + 10, parts.year + 1);
  const years = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => firstYear + index);
  $("#datePreview").textContent = formatDate(state.selectedDateValue);
  $("#datePreviewDetail").textContent = state.selectedDateValue === today()
    ? `Today / ${formatWeekday(state.selectedDateValue)}`
    : formatWeekday(state.selectedDateValue);
  $("#dateWheel").innerHTML = `
    <div class="wheel-column" aria-label="Month">
      <span>Month</span>
      <div>${MONTH_NAMES.map((month, index) => wheelButton(String(index), month, index === parts.month, "month")).join("")}</div>
    </div>
    <div class="wheel-column" aria-label="Day">
      <span>Day</span>
      <div>${Array.from({ length: daysInMonth(parts.year, parts.month) }, (_, index) => {
        const day = index + 1;
        return dayWheelButton(parts.year, parts.month, day, day === parts.day);
      }).join("")}</div>
    </div>
    <div class="wheel-column" aria-label="Year">
      <span>Year</span>
      <div>${years.map((year) => wheelButton(String(year), String(year), year === parts.year, "year")).join("")}</div>
    </div>
  `;
  if (preserveScroll) {
    restoreWheelScrollPositions("#dateWheel", scrollPositions);
  }
  centerWheelSelections("#dateWheel", scrollBehavior);
}

function updateDatePickerSelection(scrollPart = "") {
  const parts = parseDateParts(state.selectedDateValue);
  $("#datePreview").textContent = formatDate(state.selectedDateValue);
  $("#datePreviewDetail").textContent = state.selectedDateValue === today()
    ? `Today / ${formatWeekday(state.selectedDateValue)}`
    : formatWeekday(state.selectedDateValue);
  updateWheelPartSelection("#dateWheel", "month", parts.month, scrollPart === "month");
  updateWheelPartSelection("#dateWheel", "day", parts.day, scrollPart === "day");
  updateWheelPartSelection("#dateWheel", "year", parts.year, scrollPart === "year");
}

function scrollWheelSelections(selector, behavior = wheelScrollBehavior()) {
  $$(".wheel-column button.active", $(selector)).forEach((button) => {
    scrollWheelButton(button, behavior);
  });
}

function centerWheelSelections(selector, behavior = wheelScrollBehavior()) {
  if (behavior === "auto") {
    scrollWheelSelections(selector, "auto");
    requestAnimationFrame(() => {
      scrollWheelSelections(selector, "auto");
      requestAnimationFrame(() => scrollWheelSelections(selector, "auto"));
    });
    return;
  }
  requestAnimationFrame(() => scrollWheelSelections(selector, behavior));
}

function handleDateWheel(part, value) {
  if (part === "month") setSelectedDateParts({ month: Number(value) }, { scrollPart: "month" });
  if (part === "day") setSelectedDateParts({ day: Number(value) }, { scrollPart: "day" });
  if (part === "year") setSelectedDateParts({ year: Number(value) }, { scrollPart: "year" });
}

function openTimePicker(targetId, title = "Set time") {
  const input = $(`#${targetId}`);
  const drawer = $("#timeDrawer");
  if (!input || !drawer) return;
  state.activeTimeTarget = targetId;
  state.selectedTimeValue = input.value || londonTimeValue();
  $("#timeTitle").textContent = title;
  lockPageScroll();
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  renderTimePicker({ scrollBehavior: "auto", preserveScroll: false });
}

function closeTimePicker() {
  const drawer = $("#timeDrawer");
  if (drawer) {
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }
  state.activeTimeTarget = "";
  unlockPageScrollIfClear();
}

function setSelectedTime() {
  if (!state.activeTimeTarget) return;
  const input = $(`#${state.activeTimeTarget}`);
  if (!input) return;
  input.value = state.selectedTimeValue;
  updateTimeButton(state.activeTimeTarget);
  closeTimePicker();
}

function clearSelectedTime() {
  if (!state.activeTimeTarget) return;
  const input = $(`#${state.activeTimeTarget}`);
  if (!input) return;
  input.value = "";
  updateTimeButton(state.activeTimeTarget);
  closeTimePicker();
}

function openDatePicker(targetId, title = "Set date") {
  const input = $(`#${targetId}`);
  const drawer = $("#dateDrawer");
  if (!input || !drawer) return;
  state.activeDateTarget = targetId;
  state.selectedDateValue = input.value || today();
  $("#dateTitle").textContent = title;
  lockPageScroll();
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  renderDatePicker({ scrollBehavior: "auto", preserveScroll: false });
}

function closeDatePicker() {
  const drawer = $("#dateDrawer");
  if (drawer) {
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }
  state.activeDateTarget = "";
  unlockPageScrollIfClear();
}

function setSelectedDate() {
  if (!state.activeDateTarget) return;
  const input = $(`#${state.activeDateTarget}`);
  if (!input) return;
  input.value = state.selectedDateValue;
  if (state.activeDateTarget === "taskDate") {
    $("#taskStartDate").value = state.selectedDateValue;
    $("#taskDueDate").value = state.selectedDateValue;
    updateDateButton("taskStartDate");
    updateDateButton("taskDueDate");
  } else {
    updateDateButton(state.activeDateTarget);
    if (["taskStartDate", "taskDueDate"].includes(state.activeDateTarget)) {
      syncQuickTaskDateFromRange();
    }
  }
  updateDateButton(state.activeDateTarget);
  closeDatePicker();
}

function clearSelectedDate() {
  if (!state.activeDateTarget) return;
  const input = $(`#${state.activeDateTarget}`);
  if (!input) return;
  input.value = "";
  if (state.activeDateTarget === "taskDate") {
    $("#taskStartDate").value = "";
    $("#taskDueDate").value = "";
    updateDateButton("taskStartDate");
    updateDateButton("taskDueDate");
  } else {
    updateDateButton(state.activeDateTarget);
    if (["taskStartDate", "taskDueDate"].includes(state.activeDateTarget)) {
      syncQuickTaskDateFromRange();
    }
  }
  updateDateButton(state.activeDateTarget);
  closeDatePicker();
}

function updateTaskComposerAdvanced() {
  const fields = $("#taskAdvancedFields");
  const button = $("#toggleTaskAdvanced");
  if (!fields || !button) return;
  fields.classList.toggle("hidden", !state.taskComposerAdvanced);
  button.setAttribute("aria-expanded", state.taskComposerAdvanced ? "true" : "false");
  button.classList.toggle("active", state.taskComposerAdvanced);
}

function toggleTaskComposerAdvanced() {
  state.taskComposerAdvanced = !state.taskComposerAdvanced;
  updateTaskComposerAdvanced();
}

function toggleTaskListExpanded() {
  state.taskListExpanded = !state.taskListExpanded;
  renderTasks();
}

function lockPageScroll() {
  if (document.body.classList.contains("modal-open")) return;
  state.lockedScrollY = window.scrollY || 0;
  document.body.style.top = `-${state.lockedScrollY}px`;
  document.body.classList.add("modal-open");
}

function unlockPageScrollIfClear() {
  const overlayOpen = ["#entryDrawer", "#calendarDrawer", "#settingsDrawer", "#timeDrawer", "#dateDrawer"].some((selector) => {
    const element = $(selector);
    return element && !element.classList.contains("hidden");
  });
  if (overlayOpen) return;
  document.body.classList.remove("modal-open");
  document.body.style.top = "";
  window.scrollTo(0, state.lockedScrollY || 0);
}

function clearPhotoForm(entry = null) {
  state.selectedPhotoFile = null;
  $("#drawerPhotoCamera").value = "";
  $("#drawerPhotoFile").value = "";
  $("#drawerPhotoComment").value = "";
  $("#drawerPhotoDate").value = entry?.entry_date || today();
  setProjectSelect($("#drawerPhotoProjectId"), entry?.project_id || projectIdByName(entry?.project || ""));
  syncProjectHidden("drawerPhotoProjectId", "drawerPhotoProject");
  $("#selectedPhotoName").textContent = "";
  $("#selectedPhotoName").classList.add("hidden");
  $("#photoDetails").classList.add("hidden");
}

function openPrimaryAdd() {
  openDrawer(null, "photo");
}

function handlePhotoSelection(event) {
  const file = event.target.files[0];
  if (!file) return;
  state.selectedPhotoFile = file;
  $("#selectedPhotoName").textContent = file.name || "Selected image";
  $("#selectedPhotoName").classList.remove("hidden");
  $("#photoDetails").classList.remove("hidden");
}

function readImageAsDataUrl(file, maxSize = 1600, quality = 0.82) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error("Choose an image first."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the image."));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error("Could not load the image."));
      image.onload = () => {
        const scale = Math.min(1, maxSize / Math.max(image.width, image.height));
        const width = Math.max(1, Math.round(image.width * scale));
        const height = Math.max(1, Math.round(image.height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        context.drawImage(image, 0, 0, width, height);
        const dataUrl = canvas.toDataURL("image/jpeg", quality);
        resolve({
          data_url: dataUrl,
          content_type: "image/jpeg",
          filename: file.name || "photo.jpg",
        });
      };
      image.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  });
}

async function saveImageEvidence(event) {
  event.preventDefault();
  const file = state.selectedPhotoFile;
  const comment = compact($("#drawerPhotoComment").value);
  if (!file) {
    showToast("Choose an image first.");
    return;
  }

  const image = await readImageAsDataUrl(file);
  const project = selectedProjectPayload("drawerPhotoProjectId");
  await api("/api/image-evidence", {
    method: "POST",
    body: {
      ...image,
      work_entry_id: state.selectedEntry?.id || "",
      entry_date: $("#drawerPhotoDate").value || today(),
      project_id: project.project_id,
      project: project.project,
      comment,
    },
  });
  closeDrawer();
  await loadData();
  showToast("Image evidence saved.");
}

async function saveTaskEdits(event) {
  event.preventDefault();
  const taskId = $("#editTaskId").value;
  const titleInput = $("#editTaskTitle");
  const title = compact(titleInput.value);
  if (!taskId || !state.selectedTask) return;
  if (!title) {
    showToast("Add a task name first.");
    titleInput.focus();
    return;
  }
  const scheduleError = taskScheduleValidationMessage(
    $("#editTaskStartDate").value,
    $("#editTaskDueDate").value,
    $("#editTaskStartTime").value,
    $("#editTaskDueTime").value,
  );
  if (scheduleError) {
    showToast(scheduleError);
    return;
  }

  if (!setFormPending(event.currentTarget, true)) return;
  try {
    await api(`/api/tasks/${taskId}`, {
      method: "PUT",
      body: taskEditPayload(),
    });
    closeDrawer();
    await loadData();
    showToast("Task updated.");
  } finally {
    setFormPending(event.currentTarget, false);
  }
}

async function saveEntry(event) {
  event.preventDefault();
  const id = $("#entryId").value;
  const saved = id
    ? await api(`/api/entries/${id}`, { method: "PUT", body: entryPayload() })
    : await api("/api/entries", { method: "POST", body: entryPayload() });

  for (const item of state.pendingEvidence) {
    await api("/api/evidence", {
      method: "POST",
      body: {
        ...item,
        work_entry_id: saved.id,
      },
    });
  }

  closeDrawer();
  await loadData();
  showToast("Saved.");
}

async function saveQuickLog(event) {
  event.preventDefault();
  const noteInput = $("#inlineQuickNote");
  const note = compact(noteInput.value);
  if (!note) {
    showToast("Add a note first.");
    noteInput.focus();
    return;
  }

  await api("/api/quick-logs", {
    method: "POST",
    body: {
      note,
      entry_date: $("#inlineQuickDate").value || today(),
      ...selectedProjectPayload("inlineQuickProjectId"),
    },
  });
  noteInput.value = "";
  $("#inlineQuickProjectId").value = "";
  syncProjectHidden("inlineQuickProjectId", "inlineQuickProject");
  await loadData();
  showToast("Journal saved.");
}

function clearTaskComposer(options = {}) {
  $("#taskTitle").value = "";
  $("#taskDate").value = "";
  updateDateButton("taskDate");
  $("#taskStartDate").value = "";
  updateDateButton("taskStartDate");
  $("#taskStartTime").value = "";
  updateTimeButton("taskStartTime");
  $("#taskDueDate").value = "";
  updateDateButton("taskDueDate");
  $("#taskDueTime").value = "";
  updateTimeButton("taskDueTime");
  $("#taskProjectId").value = "";
  $("#taskRepeatRule").value = "none";
  $("#taskRepeatIntervalDays").value = "1";
  $("#taskRepeatUntil").value = "";
  $("#taskNotes").value = "";
  updateDateButton("taskRepeatUntil");
  updateRepeatControls("create");
  if (options.toast) {
    showToast("Planner fields cleared.");
  }
}

async function saveTask(event) {
  event.preventDefault();
  const titleInput = $("#taskTitle");
  const title = compact(titleInput.value);
  const repeatRule = $("#taskRepeatRule").value;
  const project = selectedProjectPayload("taskProjectId");
  if (!title) {
    showToast("Add a task first.");
    titleInput.focus();
    return;
  }
  const scheduleError = taskScheduleValidationMessage(
    $("#taskStartDate").value,
    $("#taskDueDate").value,
    $("#taskStartTime").value,
    $("#taskDueTime").value,
  );
  if (scheduleError) {
    showToast(scheduleError);
    return;
  }

  if (!setFormPending(event.currentTarget, true)) return;
  try {
    await api("/api/tasks", {
      method: "POST",
      body: {
        title,
        start_date: $("#taskStartDate").value,
        start_time: $("#taskStartTime").value,
        due_date: $("#taskDueDate").value,
        due_time: $("#taskDueTime").value,
        project_id: project.project_id,
        project: project.project,
        repeat_rule: repeatRule,
        repeat_interval_days: repeatRule === "none" ? "" : $("#taskRepeatIntervalDays").value,
        repeat_until: repeatRule === "none" ? "" : $("#taskRepeatUntil").value,
        notes: $("#taskNotes").value,
      },
    });
    clearTaskComposer();
    await loadData();
    showToast("Task added.");
  } finally {
    setFormPending(event.currentTarget, false);
  }
}

function projectFormPayload() {
  return {
    name: $("#projectName").value,
    goal: $("#projectGoal").value,
    deadline: $("#projectDeadline").value,
    status: $("#projectStatus").value || "planned",
    color: $("#projectColor").value || state.settings.accentColor || "#5DD4C0",
    notes: $("#projectNotes").value,
  };
}

function fillProjectForm(project = null) {
  $("#projectId").value = project?.id || "";
  $("#projectName").value = project?.name || "";
  $("#projectGoal").value = project?.goal || "";
  $("#projectDeadline").value = project?.deadline || "";
  $("#projectStatus").value = project?.status || "planned";
  $("#projectColor").value = project?.color || state.settings.accentColor || "#5DD4C0";
  $("#projectNotes").value = project?.notes || "";
}

function clearProjectForm() {
  fillProjectForm(null);
  state.projectFormVisible = false;
  renderProjects();
}

function showProjectForm(project = null) {
  fillProjectForm(project);
  state.projectFormVisible = true;
  renderProjects();
  requestAnimationFrame(() => {
    $("#projectForm")?.scrollIntoView({ block: "start", behavior: wheelScrollBehavior() });
  });
}

function handleProjectSearchInput(event) {
  state.projectSearch = event.target.value;
  state.projectPickerExpanded = true;
  renderProjects();
}

function clearSelectedProject() {
  state.selectedProjectId = "";
  state.confirmingProjectId = "";
  state.projectFormVisible = false;
  state.projectPickerExpanded = false;
  state.projectSearch = "";
  const searchInput = $("#projectSearchInput");
  if (searchInput) searchInput.value = "";
  fillProjectForm(null);
}

function collapseSelectedProject() {
  const detailCard = $(".project-detail-card");
  const selectedStrip = $(".selected-project-strip");
  const canAnimate = (detailCard || selectedStrip) && !document.body.classList.contains("reduce-motion");
  if (!canAnimate) {
    clearSelectedProject();
    renderProjects();
    return;
  }

  [selectedStrip, detailCard].filter(Boolean).forEach((element) => {
    element.style.willChange = "opacity, transform";
  });
  requestAnimationFrame(() => {
    if (selectedStrip) selectedStrip.classList.add("is-collapsing");
    if (detailCard) detailCard.classList.add("is-collapsing");
  });

  window.setTimeout(() => {
    clearSelectedProject();
    renderProjects();
  }, 320);
}

async function saveProject(event) {
  event.preventDefault();
  const projectId = $("#projectId").value;
  const payload = projectFormPayload();
  if (!compact(payload.name)) {
    showToast("Add a project name first.");
    $("#projectName").focus();
    return;
  }
  const saved = projectId
    ? await api(`/api/projects/${projectId}`, { method: "PUT", body: payload })
    : await api("/api/projects", { method: "POST", body: payload });
  state.selectedProjectId = String(saved.id);
  state.projectPickerExpanded = false;
  state.projectFormVisible = false;
  fillProjectForm(null);
  await loadData();
  openWorkHub("projects");
  showToast("Project saved.");
}

async function suggestProjectSteps(projectId) {
  if (!projectId) return;
  const suggestions = await api(`/api/projects/${projectId}/suggestions`, {
    method: "POST",
    body: {},
  });
  state.projectSuggestions[String(projectId)] = Array.isArray(suggestions) ? suggestions : [];
  setProjectRecommendationsCollapsed(projectId, false);
  renderProjects();
  showToast("Suggestions ready.");
}

async function addSuggestionToPlanner(projectId, index) {
  const project = findProject(projectId);
  const suggestion = (state.projectSuggestions[String(projectId)] || [])[Number(index)];
  if (!project || !suggestion) return;
  await api("/api/tasks", {
    method: "POST",
    body: {
      title: suggestion.title,
      project_id: project.id,
      project: project.name,
      notes: suggestion.guidance || suggestion.notes || "",
      repeat_rule: "none",
    },
  });
  state.projectSuggestions[String(projectId)] = (state.projectSuggestions[String(projectId)] || []).filter((_, itemIndex) => itemIndex !== Number(index));
  await loadData();
  openWorkHub("projects");
  showToast("Added to Planner.");
}

async function addProjectNextStep(event) {
  event.preventDefault();
  const form = event.target.closest(".project-next-step-form");
  if (!form) return;
  const project = findProject(form.dataset.projectId);
  const input = form.querySelector("input");
  const title = compact(input?.value || "");
  if (!project || !title) {
    showToast("Add a next step first.");
    input?.focus();
    return;
  }
  await api("/api/tasks", {
    method: "POST",
    body: {
      title,
      project_id: project.id,
      project: project.name,
      repeat_rule: "none",
    },
  });
  input.value = "";
  await loadData();
  openWorkHub("projects");
  showToast("Added to Planner.");
}

async function reorderProjectTasks(projectId, orderedTaskIds) {
  if (!projectId || !orderedTaskIds.length) return;
  await api(`/api/projects/${projectId}/tasks/reorder`, {
    method: "POST",
    body: { task_ids: orderedTaskIds },
  });
  await loadData();
  openWorkHub("projects");
}

async function moveProjectTask(projectId, taskId, direction) {
  const project = findProject(projectId);
  if (!project) return;
  const tasks = sortProjectTasks(linkedProjectTasks(project).filter((task) => !task.completed));
  const index = tasks.findIndex((task) => String(task.id) === String(taskId));
  if (index < 0) return;
  const targetIndex = direction === "up" ? index - 1 : index + 1;
  if (targetIndex < 0 || targetIndex >= tasks.length) return;
  const reordered = [...tasks];
  [reordered[index], reordered[targetIndex]] = [reordered[targetIndex], reordered[index]];
  await reorderProjectTasks(project.id, reordered.map((task) => String(task.id)));
  showToast("Next steps reordered.");
}

async function completeProject(projectId) {
  if (!projectId) return;
  await api(`/api/projects/${projectId}/complete`, { method: "POST", body: {} });
  state.confirmingProjectId = "";
  state.projectFormVisible = false;
  await loadData();
  openWorkHub("projects");
  showToast("Project completed and saved to Achievements.");
}

function findTask(taskId) {
  return state.tasks.find((task) => String(task.id) === String(taskId));
}

async function toggleTask(taskId) {
  const task = findTask(taskId);
  if (!task) return;
  await api(`/api/tasks/${task.id}`, {
    method: "PUT",
    body: {
      completed: !task.completed,
    },
  });
  await loadData();
}

async function deleteTask(taskId) {
  await api(`/api/tasks/${taskId}`, { method: "DELETE" });
  await loadData();
  showToast("Task deleted.");
}

async function deleteCurrentTask() {
  const taskId = $("#editTaskId").value;
  if (!taskId) return;
  closeDrawer();
  await deleteTask(taskId);
}

async function deleteEntryById(entryId) {
  if (!entryId) return;
  await api(`/api/entries/${entryId}`, { method: "DELETE" });
  await loadData();
  showToast("Entry deleted.");
}

async function logTask(taskId) {
  const task = findTask(taskId);
  if (!task) return;
  await api("/api/quick-logs", {
    method: "POST",
    body: {
      note: `Completed task: ${task.title}`,
      entry_date: today(),
      project_id: task.project_id,
      project: task.project,
      tags: ["task"],
    },
  });
  if (!task.completed) {
    await api(`/api/tasks/${task.id}`, {
      method: "PUT",
      body: {
        ...task,
        completed: true,
      },
    });
  }
  await loadData();
  showToast("Task logged.");
}

function clearEvidenceDraft() {
  $("#evidenceTitle").value = "";
  $("#evidenceType").value = "website";
  $("#evidenceUrl").value = "";
  $("#evidenceDescription").value = "";
}

function inferEvidenceTitle(url, type) {
  const typeLabel = state.options.evidence_types.find((item) => item.value === type)?.label || "Evidence";
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "") || typeLabel;
  } catch {
    return typeLabel;
  }
}

function readEvidenceDraft() {
  const evidenceType = $("#evidenceType").value || "website";
  const url = compact($("#evidenceUrl").value);
  const description = compact($("#evidenceDescription").value);
  const title = compact($("#evidenceTitle").value) || inferEvidenceTitle(url, evidenceType);
  if (!url && evidenceType !== "uploaded_file_placeholder") {
    showToast("Paste an evidence link.");
    $("#evidenceUrl").focus();
    return null;
  }
  return {
    title,
    evidence_type: evidenceType,
    evidence_url: url,
    description,
  };
}

async function addEvidenceToCurrentEntry() {
  const draft = readEvidenceDraft();
  if (!draft) return;

  if (state.selectedEntry?.id) {
    await api("/api/evidence", {
      method: "POST",
      body: {
        ...draft,
        work_entry_id: state.selectedEntry.id,
      },
    });
    await loadData();
    state.selectedEntry = findEntry(state.selectedEntry.id);
    fillEntryForm(state.selectedEntry);
  } else {
    state.pendingEvidence.push({
      ...draft,
      client_id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    });
    clearEvidenceDraft();
    renderEntryEvidenceList();
  }
  showToast("Evidence added.");
}

function renderEntryEvidenceList() {
  const savedEvidence = state.selectedEntry?.id
    ? (Array.isArray(state.evidence) ? state.evidence : []).filter((item) => item.work_entry_id === state.selectedEntry.id)
    : [];
  const pending = state.pendingEvidence.map((item) => ({
    ...item,
    id: item.client_id,
    evidence_type_label: state.options.evidence_types.find((type) => type.value === item.evidence_type)?.label || item.evidence_type,
    pending: true,
  }));
  const items = [...savedEvidence, ...pending];
  const list = $("#entryEvidenceList");
  if (items.length === 0) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = items
    .map((item) => {
      return `
        <div class="mini-item" data-evidence-id="${escapeHtml(item.id)}" data-pending="${item.pending ? "true" : "false"}">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <span>${escapeHtml(item.evidence_type_label || item.evidence_type)}</span>
            ${item.description ? `<small>${escapeHtml(item.description)}</small>` : ""}
          </div>
          <button class="ghost-button danger" type="button" data-action="${item.pending ? "remove-pending-evidence" : "delete-entry-evidence"}">Remove</button>
        </div>
      `;
    })
    .join("");
}

function entryIdFromEvent(event) {
  return event.target.closest("[data-entry-id]")?.dataset.entryId;
}

function taskIdFromEvent(event) {
  return event.target.closest("[data-task-id]")?.dataset.taskId;
}

async function handleTaskListAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;

  if (button.dataset.action === "toggle-hidden-upcoming") {
    toggleHiddenUpcoming();
    return;
  }

  if (button.dataset.action === "toggle-task-list-expanded") {
    toggleTaskListExpanded();
    return;
  }

  if (button.dataset.action === "set-planner-focus") {
    setPlannerFocus(button.dataset.focus || "today");
    return;
  }

  if (button.dataset.action === "view-task-bucket") {
    openTaskBucket(button.dataset.bucket || "inbox");
    return;
  }

  const taskId = taskIdFromEvent(event);
  if (!taskId) return;

  if (button.dataset.action === "edit-task") {
    const task = findTask(taskId);
    if (task) openTaskEditor(task);
  }
  if (button.dataset.action === "toggle-task") {
    await toggleTask(taskId);
  }
  if (button.dataset.action === "delete-task") {
    await deleteTask(taskId);
  }
  if (button.dataset.action === "hide-upcoming-task") {
    hideUpcomingTask(taskId);
  }
  if (button.dataset.action === "unhide-upcoming-task") {
    unhideUpcomingTask(taskId);
  }
}

async function handleProjectAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    const taskRow = event.target.closest(".project-mini-item[data-task-id]");
    if (taskRow) {
      const task = findTask(taskRow.dataset.taskId);
      if (task) openTaskEditor(task);
    }
    return;
  }
  const projectId = event.target.closest("[data-project-id]")?.dataset.projectId || state.selectedProjectId;

  if (button.dataset.action === "select-project") {
    state.selectedProjectId = projectId;
    state.projectPickerExpanded = false;
    state.confirmingProjectId = "";
    state.projectFormVisible = false;
    state.projectSearch = "";
    const searchInput = $("#projectSearchInput");
    if (searchInput) searchInput.value = "";
    renderProjects();
    window.setTimeout(() => {
      fastScrollToElement(".selected-project-strip", { duration: 280 });
    }, 80);
    return;
  }
  if (button.dataset.action === "deselect-project") {
    collapseSelectedProject();
    return;
  }
  if (button.dataset.action === "edit-project") {
    const project = findProject(projectId);
    if (project) {
      showProjectForm(project);
    }
    return;
  }
  if (button.dataset.action === "suggest-project") {
    await suggestProjectSteps(projectId);
    return;
  }
  if (button.dataset.action === "hide-project-suggestions") {
    setProjectRecommendationsCollapsed(projectId, true);
    return;
  }
  if (button.dataset.action === "show-project-suggestions") {
    setProjectRecommendationsCollapsed(projectId, false);
    return;
  }
  if (button.dataset.action === "add-suggestion-task") {
    await addSuggestionToPlanner(projectId, button.dataset.index);
    return;
  }
  if (button.dataset.action === "move-project-task") {
    await moveProjectTask(projectId, taskIdFromEvent(event), button.dataset.direction);
    return;
  }
  if (button.dataset.action === "start-complete-project") {
    state.confirmingProjectId = projectId;
    renderProjects();
    requestAnimationFrame(() => {
      $(".project-complete-sheet")?.scrollIntoView({ block: "nearest", behavior: wheelScrollBehavior() });
    });
    return;
  }
  if (button.dataset.action === "cancel-complete-project") {
    state.confirmingProjectId = "";
    renderProjects();
    return;
  }
  if (button.dataset.action === "complete-project-now") {
    await completeProject(projectId);
    return;
  }
  if (button.dataset.action === "edit-task") {
    const task = findTask(taskIdFromEvent(event));
    if (task) openTaskEditor(task);
    return;
  }
  if (button.dataset.action === "edit-entry") {
    const entry = findEntry(entryIdFromEvent(event));
    if (entry) openDrawer(entry, "details");
  }
}

async function deleteEvidence(evidenceId) {
  await api(`/api/evidence/${evidenceId}`, { method: "DELETE" });
  await loadData();
  if (state.selectedEntry) {
    state.selectedEntry = findEntry(state.selectedEntry.id);
    renderEntryEvidenceList();
  }
  showToast("Evidence removed.");
}

function openCalendar() {
  const drawer = $("#calendarDrawer");
  if (!drawer) return;
  state.calendarSelectedDate = state.calendarSelectedDate || today();
  state.calendarMonth = state.calendarSelectedDate.slice(0, 7);
  lockPageScroll();
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  renderCalendar();
}

function closeCalendar() {
  const drawer = $("#calendarDrawer");
  if (drawer) {
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }
  unlockPageScrollIfClear();
}

function openSettings() {
  const drawer = $("#settingsDrawer");
  if (!drawer) return;
  updateSettingsForm();
  refreshPushStatus();
  refreshGoogleIntegrationStatus();
  lockPageScroll();
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
}

function closeSettings() {
  const drawer = $("#settingsDrawer");
  if (drawer) {
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }
  unlockPageScrollIfClear();
}

function updateSettingsFromForm() {
  saveSettings({
    density: $("#settingDensity").value,
    accentColor: $("#settingAccentColor").value,
    defaultView: $("#settingDefaultView").value,
    clockFormat: "24",
    showTaskDetails: $("#settingShowTaskDetails").checked,
    reducedMotion: $("#settingReducedMotion").checked,
  });
}

function resetSettings() {
  saveSettings({
    density: "comfortable",
    accentColor: "#5DD4C0",
    showTaskDetails: true,
    defaultView: "dashboard",
    clockFormat: "24",
    reducedMotion: false,
  });
  showToast("Settings reset.");
}

function resetAccentColor() {
  saveSettings({ accentColor: "#5DD4C0" });
  showToast("Accent reset.");
}

function requestedInitialView() {
  const requested = new URLSearchParams(window.location.search).get("view");
  if (requested === "diary" || requested === "cv") {
    state.workHubTab = "cv";
    return "workhub";
  }
  if (requested === "achievements") {
    state.workHubTab = "achievements";
    return "workhub";
  }
  return ["dashboard", "planner", "workhub"].includes(requested) ? requested : "";
}

function handleCalendarAgendaAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  closeCalendar();
  if (button.dataset.action === "calendar-open-task") {
    const task = findTask(id);
    if (task) {
      openTaskEditor(task);
    } else {
      switchView("planner");
    }
    return;
  }
  if (button.dataset.action === "calendar-open-entry" || button.dataset.action === "calendar-open-evidence") {
    const entry = findEntry(id);
    if (entry) {
      openDrawer(entry, "details");
    } else {
      openWorkHub("cv");
    }
    return;
  }
  if (button.dataset.action === "calendar-open-achievement") {
    openWorkHub("achievements");
  }
}

function bindEvents() {
  on("#floatingQuickAdd", "click", openPrimaryAdd);
  on("#openCalendar", "click", openCalendar);
  on("#openSettings", "click", openSettings);
  on("#logoutButton", "click", async () => {
    await api("/api/logout", { method: "POST", body: {} });
    localStorage.removeItem("workDiaryToken");
    window.location.assign("/login.html");
  });
  on("#inlineQuickForm", "submit", saveQuickLog);
  on("#projectForm", "submit", saveProject);
  on("#clearProjectForm", "click", clearProjectForm);
  on("#projectSearchInput", "input", handleProjectSearchInput);
  on("#newProjectButton", "click", () => showProjectForm(null));
  on("#taskForm", "submit", saveTask);
  on("#clearTaskForm", "click", () => clearTaskComposer({ toast: true }));
  on("#toggleTaskAdvanced", "click", toggleTaskComposerAdvanced);
  on("#viewIncompleteTasks", "click", () => openTaskBucket("incomplete"));
  on("#taskRepeatRule", "change", () => updateRepeatControls("create"));
  $$("[data-date-target]").forEach((button) => {
    button.addEventListener("click", () => openDatePicker(
      button.dataset.dateTarget,
      button.getAttribute("aria-label") || "Set date",
    ));
  });
  $$("[data-time-target]").forEach((button) => {
    button.addEventListener("click", () => openTimePicker(
      button.dataset.timeTarget,
      button.getAttribute("aria-label") || "Set time",
    ));
  });
  on("#drawerPhotoForm", "submit", saveImageEvidence);
  on("#taskEditForm", "submit", saveTaskEdits);
  on("#editTaskRepeatRule", "change", () => updateRepeatControls("edit"));
  on("#deleteTaskFromEditor", "click", deleteCurrentTask);
  on("#drawerPhotoCamera", "change", handlePhotoSelection);
  on("#drawerPhotoFile", "change", handlePhotoSelection);
  on("#entryForm", "submit", saveEntry);
  on("#addEvidenceToEntry", "click", addEvidenceToCurrentEntry);
  on("#backToPlanner", "click", () => switchView("planner"));
  on("[data-action='open-achievements']", "click", () => openWorkHub("achievements"));

  on("#overviewCards", "click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    if (button.dataset.action === "overview-open") {
      openTaskBucket("incomplete");
    }
    if (button.dataset.action === "overview-completed") {
      openTaskBucket("complete");
    }
  });

  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $$(".workhub-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.workHubTab = button.dataset.workhubTab || "projects";
      renderWorkHub();
    });
  });
  [
    ["taskProjectId", "taskProject"],
    ["editTaskProjectId", "editTaskProject"],
    ["entryProjectId", "entryProject"],
    ["inlineQuickProjectId", "inlineQuickProject"],
    ["drawerPhotoProjectId", "drawerPhotoProject"],
  ].forEach(([selectId, hiddenId]) => {
    const select = $(`#${selectId}`);
    if (select) {
      select.addEventListener("change", () => syncProjectHidden(selectId, hiddenId));
    }
  });
  $$("[data-action='close-drawer']").forEach((button) => {
    button.addEventListener("click", closeDrawer);
  });
  $$("[data-action='close-calendar']").forEach((button) => {
    button.addEventListener("click", closeCalendar);
  });
  $$("[data-action='close-settings']").forEach((button) => {
    button.addEventListener("click", closeSettings);
  });
  $$("[data-action='close-time']").forEach((button) => {
    button.addEventListener("click", closeTimePicker);
  });
  $$("[data-action='close-date']").forEach((button) => {
    button.addEventListener("click", closeDatePicker);
  });
  on("#timeWheel", "click", (event) => {
    const button = event.target.closest("button[data-wheel-part]");
    if (!button) return;
    handleTimeWheel(button.dataset.wheelPart, button.dataset.value);
  });
  on("#dateWheel", "click", (event) => {
    const button = event.target.closest("button[data-wheel-part]");
    if (!button) return;
    handleDateWheel(button.dataset.wheelPart, button.dataset.value);
  });
  on("#setTime", "click", setSelectedTime);
  on("#clearTime", "click", clearSelectedTime);
  on("#setDate", "click", setSelectedDate);
  on("#clearDate", "click", clearSelectedDate);

  on("#calendarPrev", "click", () => {
    state.calendarMonth = shiftMonth(state.calendarMonth, -1);
    renderCalendar();
  });
  on("#calendarNext", "click", () => {
    state.calendarMonth = shiftMonth(state.calendarMonth, 1);
    renderCalendar();
  });
  on("#calendarToday", "click", () => {
    state.calendarSelectedDate = today();
    state.calendarMonth = today().slice(0, 7);
    renderCalendar();
  });
  on("#calendarTomorrow", "click", () => {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    const value = dateInputValue(date);
    state.calendarSelectedDate = value;
    state.calendarMonth = value.slice(0, 7);
    renderCalendar();
  });
  on("#calendarGrid", "click", (event) => {
    const button = event.target.closest("button[data-date]");
    if (!button) return;
    state.calendarSelectedDate = button.dataset.date;
    state.calendarMonth = button.dataset.date.slice(0, 7);
    renderCalendar();
  });
  on("#calendarAgenda", "click", handleCalendarAgendaAction);

  on("#settingsForm", "input", updateSettingsFromForm);
  on("#settingsForm", "change", updateSettingsFromForm);
  on("#resetSettings", "click", resetSettings);
  on("#resetAccentColor", "click", resetAccentColor);
  on("#phoneReminderToggle", "change", handlePhoneReminderToggle);
  on("#sendTestReminder", "click", sendTestReminder);
  on("#connectGoogle", "click", connectGoogleIntegration);
  on("#disconnectGoogle", "click", disconnectGoogleIntegration);
  on("#retryGoogleSync", "click", retryGoogleIntegrationSync);
  on("#restoreHiddenProgress", "click", restoreHiddenDashboardTasks);

  on("#searchInput", "input", renderEntries);
  on("#achievementSearchInput", "input", renderAchievements);
  on("#dashboardAchievementSearchInput", "input", renderDashboardAchievementSearch);

  on("#taskList", "click", handleTaskListAction);
  on("#taskDetailList", "click", handleTaskListAction);
  on("#projectList", "click", handleProjectAction);
  on("#projectDetail", "click", handleProjectAction);
  on("#projectDetail", "submit", addProjectNextStep);

  on("#entryList", "click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    if (button.dataset.action === "toggle-hidden-entries") {
      toggleHiddenEntries();
      return;
    }
    const entryId = entryIdFromEvent(event);
    const entry = findEntry(entryId);
    if (!entry) return;

    if (button.dataset.action === "hide-entry") {
      hideEntry(entry.id);
    }
    if (button.dataset.action === "unhide-entry") {
      removeHiddenId(HIDDEN_ENTRIES_KEY, "hiddenEntryIds", entry.id);
      renderEntries();
      showToast("CV note restored.");
    }
    if (button.dataset.action === "edit-entry") {
      openDrawer(entry, "details");
    }
    if (button.dataset.action === "delete-entry") {
      await deleteEntryById(entry.id);
    }
  });

  const openSourceEntry = (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const entryId = entryIdFromEvent(event);
    const taskId = taskIdFromEvent(event);
    if (button.dataset.action === "edit-entry") {
      const entry = findEntry(entryId);
      if (entry) openDrawer(entry, "details");
    }
    if (button.dataset.action === "edit-task") {
      const task = findTask(taskId);
      if (task) openTaskEditor(task);
    }
    if (button.dataset.action === "hide-dashboard-task") {
      hideDashboardTask(taskId);
    }
    if (button.dataset.action === "hide-dashboard-entry") {
      hideDashboardEntry(entryId);
    }
  };
  on("#achievementList", "click", openSourceEntry);
  on("#dashboardRecent", "click", openSourceEntry);
  on("#dashboardAchievementSearchResults", "click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (button?.dataset.action === "open-achievements-search") {
      const search = $("#dashboardAchievementSearchInput")?.value || "";
      const achievementSearch = $("#achievementSearchInput");
      if (achievementSearch) achievementSearch.value = search;
      openWorkHub("achievements");
      renderAchievements();
      return;
    }
    openSourceEntry(event);
  });

  on("#entryEvidenceList", "click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = event.target.closest("[data-evidence-id]");
    if (!item) return;
    if (button.dataset.action === "remove-pending-evidence") {
      state.pendingEvidence = state.pendingEvidence.filter((evidence) => evidence.client_id !== item.dataset.evidenceId);
      renderEntryEvidenceList();
    }
    if (button.dataset.action === "delete-entry-evidence") {
      await deleteEvidence(item.dataset.evidenceId);
    }
  });

  on("#deleteEntry", "click", async () => {
    const id = $("#entryId").value;
    if (!id) return;
    closeDrawer();
    await deleteEntryById(id);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const entryDrawer = $("#entryDrawer");
    const calendarDrawer = $("#calendarDrawer");
    const settingsDrawer = $("#settingsDrawer");
    const timeDrawer = $("#timeDrawer");
    const dateDrawer = $("#dateDrawer");
    if (dateDrawer && !dateDrawer.classList.contains("hidden")) {
      event.preventDefault();
      closeDatePicker();
      return;
    }
    if (timeDrawer && !timeDrawer.classList.contains("hidden")) {
      event.preventDefault();
      closeTimePicker();
      return;
    }
    if (entryDrawer && !entryDrawer.classList.contains("hidden")) {
      closeDrawer();
      return;
    }
    if (calendarDrawer && !calendarDrawer.classList.contains("hidden")) {
      closeCalendar();
      return;
    }
    if (settingsDrawer && !settingsDrawer.classList.contains("hidden")) {
      closeSettings();
    }
  });
}

async function init() {
  loadSettings();
  loadPlannerFocus();
  loadHiddenUiState();
  loadProjectUiState();
  applySettings();
  updateSettingsForm();
  updateTaskComposerAdvanced();
  updateRepeatControls("create");
  updateDateButtons();
  updateTimeButtons();
  const quickDate = $("#inlineQuickDate");
  const entryDate = $("#entryDate");
  if (quickDate) quickDate.value = today();
  if (entryDate) entryDate.value = today();
  registerServiceWorker();
  refreshPushStatus();
  window.addEventListener("unhandledrejection", (event) => {
    showToast(event.reason?.message || "Something went wrong.");
  });
  bindEvents();

  try {
    await loadData();
    const initialView = requestedInitialView() || state.settings.defaultView;
    if (initialView !== "dashboard") {
      switchView(initialView);
    }
  } catch (error) {
    showToast(error.message);
  }
}

init();
