"""
FocalAI — AI task prioritizer.

Single Lambda behind a Function URL:
  - GET  → returns the SPA (HTML/CSS/JS embedded below)
  - POST → { "tasks": "<one per line>" } → Bedrock (Nova) → { "ranked": [...] }
"""

import json
import os
import re
import logging

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)

HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FocalAI — prioritize your tasks</title>
<style>
  :root{
    --bg:#0b0d12; --panel:#12151d; --panel-2:#171b25; --border:#232838;
    --text:#e7ebf3; --muted:#8a92a6; --accent:#7c5cff; --accent-2:#22d3ee;
    --high:#ef4444; --med:#f59e0b; --low:#22c55e;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,Segoe UI,Roboto,Inter,Helvetica,Arial,sans-serif}
  .wrap{max-width:780px;margin:0 auto;padding:40px 20px 80px}
  header{display:flex;align-items:center;gap:12px;margin-bottom:24px}
  .logo{width:36px;height:36px;border-radius:10px;
    background:linear-gradient(135deg,var(--accent),var(--accent-2));
    display:grid;place-items:center;font-weight:700;color:#0b0d12}
  h1{font-size:22px;margin:0;letter-spacing:-.01em}
  p.sub{color:var(--muted);margin:4px 0 24px;font-size:14px}
  .card{background:var(--panel);border:1px solid var(--border);
    border-radius:14px;padding:18px}
  label{display:block;font-size:13px;color:var(--muted);margin-bottom:8px}
  textarea{width:100%;min-height:180px;background:var(--panel-2);color:var(--text);
    border:1px solid var(--border);border-radius:10px;padding:12px;
    font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;resize:vertical;outline:none}
  textarea:focus{border-color:var(--accent)}
  .row{display:flex;justify-content:space-between;align-items:center;margin-top:12px;gap:12px}
  button{background:linear-gradient(135deg,var(--accent),var(--accent-2));
    color:#0b0d12;border:0;padding:11px 18px;border-radius:10px;
    font-weight:600;cursor:pointer;font-size:14px}
  button:disabled{opacity:.6;cursor:progress}
  .hint{color:var(--muted);font-size:12px}
  .results{margin-top:24px;display:flex;flex-direction:column;gap:10px}
  .item{background:var(--panel);border:1px solid var(--border);
    border-radius:12px;padding:14px 16px;display:grid;grid-template-columns:auto 1fr;
    gap:14px;align-items:flex-start}
  .pill{font-size:11px;font-weight:700;padding:5px 9px;border-radius:999px;
    letter-spacing:.04em;text-transform:uppercase;white-space:nowrap}
  .pill.HIGH{background:rgba(239,68,68,.15);color:var(--high);
    border:1px solid rgba(239,68,68,.35)}
  .pill.MEDIUM{background:rgba(245,158,11,.13);color:var(--med);
    border:1px solid rgba(245,158,11,.35)}
  .pill.LOW{background:rgba(34,197,94,.13);color:var(--low);
    border:1px solid rgba(34,197,94,.35)}
  .task{font-weight:600;margin:0 0 4px}
  .reason{color:var(--muted);font-size:13px;margin:0}
  .err{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.35);
    color:#fca5a5;padding:12px;border-radius:10px;font-size:13px}
  footer{margin-top:36px;color:var(--muted);font-size:12px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">F</div>
    <div>
      <h1>FocalAI</h1>
      <p class="sub">Paste your tasks. Get them ranked by urgency &amp; impact.</p>
    </div>
  </header>

  <div class="card">
    <label for="tasks">Your tasks (one per line)</label>
    <textarea id="tasks" placeholder="Ship the demo build&#10;Reply to hackathon organizer&#10;Refactor the settings screen&#10;Book a haircut&#10;Prep slides for Monday's review"></textarea>
    <div class="row">
      <span class="hint">No login. Nothing stored. Powered by Amazon Bedrock (Nova).</span>
      <button id="go">Prioritize</button>
    </div>
  </div>

  <div id="results" class="results"></div>
  <footer>Built for the AWS Builder Challenge · Lambda + Bedrock</footer>
</div>

<script>
const btn = document.getElementById('go');
const ta  = document.getElementById('tasks');
const out = document.getElementById('results');

const order = { HIGH: 0, MEDIUM: 1, LOW: 2 };

btn.addEventListener('click', async () => {
  const tasks = ta.value.trim();
  out.innerHTML = '';
  if (!tasks) {
    out.innerHTML = '<div class="err">Enter at least one task.</div>';
    return;
  }
  btn.disabled = true; btn.textContent = 'Thinking…';
  try {
    const r = await fetch(window.location.pathname, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tasks })
    });
    const data = await r.json();
    if (!r.ok || data.error) {
      out.innerHTML = '<div class="err">' + escapeHtml(data.error || 'Something went wrong.') + '</div>';
      return;
    }
    const ranked = (data.ranked || []).slice().sort(
      (a,b) => (order[(a.priority||'').toUpperCase()] ?? 9) - (order[(b.priority||'').toUpperCase()] ?? 9)
    );
    if (!ranked.length) {
      out.innerHTML = '<div class="err">No tasks came back.</div>'; return;
    }
    out.innerHTML = ranked.map(item => {
      const p = (item.priority || '').toUpperCase();
      const cls = ['HIGH','MEDIUM','LOW'].includes(p) ? p : 'MEDIUM';
      return `<div class="item">
        <span class="pill ${cls}">${cls}</span>
        <div>
          <p class="task">${escapeHtml(item.task || '')}</p>
          <p class="reason">${escapeHtml(item.reason || '')}</p>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    out.innerHTML = '<div class="err">Network error: ' + escapeHtml(e.message) + '</div>';
  } finally {
    btn.disabled = false; btn.textContent = 'Prioritize';
  }
});

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
</script>
</body>
</html>
"""


def _resp(status, body, content_type="application/json"):
    if content_type == "application/json" and not isinstance(body, str):
        body = json.dumps(body)
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": body,
    }


def _method(event):
    ctx = event.get("requestContext") or {}
    return (
        (ctx.get("http") or {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()


def _prompt(tasks_text):
    return (
        "You are a productivity assistant. Rank the following tasks by "
        "urgency and impact. For each task, decide a priority of exactly "
        "HIGH, MEDIUM, or LOW, and give a short one-sentence reason.\n\n"
        "Return ONLY a JSON array. Each element must be an object with keys "
        '"task", "priority", "reason". No prose, no code fences, no keys other than those.\n\n'
        "Tasks:\n" + tasks_text
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _extract_json_array(text):
    text = _FENCE.sub("", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("model did not return a JSON array")
    return json.loads(text[start:end + 1])


def _call_bedrock(tasks_text):
    payload = {
        "messages": [
            {"role": "user", "content": [{"text": _prompt(tasks_text)}]}
        ],
        "inferenceConfig": {"maxTokens": 1024, "temperature": 0.2, "topP": 0.9},
    }
    r = _bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(r["body"].read())
    # Nova response: {"output": {"message": {"content": [{"text": "..."}]}}, ...}
    text = body["output"]["message"]["content"][0]["text"]
    return _extract_json_array(text)


def _handle_post(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return _resp(400, {"error": "Could not decode request body."})
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _resp(400, {"error": "Request body must be JSON."})

    tasks_text = (payload.get("tasks") or "").strip()
    if not tasks_text:
        return _resp(400, {"error": "Provide 'tasks' as a non-empty string (one task per line)."})

    try:
        ranked = _call_bedrock(tasks_text)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        log.error("Bedrock ClientError: %s / %s", code, msg)
        if code in ("AccessDeniedException", "ValidationException") and "model" in msg.lower():
            return _resp(
                403,
                {"error": "Bedrock model access is not enabled for this model in this "
                          "region. Enable it in the AWS Console → Bedrock → Model access."},
            )
        return _resp(502, {"error": f"Bedrock error: {code or 'Unknown'}"})
    except ValueError as e:
        log.error("Model output parse error: %s", e)
        return _resp(502, {"error": "The model did not return valid JSON. Please try again."})
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error")
        return _resp(500, {"error": "Unexpected server error."})

    cleaned = []
    for item in ranked if isinstance(ranked, list) else []:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip()
        priority = str(item.get("priority", "")).strip().upper()
        reason = str(item.get("reason", "")).strip()
        if not task:
            continue
        if priority not in ("HIGH", "MEDIUM", "LOW"):
            priority = "MEDIUM"
        cleaned.append({"task": task, "priority": priority, "reason": reason})

    if not cleaned:
        return _resp(502, {"error": "The model returned an empty ranking."})
    return _resp(200, {"ranked": cleaned})


def lambda_handler(event, context):
    method = _method(event)
    if method == "OPTIONS":
        return _resp(204, "", content_type="text/plain")
    if method == "GET":
        return _resp(200, HTML_PAGE, content_type="text/html; charset=utf-8")
    if method == "POST":
        return _handle_post(event)
    return _resp(405, {"error": f"Method {method} not allowed."})
