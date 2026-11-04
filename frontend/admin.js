if (!window.IS_ADMIN) { window.location.replace("/"); }

let allUsers = [];
let currentPage = 1;
const PAGE_SIZE = 15;

function setResult(el, text, ok) {
  el.innerText = text;
  el.className = text ? (ok ? "status-ok" : "status-error") : "";
}

function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function renderPendingRequests(pending) {
  const section   = document.getElementById("pendingSection");
  const tbody     = document.getElementById("pendingTableBody");
  const badge     = document.getElementById("pendingBadge");
  const sidebarBadge = document.getElementById("sidebarPendingBadge");
  const resultEl  = document.getElementById("pendingActionResult");
  if (resultEl) resultEl.innerText = "";

  if (!pending || pending.length === 0) {
    section.style.display = "none";
    if (sidebarBadge) sidebarBadge.style.display = "none";
    return;
  }
  section.style.display = "block";
  badge.textContent = pending.length;
  if (sidebarBadge) { sidebarBadge.textContent = pending.length; sidebarBadge.style.display = ""; }

  tbody.innerHTML = pending.map((r, i) => {
    const date  = r.requested_at
      ? new Date(r.requested_at).toLocaleDateString(undefined, { year:"numeric", month:"short", day:"numeric" })
      : "–";
    return `<tr>
      <td>${escapeHtml(r.name)}</td>
      <td>${escapeHtml(r.email)}</td>
      <td class="muted">${escapeHtml(r.org)}</td>
      <td class="subtle">${date}</td>
      <td class="right">
        <button class="btn-table btn-approve"
          data-action="approve" data-email="${escapeHtml(r.email)}">${t("btn_approve")}</button>
        <button class="btn-table btn-muted"
          data-action="reject" data-email="${escapeHtml(r.email)}">${t("btn_reject")}</button>
      </td>
    </tr>`;
  }).join("");
}

async function loadPendingRequests() {
  try {
    const r = await fetch("/admin/pending-requests");
    if (!r.ok) return;
    renderPendingRequests(await r.json());
  } catch { /* pending section stays hidden */ }
}

async function approveRequest(email) {
  const resultEl = document.getElementById("pendingActionResult");
  const fd = new FormData();
  fd.append("email", email);
  const r = await fetch("/admin/approve-request", { method: "POST", body: fd });
  const res = await r.json();
  if (res.ok) {
    setResult(resultEl, t("approved_ok").replace("{email}", email), true);
    loadPendingRequests();
    loadUserList();
  } else {
    setResult(resultEl, translateError(res.detail), false);
  }
}

async function rejectRequest(email) {
  if (!confirm(t("confirm_reject").replace("{email}", email))) return;
  const resultEl = document.getElementById("pendingActionResult");
  const fd = new FormData();
  fd.append("email", email);
  const r = await fetch("/admin/reject-request", { method: "POST", body: fd });
  const res = await r.json();
  if (res.ok) {
    setResult(resultEl, t("rejected_ok"), true);
    loadPendingRequests();
  } else {
    setResult(resultEl, translateError(res.detail), false);
  }
}

function getSearchQuery() {
  return (document.getElementById("userSearch")?.value || "").trim().toLowerCase();
}

function getFilteredUsers() {
  const q = getSearchQuery();
  if (!q) return allUsers;
  return allUsers.filter(u =>
    u.email.toLowerCase().includes(q) ||
    (u.first_name && u.first_name.toLowerCase().includes(q)) ||
    (u.last_name && u.last_name.toLowerCase().includes(q))
  );
}

function renderUserList() {
  const tbody = document.getElementById("userTableBody");
  const paginationEl = document.getElementById("userPagination");
  const pageInfoEl = document.getElementById("pageInfo");
  const prevBtn = document.getElementById("pagePrev");
  const nextBtn = document.getElementById("pageNext");

  const q = getSearchQuery();
  const filtered = getFilteredUsers();
  const isSearching = q.length > 0;
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  if (currentPage > totalPages) currentPage = totalPages;

  const start = isSearching ? 0 : (currentPage - 1) * PAGE_SIZE;
  const end = isSearching ? filtered.length : start + PAGE_SIZE;
  const visible = filtered.slice(start, end);

  if (visible.length === 0) {
    const msg = isSearching ? t("no_search_results") : t("no_users");
    tbody.innerHTML = `<tr><td colspan="5" class="table-message">${msg}</td></tr>`;
  } else {
    const SVG_CHECK = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5ac39a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/></svg>`;
    const SVG_X     = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ff5959" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>`;
    tbody.innerHTML = visible.map((u, i) => {
      let activeCell;
      if (!u.is_active) {
        activeCell = SVG_X;
      } else if (u.has_logged_in) {
        activeCell = SVG_CHECK;
      } else {
        activeCell = "-";
      }
      const fullName = [u.first_name, u.last_name].filter(Boolean).join(" ");
      return `
        <tr>
          <td>${escapeHtml(fullName)}</td>
          <td>${escapeHtml(u.email)}</td>
          <td class="center muted">${u.is_admin ? SVG_CHECK : "-"}</td>
          <td class="center muted">${activeCell}</td>
          <td class="right">
            <button class="btn-icon" data-action="set-admin" data-email="${escapeHtml(u.email)}" data-make-admin="${!u.is_admin}" title="${u.is_admin ? t("btn_remove_admin") : t("btn_make_admin")}" aria-label="${u.is_admin ? t("btn_remove_admin") : t("btn_make_admin")}" >${u.is_admin ? "★" : "☆"}</button>
            <button class="btn-table btn-danger" data-action="delete" data-email="${escapeHtml(u.email)}">${t("btn_delete")}</button>
          </td>
        </tr>`;
    }).join("");
  }

  if (!isSearching && allUsers.length > PAGE_SIZE) {
    paginationEl.style.display = "flex";
    pageInfoEl.innerText = `${t("page_label")} ${currentPage} ${t("of_label")} ${totalPages}`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
  } else {
    paginationEl.style.display = "none";
  }
}

