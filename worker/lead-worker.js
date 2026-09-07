/**
 * Red Sea Business Launch — lead receiver (Cloudflare Worker)
 * ----------------------------------------------------------
 * POST /            → stores a qualification-form submission
 * GET  /?key=SECRET → shows stored leads as a simple table
 * GET  /export?key=SECRET → downloads all leads as CSV
 *
 * Bindings to create in the Cloudflare dashboard:
 *   KV namespace binding : LEADS
 *   Secret               : ADMIN_KEY      (any long random string you choose)
 *   Variable             : ALLOWED_ORIGIN (e.g. https://deals.redseaglobal.com)
 */

const MAX_BODY = 20_000;          // bytes — rejects oversized payloads
const FIELD_MAX = 2_000;          // chars per field
const RATE_WINDOW = 60;           // seconds
const RATE_MAX = 5;               // submissions per IP per window

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = env.ALLOWED_ORIGIN || "*";

    if (request.method === "OPTIONS") return new Response(null, { headers: cors(origin) });

    if (request.method === "POST") return handlePost(request, env, origin);

    if (request.method === "GET") {
      if (!env.ADMIN_KEY || url.searchParams.get("key") !== env.ADMIN_KEY) {
        return new Response("Not found", { status: 404 });
      }
      const key = url.searchParams.get("key");
      return url.pathname === "/export" ? exportCsv(env) : listLeads(env, key);
    }

    return new Response("Method not allowed", { status: 405 });
  }
};

/* ---------------- submission ---------------- */

async function handlePost(request, env, origin) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";

  if (!(await underRateLimit(env, ip))) {
    return json({ ok: false, error: "rate_limited" }, 429, origin);
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ ok: false, error: "too_large" }, 413, origin);

  let data;
  try { data = JSON.parse(raw); } catch { return json({ ok: false, error: "bad_json" }, 400, origin); }
  if (typeof data !== "object" || data === null) return json({ ok: false, error: "bad_body" }, 400, origin);

  // Honeypot: bots fill hidden fields humans never see.
  if (data.website) return json({ ok: true }, 200, origin);

  const clean = {};
  for (const [k, v] of Object.entries(data)) {
    if (typeof k !== "string" || k.length > 60) continue;
    clean[k] = String(v ?? "").slice(0, FIELD_MAX);
  }

  const record = {
    ...clean,
    _ip: ip,
    _country: request.headers.get("CF-IPCountry") || "",
    _ua: (request.headers.get("User-Agent") || "").slice(0, 300),
    _receivedAt: new Date().toISOString()
  };

  const id = `lead:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`;
  await env.LEADS.put(id, JSON.stringify(record));

  return json({ ok: true, id }, 200, origin);
}

async function underRateLimit(env, ip) {
  const key = `rl:${ip}`;
  const current = parseInt((await env.LEADS.get(key)) || "0", 10);
  if (current >= RATE_MAX) return false;
  await env.LEADS.put(key, String(current + 1), { expirationTtl: RATE_WINDOW });
  return true;
}

/* ---------------- admin views ---------------- */

async function readAll(env) {
  const list = await env.LEADS.list({ prefix: "lead:" });
  const rows = await Promise.all(
    list.keys.map(async k => { try { return JSON.parse(await env.LEADS.get(k.name)); } catch { return null; } })
  );
  return rows.filter(Boolean).sort((a, b) => (b._receivedAt || "").localeCompare(a._receivedAt || ""));
}

async function listLeads(env, key) {
  const rows = await readAll(env);
  const cols = [...new Set(rows.flatMap(Object.keys))];
  const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const html = `<!doctype html><meta charset="utf-8"><title>Leads (${rows.length})</title>
<style>body{font:14px system-ui;margin:24px;background:#F7F3EC;color:#12212F}
h1{font-size:1.2rem}table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #E3E8EE;padding:7px 9px;text-align:left;vertical-align:top;max-width:280px}
th{background:#062B57;color:#fff;position:sticky;top:0}tr:nth-child(even){background:#FAFBFC}
a{color:#0E5AA5}</style>
<h1>Leads — ${rows.length} &nbsp;<a href="/export?key=${encodeURIComponent(key)}">download CSV</a></h1>
<table><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr>
${rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("")}</table>`;
  return new Response(html, { headers: { "Content-Type": "text/html;charset=utf-8" } });
}

async function exportCsv(env) {
  const rows = await readAll(env);
  const cols = [...new Set(rows.flatMap(Object.keys))];
  const cell = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = [cols.join(","), ...rows.map(r => cols.map(c => cell(r[c])).join(","))].join("\n");
  return new Response("﻿" + csv, {
    headers: {
      "Content-Type": "text/csv;charset=utf-8",
      "Content-Disposition": `attachment; filename="rsbl-leads-${new Date().toISOString().slice(0, 10)}.csv"`
    }
  });
}

/* ---------------- helpers ---------------- */

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400"
  };
}

function json(obj, status, origin) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...cors(origin) }
  });
}
