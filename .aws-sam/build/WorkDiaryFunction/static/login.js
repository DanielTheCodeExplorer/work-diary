const loginForm = document.querySelector("#loginForm");
const passwordInput = document.querySelector("#passwordInput");
const loginError = document.querySelector("#loginError");
const API_BASE_URL = window.API_BASE_URL || "";

async function clearLegacyServiceWorkers() {
  if (!("serviceWorker" in navigator)) return;
  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
  } catch {
    // Ignore failures; login should still work without offline caching.
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

async function login(event) {
  event.preventDefault();
  loginError.textContent = "";

  const response = await fetch(`${API_BASE_URL}/api/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      password: passwordInput.value,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    loginError.textContent = payload.error || "Login failed.";
    passwordInput.select();
    return;
  }
  if (payload.token) {
    localStorage.setItem("workDiaryToken", payload.token);
  }
  window.location.assign("/");
}

clearLegacyServiceWorkers();
loginForm.addEventListener("submit", login);
