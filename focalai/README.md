# FocalAI

An AI task prioritizer. Paste a list of tasks, click **Prioritize**, and Amazon
Nova ranks each one as **High / Medium / Low** with a one-sentence reason.

- No login, no database, no persistence — a single stateless request/response.
- Built for the **AWS Builder Challenge**.

## Architecture (two AWS services)

| Service | Why |
|---|---|
| **AWS Lambda** (with Function URL, auth `NONE`) | One function serves both the HTML frontend on `GET` and the JSON ranking API on `POST`. No API Gateway, no S3, no build step. |
| **Amazon Bedrock** — `amazon.nova-lite-v1:0` | Fast, low-cost foundation model for the ranking prompt. Called from Lambda via `bedrock-runtime.invoke_model`. |

```
                       ┌────────────────────────────┐
Browser ──── HTTPS ───▶│  Lambda Function URL        │
   ▲                   │  ─ GET  → HTML SPA          │
   │       JSON        │  ─ POST → Bedrock (Nova)    │
   └───────────────────│                             │
                       └────────────────────────────┘
```

## Files

- `lambda_function.py` — GET returns the embedded SPA; POST calls Bedrock and returns `{"ranked":[{task, priority, reason}, ...]}`.
- `deploy.sh` — idempotent bash deploy (role + policy + zip + function + Function URL).
- `.env.example` — the env vars the deploy expects. Copy to `.env` and fill in.

## Deploy

Prerequisites: AWS CLI installed and Bedrock **model access for
`amazon.nova-lite-v1:0` enabled in `us-east-1`** (Console → Bedrock → Model access — one-time).

```bash
cp .env.example .env         # fill in AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
cd focalai
bash deploy.sh
```

`deploy.sh` is safe to re-run: it creates the IAM role and Function URL on the
first run and updates the function code on every subsequent run.

## Test

```bash
curl -sS "$URL" | head              # HTML page
curl -sS -X POST "$URL" \
  -H "Content-Type: application/json" \
  -d '{"tasks":"Ship the demo\nBook a haircut\nReply to organizer"}' | jq
```

## Live URL

_Filled in by `deploy.sh` after a successful deployment — see the top of this
file after your first run, or check the deployment log._

## AWS resources this creates

- Lambda function: `focalai-task-prioritizer`
- IAM role: `focalai-task-prioritizer-role`
- Inline policy on the role: `focalai-task-prioritizer-bedrock-invoke`
- Function URL config on the Lambda (auth `NONE`)
- CloudWatch log group `/aws/lambda/focalai-task-prioritizer` (auto-created on first invoke)