function onUserSearch() {
  currentPage = 1;
  renderUserList();
}

function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(getFilteredUsers().length / PAGE_SIZE));
  currentPage = Math.max(1, Math.min(totalPages, currentPage + delta));
  renderUserList();
}

async function loadUserList() {
  const tbody = document.getElementById("userTableBody");
  const resultEl = document.getElementById("userActionResult");
  setResult(resultEl, "", true);

  if (window.MOCK_USERS) {
    allUsers = window.MOCK_USERS;
    currentPage = 1;
    renderUserList();
    return;
  }

  const r = await fetch("/admin/list-users");
  if (!r.ok) {
    tbody.innerHTML = `<tr><td colspan="5" class="table-error">${t("err_load_users")}</td></tr>`;
    return;
  }
  allUsers = await r.json();
  currentPage = 1;
  renderUserList();
}

async function setAdmin(email, makeAdmin) {
  const resultEl = document.getElementById("userActionResult");
  const fd = new FormData();
  fd.append("email", email);
  fd.append("is_admin", makeAdmin ? "true" : "false");
  const r = await fetch("/admin/set-admin", { method: "POST", body: fd });
  const res = await r.json();
  if (res.ok) {
    setResult(resultEl, `${res.email} ${makeAdmin ? t("admin_granted") : t("admin_removed")}`, true);
    loadUserList();
  } else {
    setResult(resultEl, translateError(res.detail), false);
  }
}

async function deleteUser(email) {
  if (!confirm(`${t("confirm_delete")} ${email}?`)) return;
  const resultEl = document.getElementById("userActionResult");
  const fd = new FormData();
  fd.append("email", email);
  const r = await fetch("/admin/delete-user", { method: "POST", body: fd });
  const res = await r.json();
  if (res.ok) {
    setResult(resultEl, `${res.deleted} ${t("user_deleted")}`, true);
    loadUserList();
  } else {
    setResult(resultEl, translateError(res.detail), false);
  }
}

document.getElementById("addUserForm").onsubmit = async e => {
  e.preventDefault();
  const addResultEl = document.getElementById("addUserResult");
  const fd = new FormData();
  fd.append("first_name", document.getElementById("newUserFirstName").value.trim());
  fd.append("last_name", document.getElementById("newUserLastName").value.trim());
  fd.append("email", document.getElementById("newUserEmail").value);
  fd.append("is_admin", document.getElementById("newUserIsAdmin").checked ? "true" : "false");
  const r = await fetch("/admin/add-user", { method: "POST", body: fd });
  const res = await r.json();
  if (res.ok) {
    setResult(addResultEl, res.email_sent
      ? `${t("user_created")} - ${t("email_sent_ok")} (${res.email})`
      : `${t("user_created")} - ${t("email_sent_fail")} (${res.email})`, true);
    document.getElementById("newUserFirstName").value = "";
    document.getElementById("newUserLastName").value = "";
    document.getElementById("newUserEmail").value = "";
    document.getElementById("newUserIsAdmin").checked = false;
    loadUserList();
  } else {
    setResult(addResultEl, translateError(res.detail), false);
  }
};

loadUserList();
loadPendingRequests();

// Knapper i tabellene bruker data-attributter i stedet for inline onclick,
// slik at e-postadresser aldri tolkes som JavaScript.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const email = btn.dataset.email;
  switch (btn.dataset.action) {
    case "approve":   approveRequest(email); break;
    case "reject":    rejectRequest(email); break;
    case "set-admin": setAdmin(email, btn.dataset.makeAdmin === "true"); break;
    case "delete":    deleteUser(email); break;
  }
});
