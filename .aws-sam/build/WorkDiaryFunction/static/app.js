const state = {
  activeView: "dashboard",
  taskBucket: "inbox",
  tasks: [],
  entries: [],
  evidence: [],
  achievements: [],
  hiddenDashboardTaskIds: [],
  hiddenDashboardEntryIds: [],
  hiddenUpcomingTaskIds: [],
  hiddenEntryIds: [],
  showHiddenUpcoming: false,
  showHiddenEntries: false,
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
    reducedMotion: false,
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const API_BASE_URL = window.API_BASE_URL || "";
const SETTINGS_KEY = "workDiarySettings";
const HIDDEN_DASHBOARD_TASKS_KEY = "workDiaryHiddenDashboardTasks";
const HIDDEN_DASHBOARD_ENTRIES_KEY = "workDiaryHiddenDashboardEntries";
const HIDDEN_UPCOMING_TASKS_KEY = "workDiaryHiddenUpcomingTasks";
const HIDDEN_ENTRIES_KEY = "workDiaryHiddenEntries";

async function clearLegacyServiceWorkers() {
  if (!("serviceWorker" in navigator)) return;
  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
  } catch {
    // Ignore failures; the app is still usable without offline caching.
  }
  if (window.caches) {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch {
      // Ignore failures.
    }
  }
}

function today() {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 10);
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
  return new Date(`2000-01-01T${hour}:${minute}:00`).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
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
  const date = new Date();
  date.setDate(date.getDate() - days);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
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
  const response = await fetch(`${API_BASE_URL}${path}`, request);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
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

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => {
    toast.classList.add("hidden");
  }, 2600);
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    state.settings = {
      ...state.settings,
      ...saved,
      accentColor: saved.accentColor || state.settings.accentColor,
      showTaskDetails: saved.showTaskDetails !== false,
      defaultView: ["dashboard", "planner", "diary", "achievements"].includes(saved.defaultView)
        ? saved.defaultView
        : state.settings.defaultView,
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
  document.body.classList.toggle("hide-task-details", !settings.showTaskDetails);
  document.body.classList.toggle("reduce-motion", Boolean(settings.reducedMotion));
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
  $("#settingDensity").value = state.settings.density;
  $("#settingAccentColor").value = state.settings.accentColor;
  $("#settingDefaultView").value = state.settings.defaultView;
  $("#settingShowTaskDetails").checked = state.settings.showTaskDetails;
  $("#settingReducedMotion").checked = state.settings.reducedMotion;
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
    const [tasks, entries, evidence, options, achievements] = await Promise.all([
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
    ]);
    bootstrap = { tasks, entries, evidence, options, achievements };
  }
  const tasks = bootstrap?.tasks;
  const entries = bootstrap?.entries;
  const evidence = bootstrap?.evidence;
  const achievements = bootstrap?.achievements;
  const options = bootstrap?.options;
  state.tasks = Array.isArray(tasks) ? tasks : [];
  state.entries = Array.isArray(entries) ? entries : Array.isArray(entries?.entries) ? entries.entries : [];
  state.evidence = Array.isArray(evidence) ? evidence : Array.isArray(evidence?.evidence) ? evidence.evidence : [];
  state.achievements = Array.isArray(achievements)
    ? achievements
    : Array.isArray(achievements?.achievements)
      ? achievements.achievements
      : [];
  state.options = {
    projects: Array.isArray(options?.projects) ? options.projects : [],
    skills: Array.isArray(options?.skills) ? options.skills : [],
    tags: Array.isArray(options?.tags) ? options.tags : [],
    evidence_types: Array.isArray(options?.evidence_types) ? options.evidence_types : [],
  };
  populateControls();
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

function populateControls() {
  setSelectOptions($("#evidenceType"), "Website link", state.options.evidence_types, $("#evidenceType").value || "website");
}

function switchView(view) {
  state.activeView = view;
  const tabView = view === "taskDetail" ? "planner" : view;
  $$(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === tabView);
  });
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `${view}View`);
  });
  render();
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

