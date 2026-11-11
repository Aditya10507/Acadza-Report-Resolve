/* Acadza demo · login page */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const homeFor = (role) => (role === "faculty" ? "/" : "/student.html");

let mode = "login";

function setMode(next) {
  mode = next;
  $$(".auth-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === mode));
  $("#roleRow").classList.toggle("hidden", mode !== "signup");
  $("#authGo").textContent = mode === "login" ? "Sign in" : "Create account";
  $("#authError").classList.add("hidden");
}

async function submit(event) {
  event.preventDefault();
  const button = $("#authGo");
  button.disabled = true;
  $("#authError").classList.add("hidden");
  try {
    const body = {
      username: $("#username").value.trim(),
      password: $("#password").value,
    };
    if (mode === "signup") {
      body.role = document.querySelector('input[name="role"]:checked').value;
    }
    const res = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");
    window.location.href = homeFor(data.role);
  } catch (err) {
    const box = $("#authError");
    box.textContent = err.message;
    box.classList.remove("hidden");
    button.disabled = false;
  }
}

// Already signed in? Go straight to the right panel.
(async () => {
  $$(".auth-tab").forEach((tab) =>
    tab.addEventListener("click", () => setMode(tab.dataset.mode)));
  $("#authForm").addEventListener("submit", submit);

  try {
    const res = await fetch("/api/auth/me", { cache: "no-store" });
    if (res.ok) {
      const me = await res.json();
      window.location.href = homeFor(me.role);
    }
  } catch { /* login page works offline of auth */ }
})();
