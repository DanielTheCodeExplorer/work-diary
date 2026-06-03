const state = {
  activeView: "todo",
  taskBucket: "inbox",
  tasks: [],
  entries: [],
  evidence: [],
  options: {
    projects: [],
    skills: [],
    tags: [],
    evidence_types: [],
  },
  selectedEntry: null,
  pendingEvidence: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const API_BASE_URL = window.API_BASE_URL || "";

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
    window.location.assign("/login");
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

async function loadData() {
  const [tasks, entries, evidence, options] = await Promise.all([
    api("/api/tasks"),
    api("/api/entries"),
    api("/api/evidence"),
    api("/api/options"),
  ]);
  state.tasks = tasks;
  state.entries = entries;
  state.evidence = evidence;
  state.options = options;
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
  setSelectOptions($("#projectFilter"), "All projects", state.options.projects, $("#projectFilter").value);
  setSelectOptions($("#skillFilter"), "All skills", state.options.skills, $("#skillFilter").value);
  setSelectOptions($("#tagFilter"), "All tags", state.options.tags, $("#tagFilter").value);
  setSelectOptions($("#evidenceType"), "Website link", state.options.evidence_types, $("#evidenceType").value || "website");
}

function switchView(view) {
  state.activeView = view;
  $$(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `${view}View`);
  });
  render();
}

