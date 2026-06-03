const loginForm = document.querySelector("#loginForm");
const passwordInput = document.querySelector("#passwordInput");
const loginError = document.querySelector("#loginError");

async function login(event) {
  event.preventDefault();
  loginError.textContent = "";

  const response = await fetch("/api/login", {
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

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}

loginForm.addEventListener("submit", login);