function taskMeta(task) {
  const meta = [];
  if (task.project) {
    meta.push(task.project);
  }
  if (task.due_date) {
    meta.push(`Due ${formatDate(task.due_date)}${task.due_time ? ` ${formatTime(task.due_time)}` : ""}`);
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

function taskPriorityLabel(priority) {
  if (priority === "low") return "Low";
  if (priority === "medium") return "Medium";
  if (priority === "high") return "High";
  return "";
}

function taskSortValue(task) {
  const date = task.due_date || "9999-12-31";
  const time = task.due_time || "23:59";
  return `${date}T${time}:${String(task.id).padStart(8, "0")}`;
}

function sortTasks(tasks) {
  return [...tasks].sort((a, b) => taskSortValue(a).localeCompare(taskSortValue(b)));
}

function taskBuckets() {
  const tasks = Array.isArray(state.tasks) ? state.tasks : [];
  const openTasks = tasks.filter((task) => !task.completed);
  const completedTasks = tasks.filter((task) => task.completed);
  const inboxTasks = openTasks.filter((task) => !task.due_date);
  const todayTasks = openTasks.filter((task) => task.due_date === today());
  const upcomingTasks = openTasks.filter((task) => task.due_date && task.due_date !== today());
  return {
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

function completedSortValue(task) {
  return task.completed_at || task.updated_at || task.created_at || "";
}

function sortCompletedTasks(tasks) {
  return [...tasks].sort((a, b) => completedSortValue(b).localeCompare(completedSortValue(a)));
}

function completedTaskAchievement(task) {
  return {
    kind: "task",
    id: `task-${task.id}`,
    taskId: task.id,
    date: completedDate(task) || today(),
    title: `Completed task: ${task.title}`,
    meta: [task.project, task.priority ? `${taskPriorityLabel(task.priority)} priority` : ""].filter(Boolean).join(" / "),
    searchText: [task.title, task.project, task.notes, task.priority, "completed task"].join(" "),
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
  const completed = (Array.isArray(state.tasks) ? state.tasks : [])
    .filter((task) => task.completed)
    .map(completedTaskAchievement);
  return [...achievements, ...completed].sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

function taskBucketTitle(bucket) {
  if (bucket === "today") return "Today";
  if (bucket === "upcoming") return "Upcoming";
  return "Inbox";
}

function taskBucketCountLabel(bucket, count) {
  if (bucket === "today") return `${count} today`;
  if (bucket === "upcoming") return `${count} dated`;
  return `${count} undated`;
}

function renderTaskCard(task, options = {}) {
  const expanded = Boolean(options.expanded);
  const hideUpcomingButton = options.allowHideUpcoming
    ? `<button class="link-button subtle" type="button" data-action="hide-upcoming-task">Hide</button>`
    : "";
  const meta = taskMeta(task)
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join("");
  const priorityLabel = taskPriorityLabel(task.priority);
  return `
    <article class="task-item ${task.completed ? "completed" : ""}" data-task-id="${task.id}">
      <button class="task-checkbox ${task.completed ? "checked" : ""}" type="button" data-action="toggle-task" aria-label="${task.completed ? "Mark task open" : "Mark task done"}">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M20 6 9 17l-5-5"></path>
        </svg>
      </button>
      <div class="task-main">
        <div class="task-title-row">
          <div class="task-title">${escapeHtml(task.title)}</div>
          ${priorityLabel ? `<span class="priority-chip priority-${escapeHtml(task.priority)}">${escapeHtml(priorityLabel)}</span>` : ""}
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

function renderTaskColumn(bucket, tasks) {
  const title = taskBucketTitle(bucket);
  const hiddenUpcomingIds = new Set(state.hiddenUpcomingTaskIds.map(String));
  const hiddenCount = bucket === "upcoming" ? tasks.filter((task) => hiddenUpcomingIds.has(String(task.id))).length : 0;
  const visibleTasks = bucket === "upcoming" && !state.showHiddenUpcoming
    ? tasks.filter((task) => !hiddenUpcomingIds.has(String(task.id)))
    : tasks;
  const countLabel = taskBucketCountLabel(bucket, visibleTasks.length);
  const preview = visibleTasks.slice(0, 2);
  const showHiddenButton = bucket === "upcoming" && hiddenCount > 0
    ? `<button class="planner-box-link" type="button" data-action="toggle-hidden-upcoming">${state.showHiddenUpcoming ? "Hide hidden" : "Show hidden"}</button>`
    : "";
  return `
    <section class="planner-box" aria-label="${escapeHtml(title)}">
      <header class="planner-box-head">
        <h3>${escapeHtml(title)}</h3>
        <div class="planner-box-head-actions">
          <span>${escapeHtml(countLabel)}</span>
          ${showHiddenButton}
          <button class="planner-box-link" type="button" data-action="view-task-bucket" data-bucket="${escapeHtml(bucket)}">View all</button>
        </div>
      </header>
      <div class="planner-box-list">
        ${
          preview.length
            ? preview.map((task) => renderTaskCard(task, { allowHideUpcoming: bucket === "upcoming" })).join("")
            : `<div class="planner-empty">No tasks here.</div>`
        }
        ${visibleTasks.length > preview.length ? `<button class="ghost-button" type="button" data-action="view-task-bucket" data-bucket="${escapeHtml(bucket)}">Show ${visibleTasks.length - preview.length} more</button>` : ""}
      </div>
    </section>
  `;
}

function renderTasks() {
  const buckets = taskBuckets();
  const list = $("#taskList");

  $("#todoCount").textContent = `${buckets.openTasks.length} open`;
  list.innerHTML = [
    renderTaskColumn("inbox", buckets.inbox),
    renderTaskColumn("today", buckets.today),
    renderTaskColumn("upcoming", buckets.upcoming),
  ].join("");
}

function renderOverview() {
  const buckets = taskBuckets();
  const weekStart = dateDaysAgo(6);
  const entries = Array.isArray(state.entries) ? state.entries : [];
  const achievements = Array.isArray(state.achievements) ? state.achievements : [];
  const recentEntries = entries.filter((entry) => entry.entry_date >= weekStart);
  const recentAchievements = achievements.filter((item) => item.achieved_at >= weekStart);
  const recentCompleted = buckets.completedTasks.filter((task) => completedDate(task) >= weekStart);
  const nextTask = sortTasks(buckets.openTasks)[0];
  const nextTaskBucket = nextTask?.due_date === today() ? "today" : nextTask?.due_date ? "upcoming" : "inbox";
  const nextTaskLabel = nextTask
    ? nextTask.due_date
      ? `${formatDate(nextTask.due_date)}${nextTask.due_time ? ` ${formatTime(nextTask.due_time)}` : ""}`
      : "Inbox"
    : "Clear";

  const cards = [
    {
      action: "overview-open",
      label: "Open tasks",
      value: buckets.openTasks.length,
      detail: buckets.openTasks.length === 1 ? "task active" : "tasks active",
    },
    {
      action: "overview-today",
      label: "Due today",
      value: buckets.today.length,
      detail: buckets.today.length === 1 ? "task due" : "tasks due",
    },
    {
      action: "overview-completed",
      label: "Completed",
      value: recentCompleted.length,
      detail: recentCompleted.length === 1 ? "task this week" : "tasks this week",
    },
    {
      action: "overview-diary",
      label: "CV",
      value: recentEntries.length,
      detail: recentEntries.length === 1 ? "note this week" : "notes this week",
    },
    {
      action: "overview-achievements",
      label: "Achievements",
      value: recentAchievements.length,
      detail: recentAchievements.length === 1 ? "bullet this week" : "bullets this week",
    },
    {
      action: "overview-next",
      label: "Recent progress",
      value: nextTask ? nextTask.title : "Clear",
      detail: nextTask ? nextTaskLabel : "No open task",
      bucket: nextTaskBucket,
      wide: true,
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
  $("#taskDetailCount").textContent = taskBucketCountLabel(state.taskBucket, tasks.length);
  $("#taskDetailList").innerHTML = tasks.length
    ? tasks.map((task) => renderTaskCard(task, { expanded: true, allowHideUpcoming: state.taskBucket === "upcoming" })).join("")
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

function achievementMatches(item, search) {
  if (!search) return true;
  const text = String(item.searchText || "").toLowerCase();
  return text.includes(search);
}

function renderAchievements() {
  const search = compact($("#achievementSearchInput").value).toLowerCase();
  const achievements = progressRows().filter((item) =>
    achievementMatches(item, search)
  );
  const list = $("#achievementList");
  if (achievements.length === 0) {
    list.innerHTML = `<div class="empty-state">No achievements found. Complete a task or save a CV note to build your career log.</div>`;
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
  const tasks = (Array.isArray(state.tasks) ? state.tasks : []).filter((task) => task.due_date === date);
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
      type: task.completed ? "Done task" : "Task",
      meta: [task.project, task.due_time ? formatTime(task.due_time) : ""].filter(Boolean).join(" / "),
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
  renderEntries();
  renderAchievements();
  renderCalendar();
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
  $("#entryDrawer").classList.add("hidden");
  $("#entryDrawer").setAttribute("aria-hidden", "true");
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
  $("#entryProject").value = entry?.project || "";
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
  return {
    entry_date: $("#entryDate").value,
    title: $("#entryTitle").value,
    what_i_did: $("#entryWhat").value,
    project: $("#entryProject").value,
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
  $("#editTaskProject").value = task?.project || "";
  $("#editTaskDueDate").value = task?.due_date || "";
  $("#editTaskDueTime").value = task?.due_time || "";
  $("#editTaskReminder").value = task?.reminder_at || "";
  $("#editTaskRepeatRule").value = repeatRule;
  $("#editTaskRepeatIntervalDays").value = Number.isFinite(repeatDays) && repeatDays > 0 ? repeatDays : 1;
  $("#editTaskRepeatUntil").value = task?.repeat_until || "";
  $("#editTaskPriority").value = task?.priority || "";
  $("#editTaskLocation").value = task?.location || "";
  $("#editTaskNotes").value = task?.notes || "";
  $("#editTaskCompleted").checked = Boolean(task?.completed);
  updateRepeatControls("edit");
}

function taskEditPayload() {
  const repeatRule = $("#editTaskRepeatRule").value;
  return {
    ...state.selectedTask,
    title: $("#editTaskTitle").value,
    project: $("#editTaskProject").value,
    due_date: $("#editTaskDueDate").value,
    due_time: $("#editTaskDueTime").value,
    reminder_at: $("#editTaskReminder").value,
    repeat_rule: repeatRule,
    repeat_interval_days: repeatRule === "none" ? "" : $("#editTaskRepeatIntervalDays").value,
    repeat_until: repeatRule === "none" ? "" : $("#editTaskRepeatUntil").value,
    priority: $("#editTaskPriority").value,
    location: $("#editTaskLocation").value,
    notes: $("#editTaskNotes").value,
    completed: $("#editTaskCompleted").checked,
  };
}

function updateRepeatControls(prefix) {
  const isEdit = prefix === "edit";
  const rule = $(isEdit ? "#editTaskRepeatRule" : "#taskRepeatRule").value;
  const show = rule !== "none";
  $(isEdit ? "#editTaskRepeatIntervalWrap" : "#taskRepeatIntervalWrap").classList.toggle("hidden", !show);
  $(isEdit ? "#editTaskRepeatUntilWrap" : "#taskRepeatUntilWrap").classList.toggle("hidden", !show);
}

function lockPageScroll() {
  if (document.body.classList.contains("modal-open")) return;
  state.lockedScrollY = window.scrollY || 0;
  document.body.style.top = `-${state.lockedScrollY}px`;
  document.body.classList.add("modal-open");
}

function unlockPageScrollIfClear() {
  const overlayOpen = ["#entryDrawer", "#calendarDrawer", "#settingsDrawer"].some((selector) => {
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
  $("#drawerPhotoProject").value = entry?.project || "";
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
  await api("/api/image-evidence", {
    method: "POST",
    body: {
      ...image,
      work_entry_id: state.selectedEntry?.id || "",
      entry_date: $("#drawerPhotoDate").value || today(),
      project: $("#drawerPhotoProject").value,
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

  await api(`/api/tasks/${taskId}`, {
    method: "PUT",
    body: taskEditPayload(),
  });
  closeDrawer();
  await loadData();
  showToast("Task updated.");
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
      project: $("#inlineQuickProject").value,
    },
  });
  noteInput.value = "";
  $("#inlineQuickProject").value = "";
  await loadData();
  showToast("Journal saved.");
}

async function saveTask(event) {
  event.preventDefault();
  const titleInput = $("#taskTitle");
  const title = compact(titleInput.value);
  const repeatRule = $("#taskRepeatRule").value;
  if (!title) {
    showToast("Add a task first.");
    titleInput.focus();
    return;
  }

  await api("/api/tasks", {
    method: "POST",
    body: {
      title,
      due_date: $("#taskDueDate").value,
      due_time: $("#taskDueTime").value,
      priority: $("#taskPriority").value,
      repeat_rule: repeatRule,
      repeat_interval_days: repeatRule === "none" ? "" : $("#taskRepeatIntervalDays").value,
      repeat_until: repeatRule === "none" ? "" : $("#taskRepeatUntil").value,
    },
  });
  $("#taskTitle").value = "";
  $("#taskDueDate").value = "";
  $("#taskDueTime").value = "";
  $("#taskPriority").value = "";
  $("#taskRepeatRule").value = "none";
  $("#taskRepeatIntervalDays").value = "1";
  $("#taskRepeatUntil").value = "";
  updateRepeatControls("create");
  await loadData();
  showToast("Task added.");
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
      ...task,
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
  if (button.dataset.action === "log-task") {
    await logTask(taskId);
    switchView("diary");
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
  state.calendarSelectedDate = state.calendarSelectedDate || today();
  state.calendarMonth = state.calendarSelectedDate.slice(0, 7);
  lockPageScroll();
  $("#calendarDrawer").classList.remove("hidden");
  $("#calendarDrawer").setAttribute("aria-hidden", "false");
  renderCalendar();
}

function closeCalendar() {
  $("#calendarDrawer").classList.add("hidden");
  $("#calendarDrawer").setAttribute("aria-hidden", "true");
  unlockPageScrollIfClear();
}

function openSettings() {
  updateSettingsForm();
  lockPageScroll();
  $("#settingsDrawer").classList.remove("hidden");
  $("#settingsDrawer").setAttribute("aria-hidden", "false");
}

function closeSettings() {
  $("#settingsDrawer").classList.add("hidden");
  $("#settingsDrawer").setAttribute("aria-hidden", "true");
  unlockPageScrollIfClear();
}

function updateSettingsFromForm() {
  saveSettings({
    density: $("#settingDensity").value,
    accentColor: $("#settingAccentColor").value,
    defaultView: $("#settingDefaultView").value,
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
    reducedMotion: false,
  });
  showToast("Settings reset.");
}

function resetAccentColor() {
  saveSettings({ accentColor: "#5DD4C0" });
  showToast("Accent reset.");
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
      switchView("diary");
    }
    return;
  }
  if (button.dataset.action === "calendar-open-achievement") {
    switchView("achievements");
  }
}

function bindEvents() {
  $("#floatingQuickAdd").addEventListener("click", openPrimaryAdd);
  $("#openCalendar").addEventListener("click", openCalendar);
  $("#openSettings").addEventListener("click", openSettings);
  $("#logoutButton").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} });
    localStorage.removeItem("workDiaryToken");
    window.location.assign("/login.html");
  });
  $("#inlineQuickForm").addEventListener("submit", saveQuickLog);
  $("#taskForm").addEventListener("submit", saveTask);
  $("#taskRepeatRule").addEventListener("change", () => updateRepeatControls("create"));
  $("#drawerPhotoForm").addEventListener("submit", saveImageEvidence);
  $("#taskEditForm").addEventListener("submit", saveTaskEdits);
  $("#editTaskRepeatRule").addEventListener("change", () => updateRepeatControls("edit"));
  $("#deleteTaskFromEditor").addEventListener("click", deleteCurrentTask);
  $("#drawerPhotoCamera").addEventListener("change", handlePhotoSelection);
  $("#drawerPhotoFile").addEventListener("change", handlePhotoSelection);
  $("#entryForm").addEventListener("submit", saveEntry);
  $("#addEvidenceToEntry").addEventListener("click", addEvidenceToCurrentEntry);
  $("#backToPlanner").addEventListener("click", () => switchView("planner"));
  $("[data-action='open-achievements']").addEventListener("click", () => switchView("achievements"));

  $("#overviewCards").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    if (button.dataset.action === "overview-open") {
      switchView("planner");
    }
    if (button.dataset.action === "overview-today") {
      openTaskBucket("today");
    }
    if (button.dataset.action === "overview-diary") {
      switchView("diary");
    }
    if (button.dataset.action === "overview-achievements") {
      switchView("achievements");
    }
    if (button.dataset.action === "overview-completed") {
      switchView("achievements");
    }
    if (button.dataset.action === "overview-next") {
      openTaskBucket(button.dataset.bucket || "inbox");
    }
  });

  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
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

  $("#calendarPrev").addEventListener("click", () => {
    state.calendarMonth = shiftMonth(state.calendarMonth, -1);
    renderCalendar();
  });
  $("#calendarNext").addEventListener("click", () => {
    state.calendarMonth = shiftMonth(state.calendarMonth, 1);
    renderCalendar();
  });
  $("#calendarGrid").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-date]");
    if (!button) return;
    state.calendarSelectedDate = button.dataset.date;
    state.calendarMonth = button.dataset.date.slice(0, 7);
    renderCalendar();
  });
  $("#calendarAgenda").addEventListener("click", handleCalendarAgendaAction);

  $("#settingsForm").addEventListener("input", updateSettingsFromForm);
  $("#settingsForm").addEventListener("change", updateSettingsFromForm);
  $("#resetSettings").addEventListener("click", resetSettings);
  $("#resetAccentColor").addEventListener("click", resetAccentColor);
  $("#restoreHiddenProgress").addEventListener("click", restoreHiddenDashboardTasks);

  $("#searchInput").addEventListener("input", renderEntries);
  $("#achievementSearchInput").addEventListener("input", renderAchievements);

  $("#taskList").addEventListener("click", handleTaskListAction);
  $("#taskDetailList").addEventListener("click", handleTaskListAction);

  $("#entryList").addEventListener("click", async (event) => {
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
  $("#achievementList").addEventListener("click", openSourceEntry);
  $("#dashboardRecent").addEventListener("click", openSourceEntry);

  $("#entryEvidenceList").addEventListener("click", async (event) => {
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

  $("#deleteEntry").addEventListener("click", async () => {
    const id = $("#entryId").value;
    if (!id) return;
    closeDrawer();
    await deleteEntryById(id);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#entryDrawer").classList.contains("hidden")) {
      closeDrawer();
    }
    if (!$("#calendarDrawer").classList.contains("hidden")) {
      closeCalendar();
    }
    if (!$("#settingsDrawer").classList.contains("hidden")) {
      closeSettings();
    }
  });
}

async function init() {
  loadSettings();
  loadHiddenUiState();
  applySettings();
  updateSettingsForm();
  updateRepeatControls("create");
  $("#inlineQuickDate").value = today();
  $("#entryDate").value = today();
  clearLegacyServiceWorkers();
  window.addEventListener("unhandledrejection", (event) => {
    showToast(event.reason?.message || "Something went wrong.");
  });
  bindEvents();

  try {
    await loadData();
    if (state.settings.defaultView !== "dashboard") {
      switchView(state.settings.defaultView);
    }
  } catch (error) {
    showToast(error.message);
  }
}

init();
