// ── State ────────────────────────────────────────────────────────────────────
const state = {
  isRecording: false,
  isProcessing: false,
  timerInterval: null,
  pollInterval: null,
  startTime: null,
  actionItemCounter: 0,
};

// ── Recording Controls ───────────────────────────────────────────────────────
async function toggleRecording() {
  if (state.isProcessing) return;
  state.isRecording ? await stopRecording() : await startRecording();
}

async function startRecording() {
  const title = document.getElementById("meeting-title").value.trim() || "Untitled Meeting";

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error("Failed to start");

    state.isRecording = true;
    state.startTime = Date.now();

    setRecordingUI(true);
    startTimer();
    startPolling();
  } catch (err) {
    console.error(err);
    showToast("Could not start recording", "error");
  }
}

async function stopRecording() {
  setProcessingUI(true);

  try {
    await fetch("/api/stop", { method: "POST" });
    state.isRecording = false;

    stopTimer();
    stopPolling();
    setProcessingUI(false);
    setRecordingUI(false);
    setStatusBadge("Completed", "green");
    showExportBar();

    // Poll until summary arrives (auto-summary has a 3s delay before it starts)
    await fetchStatus();
    state.summaryPollInterval = setInterval(async () => {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (data.summary) {
        renderSummary(data.summary);
        renderActionItems(data.action_items);
        clearInterval(state.summaryPollInterval);
        state.summaryPollInterval = null;
        showToast("Summary ready", "success");
        loadTodos();
      }
    }, 2000);
    // Give up after 3 minutes
    setTimeout(() => {
      if (state.summaryPollInterval) {
        clearInterval(state.summaryPollInterval);
        state.summaryPollInterval = null;
      }
    }, 180000);
  } catch (err) {
    console.error(err);
    setProcessingUI(false);
    showToast("Error stopping recording", "error");
  }
}

// ── UI State Helpers ─────────────────────────────────────────────────────────
function setRecordingUI(recording) {
  const btn = document.getElementById("record-btn");
  const icon = document.getElementById("record-icon");
  const label = document.getElementById("record-label");
  const waveform = document.getElementById("waveform");
  const timer = document.getElementById("timer");
  const titleInput = document.getElementById("meeting-title");

  btn.className = btn.className.replace(/record-btn-\w+/g, "");

  if (recording) {
    btn.classList.add("record-btn-recording");
    icon.className = "w-3 h-3 rounded-full bg-current animate-pulse";
    label.textContent = "Stop Recording";
    waveform.classList.remove("hidden");
    waveform.classList.add("flex");
    timer.classList.remove("hidden");
    titleInput.disabled = true;
    setStatusBadge("Recording", "red");
  } else {
    btn.classList.add("record-btn-idle");
    icon.className = "w-3 h-3 rounded-full bg-current";
    label.textContent = "Start Recording";
    waveform.classList.add("hidden");
    waveform.classList.remove("flex");
    titleInput.disabled = false;
  }
}

function setProcessingUI(processing) {
  state.isProcessing = processing;
  const btn = document.getElementById("record-btn");
  const label = document.getElementById("record-label");
  const icon = document.getElementById("record-icon");

  btn.className = btn.className.replace(/record-btn-\w+/g, "");
  if (processing) {
    btn.classList.add("record-btn-processing");
    icon.outerHTML = `<span id="record-icon" class="processing-spinner"></span>`;
    label.textContent = "Processing…";
    btn.disabled = true;
    setStatusBadge("Processing", "yellow");
  } else {
    btn.disabled = false;
  }
}

function setStatusBadge(text, color) {
  const badge = document.getElementById("status-badge");
  const colorMap = {
    red:    "bg-red-900/50 text-red-400",
    green:  "bg-green-900/50 text-green-400",
    yellow: "bg-yellow-900/50 text-yellow-400",
    gray:   "bg-slate-700 text-slate-400",
  };
  badge.className = `text-xs px-3 py-1 rounded-full font-medium transition-all duration-300 ${colorMap[color] || colorMap.gray}`;
  badge.textContent = text;
}

function showExportBar() {
  document.getElementById("export-bar").classList.remove("hidden");
  document.getElementById("export-bar").classList.add("flex");
}