function getDiaryFilters() {
  return {
    search: compact($("#searchInput").value).toLowerCase(),
    project: compact($("#projectFilter").value).toLowerCase(),
    skill: compact($("#skillFilter").value).toLowerCase(),
    tag: compact($("#tagFilter").value).toLowerCase(),
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
  if (filters.project && entry.project.toLowerCase() !== filters.project) return false;
  if (filters.skill && !(entry.skills_used || []).some((skill) => skill.toLowerCase() === filters.skill)) return false;
  if (filters.tag && !(entry.tags || []).some((tag) => tag.toLowerCase() === filters.tag)) return false;
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
    meta.push(`Repeats ${task.repeat_rule}`);
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
  const openTasks = state.tasks.filter((task) => !task.completed);
  const inboxTasks = openTasks.filter((task) => !task.due_date);
  const todayTasks = openTasks.filter((task) => task.due_date === today());
  const upcomingTasks = openTasks.filter((task) => task.due_date && task.due_date !== today());
  return {
    openTasks,
    inbox: sortTasks(inboxTasks),
    today: sortTasks(todayTasks),
    upcoming: sortTasks(upcomingTasks),
  };
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
        <button class="link-button" type="button" data-action="log-task">Log</button>
        <button class="ghost-button danger" type="button" data-action="delete-task">Delete</button>
      </div>
    </article>
  `;
}

function renderTaskColumn(bucket, tasks) {
  const title = taskBucketTitle(bucket);
  const countLabel = taskBucketCountLabel(bucket, tasks.length);
  const preview = tasks.slice(0, 4);
  return `
    <section class="planner-box" aria-label="${escapeHtml(title)}">
      <header class="planner-box-head">
        <h3>${escapeHtml(title)}</h3>
        <div class="planner-box-head-actions">
          <span>${escapeHtml(countLabel)}</span>
          <button class="planner-box-link" type="button" data-action="view-task-bucket" data-bucket="${escapeHtml(bucket)}">View all</button>
        </div>
      </header>
      <div class="planner-box-list">
        ${
          preview.length
            ? preview.map((task) => renderTaskCard(task)).join("")
            : `<div class="planner-empty">No tasks here.</div>`
        }
        ${tasks.length > preview.length ? `<button class="ghost-button" type="button" data-action="view-task-bucket" data-bucket="${escapeHtml(bucket)}">Show ${tasks.length - preview.length} more</button>` : ""}
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

function renderTaskDetail() {
  const buckets = taskBuckets();
  const tasks = buckets[state.taskBucket] || buckets.inbox;
  const title = taskBucketTitle(state.taskBucket);
  $("#taskDetailHeading").textContent = title;
  $("#taskDetailCount").textContent = taskBucketCountLabel(state.taskBucket, tasks.length);
  $("#taskDetailList").innerHTML = tasks.length
    ? tasks.map((task) => renderTaskCard(task, { expanded: true })).join("")
    : `<div class="empty-state">No tasks here.</div>`;
}

function openTaskBucket(bucket) {
  state.taskBucket = bucket;
  switchView("taskDetail");
}

function renderEntries() {
  const filters = getDiaryFilters();
  const entries = state.entries.filter((entry) => entryMatches(entry, filters));
  const list = $("#entryList");
  if (entries.length === 0) {
    list.innerHTML = `<div class="empty-state">No diary entries found.</div>`;
    return;
  }

  list.innerHTML = entries
    .map((entry) => {
      const meta = [
        entry.project ? escapeHtml(entry.project) : "",
        entry.difficulty ? escapeHtml(entry.difficulty) : "",
        `${entry.evidence_count} evidence`,
      ]
        .filter(Boolean)
        .join(" / ");
      const tags = [...(entry.skills_used || []), ...(entry.tags || [])];
      return `
        <article class="entry-card ${entry.source_mode === "quick_log" ? "quick" : ""}" data-entry-id="${entry.id}">
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
            <button class="link-button" type="button" data-action="view-project">Project</button>
            <button class="link-button" type="button" data-action="draft-entry-bullet">Draft bullet</button>
            <button class="secondary-button" type="button" data-action="edit-entry">Edit</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function projectKey(project) {
  return compact(project) || "No project";
}

function buildProjectGroups() {
  const groups = new Map();
  const ensure = (name) => {
    const key = projectKey(name);
    if (!groups.has(key)) {
      groups.set(key, {
        name: key,
        tasks: [],
        entries: [],
        evidence: [],
      });
    }
    return groups.get(key);
  };

  state.tasks.forEach((task) => ensure(task.project).tasks.push(task));
  state.entries.forEach((entry) => ensure(entry.project).entries.push(entry));
  state.evidence.forEach((item) => ensure(item.project).evidence.push(item));

  return Array.from(groups.values()).sort((a, b) => {
    if (a.name === "No project") return 1;
    if (b.name === "No project") return -1;
    return a.name.localeCompare(b.name);
  });
}

function projectMatches(group, search) {
  if (!search) return true;
  const text = [
    group.name,
    ...group.tasks.map((task) => [task.title, task.location, task.notes].join(" ")),
    ...group.entries.map((entry) => [entry.title, entry.what_i_did, ...(entry.skills_used || [])].join(" ")),
    ...group.evidence.map((item) => [item.title, item.description, item.evidence_type_label].join(" ")),
  ]
    .join(" ")
    .toLowerCase();
  return text.includes(search);
}

function renderProjectMiniTasks(tasks) {
  const openTasks = sortTasks(tasks.filter((task) => !task.completed)).slice(0, 5);
  if (openTasks.length === 0) return `<div class="planner-empty">No open tasks.</div>`;
  return openTasks
    .map((task) => {
      const meta = [
        task.due_date ? `${formatDate(task.due_date)}${task.due_time ? ` ${formatTime(task.due_time)}` : ""}` : "",
        taskPriorityLabel(task.priority),
        task.location,
      ]
        .filter(Boolean)
        .map((item) => `<span>${escapeHtml(item)}</span>`)
        .join("");
      return `
        <div class="project-mini-item" data-task-id="${task.id}">
          <div class="project-mini-title">${escapeHtml(task.title)}</div>
          ${meta ? `<div class="project-mini-meta">${meta}</div>` : ""}
        </div>
      `;
    })
    .join("");
}

function renderProjectMiniEntries(entries) {
  const recentEntries = [...entries]
    .sort((a, b) => b.entry_date.localeCompare(a.entry_date))
    .slice(0, 4);
  if (recentEntries.length === 0) return `<div class="planner-empty">No diary entries.</div>`;
  return recentEntries
    .map((entry) => {
      return `
        <div class="project-mini-item" data-entry-id="${entry.id}">
          <div class="project-mini-title">${escapeHtml(entry.title)}</div>
          <div class="project-mini-meta">
            <span>${escapeHtml(formatDate(entry.entry_date))}</span>
            <button class="link-button" type="button" data-action="edit-entry">Edit</button>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderProjectMiniEvidence(evidenceItems) {
  const items = evidenceItems.slice(0, 4);
  if (items.length === 0) return `<div class="planner-empty">No evidence links.</div>`;
  return items
    .map((item) => {
      return `
        <div class="project-mini-item" data-entry-id="${item.work_entry_id}">
          <div class="project-mini-title">${escapeHtml(item.title)}</div>
          <div class="project-mini-meta">
            <span>${escapeHtml(item.evidence_type_label)}</span>
            <button class="link-button" type="button" data-action="edit-entry">Edit entry</button>
          </div>
          ${
            item.evidence_url
              ? `<a class="project-mini-link" href="${escapeHtml(item.evidence_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.evidence_url)}</a>`
              : `<span class="chip">Upload placeholder</span>`
          }
        </div>
      `;
    })
    .join("");
}

function renderProjects() {
  const search = compact($("#projectSearchInput").value).toLowerCase();
  const groups = buildProjectGroups().filter((group) => projectMatches(group, search));
  const list = $("#projectList");
  if (groups.length === 0) {
    list.innerHTML = `<div class="empty-state">No projects found.</div>`;
    return;
  }

  list.innerHTML = groups
    .map((group) => {
      const openTaskCount = group.tasks.filter((task) => !task.completed).length;
      const meta = [
        `${openTaskCount} open tasks`,
        `${group.entries.length} diary entries`,
        `${group.evidence.length} evidence links`,
      ].join(" / ");
      return `
        <article class="project-card" data-project="${escapeHtml(group.name)}">
          <div class="card-top">
            <div>
              <h3>${escapeHtml(group.name)}</h3>
              <div class="project-meta">${escapeHtml(meta)}</div>
            </div>
          </div>
          <div class="project-grid">
            <section class="project-column">
              <h4>Tasks</h4>
              <div class="project-mini-list">${renderProjectMiniTasks(group.tasks)}</div>
            </section>
            <section class="project-column">
              <h4>Diary</h4>
              <div class="project-mini-list">${renderProjectMiniEntries(group.entries)}</div>
            </section>
            <section class="project-column">
              <h4>Evidence</h4>
              <div class="project-mini-list">${renderProjectMiniEvidence(group.evidence)}</div>
            </section>
          </div>
        </article>
      `;
    })
    .join("");
}

function render() {
  renderTasks();
  renderTaskDetail();
  renderEntries();
  renderProjects();
}

function setMode(mode) {
  const quick = mode === "quick";
  const task = mode === "task";
  const details = mode === "details";
  $$(".mode").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  $("#drawerQuickForm").classList.toggle("hidden", !quick);
  $("#drawerTaskForm").classList.toggle("hidden", !task);
  $("#entryForm").classList.toggle("hidden", !details);
}

function findEntry(entryId) {
  return state.entries.find((entry) => String(entry.id) === String(entryId));
}

function openDrawer(entry = null, mode = "quick") {
  state.selectedEntry = entry;
  state.pendingEvidence = [];
  $("#entryDrawer").classList.remove("hidden");
  $("#entryDrawer").setAttribute("aria-hidden", "false");
  $("#drawerMode").textContent = mode === "task" ? "New task" : entry ? "Edit log" : "New log";
  $("#drawerTitle").textContent = mode === "task" ? "Planner task" : entry ? entry.title : "Work entry";

  $("#drawerQuickNote").value = "";
  $("#drawerQuickDate").value = today();
  $("#drawerQuickProject").value = entry?.project || "";
  clearTaskDetailForm();
  fillEntryForm(entry);
  setMode(mode);

  window.setTimeout(() => {
    const target = mode === "quick" ? $("#drawerQuickNote") : mode === "task" ? $("#drawerTaskTitle") : $("#entryTitle");
    target.focus();
  }, 0);
}

function closeDrawer() {
  $("#entryDrawer").classList.add("hidden");
  $("#entryDrawer").setAttribute("aria-hidden", "true");
  state.selectedEntry = null;
  state.pendingEvidence = [];
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

function clearTaskDetailForm(defaults = {}) {
  $("#drawerTaskTitle").value = defaults.title || "";
  $("#drawerTaskProject").value = defaults.project || "";
  $("#drawerTaskDueDate").value = defaults.due_date || "";
  $("#drawerTaskDueTime").value = defaults.due_time || "";
  $("#drawerTaskReminder").value = defaults.reminder_at || "";
  $("#drawerTaskRepeatRule").value = defaults.repeat_rule || "none";
  $("#drawerTaskPriority").value = defaults.priority || "";
  $("#drawerTaskLocation").value = defaults.location || "";
  $("#drawerTaskNotes").value = defaults.notes || "";
}

function openPrimaryAdd() {
  if (state.activeView === "todo" || state.activeView === "taskDetail") {
    const defaults = state.taskBucket === "today" ? { due_date: today() } : {};
    openDrawer(null, "task");
    clearTaskDetailForm(defaults);
    return;
  }
  openDrawer(null, "quick");
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

async function saveQuickLog(event, source) {
  event.preventDefault();
  const isInline = source === "inline";
  const noteInput = isInline ? $("#inlineQuickNote") : $("#drawerQuickNote");
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
      entry_date: isInline ? today() : $("#drawerQuickDate").value,
      project: isInline ? "" : $("#drawerQuickProject").value,
    },
  });
  noteInput.value = "";
  if (!isInline) closeDrawer();
  await loadData();
  showToast("Quick log saved.");
}

async function saveTask(event) {
  event.preventDefault();
  const titleInput = $("#taskTitle");
  const title = compact(titleInput.value);
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
    },
  });
  $("#taskTitle").value = "";
  $("#taskDueDate").value = "";
  $("#taskDueTime").value = "";
  $("#taskPriority").value = "";
  await loadData();
  showToast("Task added.");
}

async function saveDetailedTask(event) {
  event.preventDefault();
  const titleInput = $("#drawerTaskTitle");
  const title = compact(titleInput.value);
  if (!title) {
    showToast("Add a task first.");
    titleInput.focus();
    return;
  }

  await api("/api/tasks", {
    method: "POST",
    body: {
      title,
      project: $("#drawerTaskProject").value,
      due_date: $("#drawerTaskDueDate").value,
      due_time: $("#drawerTaskDueTime").value,
      reminder_at: $("#drawerTaskReminder").value,
      repeat_rule: $("#drawerTaskRepeatRule").value,
      priority: $("#drawerTaskPriority").value,
      location: $("#drawerTaskLocation").value,
      notes: $("#drawerTaskNotes").value,
    },
  });
  closeDrawer();
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
    ? state.evidence.filter((item) => item.work_entry_id === state.selectedEntry.id)
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
          </div>
          <button class="ghost-button danger" type="button" data-action="${item.pending ? "remove-pending-evidence" : "delete-entry-evidence"}">Remove</button>
        </div>
      `;
    })
    .join("");
}

async function draftBullet(entryId) {
  await api(`/api/entries/${entryId}/draft-bullet`, { method: "POST", body: {} });
  await loadData();
  const selected = state.selectedEntry?.id ? findEntry(state.selectedEntry.id) : null;
  if (selected) {
    state.selectedEntry = selected;
    fillEntryForm(selected);
  }
  showToast("Draft bullet updated.");
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

  if (button.dataset.action === "view-task-bucket") {
    openTaskBucket(button.dataset.bucket || "inbox");
    return;
  }

  const taskId = taskIdFromEvent(event);
  if (!taskId) return;

  if (button.dataset.action === "toggle-task") {
    await toggleTask(taskId);
  }
  if (button.dataset.action === "delete-task") {
    await deleteTask(taskId);
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

function bindEvents() {
  $("#openQuickAdd").addEventListener("click", openPrimaryAdd);
  $("#floatingQuickAdd").addEventListener("click", openPrimaryAdd);
  $("#logoutButton").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} });
    localStorage.removeItem("workDiaryToken");
    window.location.assign("/login");
  });
  $("#inlineQuickForm").addEventListener("submit", (event) => saveQuickLog(event, "inline"));
  $("#taskForm").addEventListener("submit", saveTask);
  $("#drawerTaskForm").addEventListener("submit", saveDetailedTask);
  $("#drawerQuickForm").addEventListener("submit", (event) => saveQuickLog(event, "drawer"));
  $("#entryForm").addEventListener("submit", saveEntry);
  $("#addEvidenceToEntry").addEventListener("click", addEvidenceToCurrentEntry);
  $("#backToPlanner").addEventListener("click", () => switchView("todo"));

  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $$(".mode").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });
  $$("[data-action='close-drawer']").forEach((button) => {
    button.addEventListener("click", closeDrawer);
  });

  ["searchInput", "projectFilter", "skillFilter", "tagFilter"].forEach((id) => {
    $(`#${id}`).addEventListener("input", renderEntries);
  });
  $("#projectSearchInput").addEventListener("input", renderProjects);

  $("#clearFilters").addEventListener("click", () => {
    $("#searchInput").value = "";
    $("#projectFilter").value = "";
    $("#skillFilter").value = "";
    $("#tagFilter").value = "";
    renderEntries();
  });

  $("#taskList").addEventListener("click", handleTaskListAction);
  $("#taskDetailList").addEventListener("click", handleTaskListAction);

  $("#entryList").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const entryId = entryIdFromEvent(event);
    const entry = findEntry(entryId);
    if (!entry) return;

    if (button.dataset.action === "edit-entry") {
      openDrawer(entry, "details");
    }
    if (button.dataset.action === "view-project") {
      switchView("projects");
      $("#projectSearchInput").value = entry.project || entry.title;
      renderProjects();
    }
    if (button.dataset.action === "draft-entry-bullet") {
      await draftBullet(entry.id);
    }
  });

  $("#projectList").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const entryId = entryIdFromEvent(event);
    if (button.dataset.action === "edit-entry") {
      const entry = findEntry(entryId);
      if (entry) openDrawer(entry, "details");
    }
  });

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
      if (window.confirm("Delete this evidence link?")) {
        await deleteEvidence(item.dataset.evidenceId);
      }
    }
  });

  $("#deleteEntry").addEventListener("click", async () => {
    const id = $("#entryId").value;
    if (!id) return;
    if (window.confirm("Delete this diary entry and its evidence links?")) {
      await api(`/api/entries/${id}`, { method: "DELETE" });
      closeDrawer();
      await loadData();
      showToast("Entry deleted.");
    }
  });

  $("#draftBullet").addEventListener("click", async () => {
    const id = $("#entryId").value;
    if (!id) {
      showToast("Save the entry first.");
      return;
    }
    await draftBullet(id);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#entryDrawer").classList.contains("hidden")) {
      closeDrawer();
    }
  });
}

async function init() {
  $("#drawerQuickDate").value = today();
  $("#entryDate").value = today();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  window.addEventListener("unhandledrejection", (event) => {
    showToast(event.reason?.message || "Something went wrong.");
  });
  bindEvents();

  // Add scroll detection for mobile-friendly header
  let lastScrollY = 0;
  let scrollTimeout;
  let hideCommitTimeout;
  const topbar = $("#topbar") || document.querySelector(".topbar");

  window.addEventListener("scroll", () => {
    const currentScrollY = window.scrollY;
    const isScrollingDown = currentScrollY > lastScrollY;

    if (isScrollingDown) {
      // Hide immediately on downward scroll
      topbar.classList.add("hidden");

      // Clear any previous commit timeout
      clearTimeout(hideCommitTimeout);

      // Fully commit the hide after 1 second of downward scrolling
      hideCommitTimeout = setTimeout(() => {
        topbar.classList.add("hidden");
      }, 1000);
    } else {
      // Show immediately on upward scroll
      clearTimeout(hideCommitTimeout);
      topbar.classList.remove("hidden");
    }

    lastScrollY = currentScrollY;
  }, { passive: true });

  try {
    await loadData();
  } catch (error) {
    showToast(error.message);
  }
}

init();
