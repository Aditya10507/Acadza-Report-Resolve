// Runtime smoke test for the demo login page (catches broken listeners,
// which used to make "Sign in" reload the same page).
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../Frontend", import.meta.url));
const html = fs.readFileSync(path.join(ROOT, "login.html"), "utf8");
const loginJs = fs.readFileSync(path.join(ROOT, "login.js"), "utf8");

const calls = [];
const respond = (url, body) => {
  calls.push(url);
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
};

const makeDom = (fetchMock) => {
  const dom = new JSDOM(html, { url: "http://localhost/login.html", runScripts: "outside-only", pretendToBeVisual: true });
  dom.window.fetch = fetchMock;
  return dom;
};

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const assert = (name, condition) => {
  results.push([condition ? "PASS" : "FAIL", name]);
  if (!condition) process.exitCode = 1;
};

// 1. login mode: student credentials navigate to the student panel
{
  const dom = makeDom((url, options = {}) => {
    if (String(url).includes("/api/auth/me")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
    }
    if (String(url).includes("/api/auth/login")) {
      return respond(url, { username: "student", role: "student" });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  });
  const { window } = dom;
  window.eval(loginJs);
  await wait(40);
  assert("login page wires both tabs", window.document.querySelectorAll(".auth-tab").length === 2);
  assert("signup-only role row starts hidden", window.document.querySelector("#roleRow").classList.contains("hidden"));

  window.document.querySelector("#username").value = "student";
  window.document.querySelector("#password").value = "student123";
  window.document.querySelector("#authForm")
    .dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(60);
  assert("sign-in calls the login endpoint", calls.some((c) => String(c).includes("/api/auth/login")));
  assert("successful sign-in leaves the page (button stays disabled)",
    window.document.querySelector("#authGo").disabled);
  assert("no error is shown on success", window.document.querySelector("#authError").classList.contains("hidden"));
}

// 2. signup mode: faculty role navigates to the faculty console
{
  calls.length = 0;
  const dom = makeDom((url) => {
    if (String(url).includes("/api/auth/me")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
    }
    if (String(url).includes("/api/auth/signup")) {
      return respond(url, { username: "new_fac", role: "faculty" });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  });
  const { window } = dom;
  window.eval(loginJs);
  await wait(40);
  window.document.querySelector('.auth-tab[data-mode="signup"]').click();
  assert("signup mode shows the role chooser", !window.document.querySelector("#roleRow").classList.contains("hidden"));

  window.document.querySelector("#username").value = "new_fac";
  window.document.querySelector("#password").value = "pass1234";
  window.document.querySelector('input[name="role"][value="faculty"]').checked = true;
  window.document.querySelector("#authForm")
    .dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(60);
  assert("signup calls the signup endpoint", calls.some((c) => String(c).includes("/api/auth/signup")));
  assert("faculty signup heads to the console (button stays disabled)",
    window.document.querySelector("#authGo").disabled);
}

// 3. wrong password shows the error and re-enables the button
{
  const dom = makeDom((url) => {
    if (String(url).includes("/api/auth/me")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
    }
    if (String(url).includes("/api/auth/login")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ detail: "Wrong username or password." }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  });
  const { window } = dom;
  window.eval(loginJs);
  await wait(40);
  window.document.querySelector("#username").value = "student";
  window.document.querySelector("#password").value = "nope";
  window.document.querySelector("#authForm")
    .dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(60);
  assert("bad credentials surface an error message",
    !window.document.querySelector("#authError").classList.contains("hidden"));
  assert("sign-in button is usable again after a failure", !window.document.querySelector("#authGo").disabled);
}

console.log(results.map(([status, name]) => `${status}  ${name}`).join("\n"));
console.log(results.some(([status]) => status === "FAIL") ? "\nFAILURES" : "\nALL PASS");