// ── Timer ────────────────────────────────────────────────────────────────────
function startTimer() {
  state.timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
    const h = String(Math.floor(elapsed / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    document.getElementById("timer").textContent = `${h}:${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  clearInterval(state.timerInterval);
  state.timerInterval = null;
}

// ── Polling ──────────────────────────────────────────────────────────────────
function startPolling() {
  state.pollInterval = setInterval(fetchStatus, 2000);
}

function stopPolling() {
  clearInterval(state.pollInterval);
  state.pollInterval = null;
}

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    renderTranscript(data.transcript);
    if (data.summary)      renderSummary(data.summary);
    if (data.action_items) renderActionItems(data.action_items);

    // Meeting detection badge
    const badge = document.getElementById("meeting-detected-badge");
    if (data.meeting_detected) badge.classList.replace("hidden", "flex");
    else badge.classList.replace("flex", "hidden");

    // Summarizing state
    const btn = document.getElementById("summarize-btn");
    if (btn) btn.disabled = data.summarizing;
    if (data.summarizing) {
      const sc = document.getElementById("summary-content");
      if (!sc.querySelector(".summarizing-label")) {
        sc.innerHTML = `<div class="summarizing-label py-12 justify-center">
          <span class="processing-spinner"></span> Generating summary with ${state._ollamaModel || "Ollama"}…
        </div>`;
      }
    }

    // Auto-start/stop from meeting watcher
    if (data.auto_action === "start" && !state.isRecording) {
      showToast("Meeting detected — auto-starting recording", "info");
      await startRecording();
    } else if (data.auto_action === "stop" && state.isRecording) {
      showToast("Meeting ended — auto-stopping recording", "info");
      await stopRecording();
    }
  } catch (err) {
    console.error("Poll failed:", err);
  }
}

// ── Render Functions ─────────────────────────────────────────────────────────
function renderTranscript(text) {
  const container = document.getElementById("transcript-content");
  if (!text) return;

  container.innerHTML = "";
  const lines = text.split("\n").filter(Boolean);
  lines.forEach((line) => {
    const div = document.createElement("div");
    div.className = "transcript-line";

    // Try [HH:MM:SS] Speaker: text
    const speakerMatch = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(You|Meeting):\s*(.*)/);
    if (speakerMatch) {
      const [, ts, speaker, content] = speakerMatch;
      const cls = speaker === "You" ? "speaker-you" : "speaker-meeting";
      div.innerHTML = `<span class="timestamp">${ts}</span><span class="${cls}">${escapeHtml(speaker)}</span>${escapeHtml(content)}`;
    } else {
      // Fallback: plain [HH:MM:SS] text
      const tsMatch = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)/);
      if (tsMatch) {
        div.innerHTML = `<span class="timestamp">${tsMatch[1]}</span>${escapeHtml(tsMatch[2])}`;
      } else {
        div.textContent = line;
      }
    }
    container.appendChild(div);
  });

  container.scrollTop = container.scrollHeight;
}

function renderSummary(text) {
  const container = document.getElementById("summary-content");
  container.innerHTML = `<div class="summary-body">${escapeHtml(text).replace(/\n/g, "<br/>")}</div>`;
}

function renderActionItems(items) {
  const container = document.getElementById("actions-content");
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = `<p class="text-slate-500 text-sm text-center py-12">No action items identified</p>`;
    return;
  }

  items.forEach((item, i) => {
    const id = `action-${i}`;
    const div = document.createElement("div");
    div.className = "action-item";
    div.id = `item-wrapper-${i}`;
    div.innerHTML = `
      <input type="checkbox" id="${id}" onchange="toggleActionItem(${i})" />
      <label for="${id}">${escapeHtml(item)}</label>
    `;
    container.appendChild(div);
  });

  // Update badge count
  const badge = document.getElementById("action-count");
  badge.textContent = items.length;
  badge.classList.remove("hidden");
}

function toggleActionItem(index) {
  const wrapper = document.getElementById(`item-wrapper-${index}`);
  wrapper.classList.toggle("done");
}

// ── Clear ─────────────────────────────────────────────────────────────────────
async function clearTranscript() {
  if (!confirm("Clear the transcript, summary, and action items?")) return;
  try {
    await fetch("/api/clear", { method: "POST" });
    document.getElementById("transcript-content").innerHTML = `
      <div class="empty-state flex flex-col items-center justify-center py-16 text-center">
        <p class="text-slate-500 text-sm">Start recording to see live transcript</p>
      </div>`;
    document.getElementById("summary-content").innerHTML = "";
    document.getElementById("actions-content").innerHTML = "";
    document.getElementById("action-count").classList.add("hidden");
    document.getElementById("export-bar").classList.add("hidden");
    showToast("Cleared", "info");
  } catch {
    showToast("Clear failed", "error");
  }
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.add("hidden"));
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("tab-active"));
  document.getElementById(`pane-${name}`).classList.remove("hidden");
  document.getElementById(`tab-${name}`).classList.add("tab-active");
}

// ── Export ───────────────────────────────────────────────────────────────────
async function saveNotes() {
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    document.getElementById("export-filename").textContent = data.filename;
    showToast(`Saved: ${data.filename}`, "success");
  } catch (err) {
    showToast("Save failed", "error");
  }
}

async function copyToClipboard() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    let text = `# ${data.meeting_title || "Meeting Notes"}\n\n`;
    if (data.summary) text += `## Summary\n\n${data.summary}\n\n`;
    if (data.action_items?.length) {
      text += `## Action Items\n\n`;
      data.action_items.forEach(item => { text += `- [ ] ${item}\n`; });
      text += "\n";
    }
    if (data.transcript) text += `## Transcript\n\n${data.transcript}`;

    // Use backend pbcopy (required for pywebview — clipboard API unavailable)
    const copyRes = await fetch("/api/clipboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!copyRes.ok) throw new Error("pbcopy failed");
    showToast("Copied to clipboard!", "success");
  } catch (err) {
    showToast("Copy failed", "error");
  }
}

// ── Toast Notification ───────────────────────────────────────────────────────
function showToast(message, type = "info") {
  const existing = document.getElementById("toast");
  if (existing) existing.remove();

  const colorMap = {
    success: "bg-green-900/90 text-green-300 border-green-700",
    error:   "bg-red-900/90 text-red-300 border-red-700",
    info:    "bg-surface-700 text-slate-300 border-slate-600",
  };

  const toast = document.createElement("div");
  toast.id = "toast";
  toast.className = `fixed bottom-6 right-6 px-4 py-3 rounded-xl border text-sm font-medium
    shadow-lg z-50 transition-all duration-300 ${colorMap[type] || colorMap.info}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ── Settings ─────────────────────────────────────────────────────────────────
function toggleSettings() {
  const panel = document.getElementById("settings-panel");
  panel.classList.toggle("open");
}

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    document.getElementById("notes-dir").value = data.notes_dir || "";
    document.getElementById("ollama-model").value = data.ollama_model || "";
    document.getElementById("notion-token").value = data.notion_token || "";
    document.getElementById("notion-parent-id").value = data.notion_parent_id || "";
    document.getElementById("auto-start").checked = !!data.auto_start;
    state._ollamaModel = data.ollama_model;
  } catch {}
}

async function saveSettings() {
  const payload = {
    notes_dir: document.getElementById("notes-dir").value.trim(),
    ollama_model: document.getElementById("ollama-model").value.trim(),
    notion_token: document.getElementById("notion-token").value.trim(),
    notion_parent_id: document.getElementById("notion-parent-id").value.trim(),
    auto_start: document.getElementById("auto-start").checked,
  };
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state._ollamaModel = payload.ollama_model;
    showToast("Settings saved", "success");
    toggleSettings();
  } catch {
    showToast("Failed to save settings", "error");
  }
}

window.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  setDefaultMeetingTitle();
  loadTodos();
});

function setDefaultMeetingTitle() {
  const input = document.getElementById("meeting-title");
  if (input && !input.value.trim()) {
    const now = new Date();
    const pad = n => String(n).padStart(2, "0");
    input.value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())} Meeting`;
  }
}

// ── Summary generation ────────────────────────────────────────────────────────
async function requestSummary() {
  try {
    const res = await fetch("/api/summary", { method: "POST" });
    if (res.status === 400) {
      showToast("No transcript to summarize yet", "error");
      return;
    }
    showToast("Generating summary with Ollama…", "info");
  } catch {
    showToast("Could not reach Ollama", "error");
  }
}

// ── Todos ─────────────────────────────────────────────────────────────────────
async function loadTodos() {
  try {
    const res = await fetch("/api/todos");
    const todos = await res.json();
    renderTodos(todos);
  } catch {}
}

function renderTodos(todos) {
  const container = document.getElementById("todos-content");
  if (!todos || todos.length === 0) {
    container.innerHTML = `<p class="text-slate-500 text-sm text-center py-16">Action items from completed meetings will appear here</p>`;
    return;
  }

  container.innerHTML = "";
  // Show newest first
  [...todos].reverse().forEach(entry => {
    const section = document.createElement("div");
    section.className = "mb-6";
    section.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <div>
          <p class="text-sm font-semibold text-slate-200">${escapeHtml(entry.meeting_title)}</p>
          <p class="text-xs text-slate-500">${escapeHtml(entry.date)}</p>
        </div>
        <button onclick="deleteTodoMeeting('${entry.id}')"
          class="text-slate-600 hover:text-red-400 transition-colors p-1">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
          </svg>
        </button>
      </div>`;

    entry.items.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = `action-item${item.done ? " done" : ""}`;
      row.id = `todo-${entry.id}-${idx}`;
      const cbId = `todo-cb-${entry.id}-${idx}`;
      row.innerHTML = `
        <input type="checkbox" id="${cbId}" ${item.done ? "checked" : ""}
          onchange="toggleTodo('${entry.id}', ${idx})" />
        <label for="${cbId}">${escapeHtml(item.text)}</label>`;
      section.appendChild(row);
    });

    container.appendChild(section);
  });
}

async function toggleTodo(entryId, itemIdx) {
  await fetch(`/api/todos/${entryId}/toggle/${itemIdx}`, { method: "POST" });
  const row = document.getElementById(`todo-${entryId}-${itemIdx}`);
  if (row) row.classList.toggle("done");

  // If all items in this meeting are now checked, wipe the meeting entry
  const res = await fetch("/api/todos");
  const todos = await res.json();
  const entry = todos.find(t => t.id === entryId);
  if (entry && entry.items.every(i => i.done)) {
    await fetch(`/api/todos/${entryId}`, { method: "DELETE" });
    loadTodos();
    showToast("All done — meeting cleared from todos", "success");
  }
}

async function deleteTodoMeeting(entryId) {
  await fetch(`/api/todos/${entryId}`, { method: "DELETE" });
  loadTodos();
}

// ── Notion ────────────────────────────────────────────────────────────────────
async function pushToNotion() {
  const btn = document.querySelector('[onclick="pushToNotion()"]');
  const originalHTML = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="processing-spinner"></span> Sending…`;
  }
  try {
    const res = await fetch("/api/notion/push", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showToast(`Notion error: ${data.error || "unknown"}`, "error");
      if (btn) { btn.disabled = false; btn.innerHTML = originalHTML; }
      return;
    }
    // Show green success state
    if (btn) {
      btn.classList.add("bg-green-700", "text-white");
      btn.classList.remove("bg-surface-700", "text-slate-300");
      btn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg> Saved to Notion`;
      setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove("bg-green-700", "text-white");
        btn.classList.add("bg-surface-700", "text-slate-300");
        btn.innerHTML = originalHTML;
      }, 3000);
    }
    showToast("Pushed to Notion!", "success");
  } catch {
    showToast("Notion push failed", "error");
    if (btn) { btn.disabled = false; btn.innerHTML = originalHTML; }
  }
}

// ── Utils ────────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Demo Mode (for UI preview without backend) ───────────────────────────────
// Uncomment the block below to test the UI with fake data:
/*
window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    renderTranscript(
      "[00:00:05] Alice: Let's kick off the Q2 planning.\n" +
      "[00:00:12] Bob: Agreed. I think we should prioritise the onboarding flow first.\n" +
      "[00:00:28] Alice: Yes, and we need to fix the login bug before the release.\n" +
      "[00:00:45] Bob: I'll take ownership of that. Target: end of week.\n" +
      "[00:01:10] Alice: Great. Also, someone needs to update the docs by Friday."
    );
    renderSummary(
      "The team discussed Q2 priorities, focusing on improving the onboarding flow and " +
      "resolving the critical login bug ahead of the upcoming release. " +
      "Responsibilities were assigned and timelines confirmed for end of week."
    );
    renderActionItems([
      "Bob: Fix the login bug before end of week",
      "Alice: Prioritise onboarding flow improvements for Q2",
      "Update the documentation by Friday",
    ]);
    setStatusBadge("Completed", "green");
    showExportBar();
  }, 500);
});
*/
