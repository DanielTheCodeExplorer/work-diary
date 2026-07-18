const loginForm = document.querySelector("#loginForm");
const passwordInput = document.querySelector("#passwordInput");
const loginError = document.querySelector("#loginError");
const API_BASE_URL = window.API_BASE_URL || "";
const APP_ASSET_VERSION = "20260718-archive-any-task";
const API_REQUEST_TIMEOUT_MS = 10000;

function setLoginError(message) {
  if (loginError) {
    loginError.textContent = message;
  }
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register(`/service-worker.js?v=${APP_ASSET_VERSION}`);
  } catch {
    // Ignore failures; login should still work without offline caching or push.
  }
}

async function login(event) {
  event.preventDefault();
  setLoginError("");
  if (!passwordInput) {
    setLoginError("Login form did not load correctly. Refresh the page.");
    return;
  }

  const submit = loginForm?.querySelector('[type="submit"]');
  if (submit?.disabled) return;
  if (submit) submit.disabled = true;
  loginForm?.setAttribute("aria-busy", "true");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}/api/login`, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        password: passwordInput.value,
      }),
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = {};
    }
    if (!response.ok) {
      setLoginError(payload.error || "Login failed. Please try again.");
      passwordInput.select();
      return;
    }
    if (payload.token) {
      localStorage.setItem("workDiaryToken", payload.token);
    }
    window.location.assign("/");
  } catch (error) {
    setLoginError(
      error?.name === "AbortError"
        ? "Login took too long. Check your connection and try again."
        : "The server could not be reached. Check your connection and try again."
    );
  } finally {
    window.clearTimeout(timeout);
    if (submit) submit.disabled = false;
    loginForm?.setAttribute("aria-busy", "false");
  }
}

registerServiceWorker();
if (loginForm) {
  loginForm.addEventListener("submit", login);
}
