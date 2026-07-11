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
- `deploy.py` — idempotent boto3 deploy (no AWS CLI required).
- `deploy.sh` — idempotent bash deploy (uses AWS CLI). Same behaviour as `deploy.py`.
- `.env.example` — the env vars the deploy expects. Copy to `.env` and fill in.

## Deploy

Prerequisites: Bedrock **model access for `amazon.nova-lite-v1:0` enabled in
`us-east-1`** (Console → Bedrock → Model access — one-time).

Copy `.env.example` → `.env` and fill in your AWS keys, then pick one:

```bash
# Option A: Python + boto3 (no AWS CLI required)
cd focalai
pip install boto3
python deploy.py

# Option B: bash + AWS CLI
cd focalai
bash deploy.sh
```

Both scripts are idempotent — safe to re-run. They create the IAM role and
Function URL on the first run and update the function code on every run after.

## Test

```bash
curl -sS "$URL" | head              # HTML page
curl -sS -X POST "$URL" \
  -H "Content-Type: application/json" \
  -d '{"tasks":"Ship the demo\nBook a haircut\nReply to organizer"}' | jq
```

## Live URL

**https://zxci5naldqxn66iiywu53bofga0wpthf.lambda-url.us-east-1.on.aws/**

Open it in a browser to use the app, or:

```bash
curl -sS https://zxci5naldqxn66iiywu53bofga0wpthf.lambda-url.us-east-1.on.aws/ | head
curl -sS -X POST https://zxci5naldqxn66iiywu53bofga0wpthf.lambda-url.us-east-1.on.aws/ \
  -H "Content-Type: application/json" \
  -d '{"tasks":"Ship the demo\nBook a haircut\nReply to organizer"}'
```

## AWS resources this creates

- Lambda function: `focalai-task-prioritizer` (region `us-east-1`, python 3.12, 25s timeout, 512 MB)
- IAM role: `focalai-task-prioritizer-role`
  - Attached: `AWSLambdaBasicExecutionRole` (AWS-managed)
  - Inline policy: `focalai-task-prioritizer-bedrock-invoke` → `bedrock:InvokeModel` on `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`
- Function URL config on the Lambda (auth `NONE`, CORS `*`)
- Two statements on the function's resource-based policy: `FunctionURLAllowPublic` and `FunctionInvokeAllowPublic` (both `Principal:*`)
- CloudWatch log group `/aws/lambda/focalai-task-prioritizer` (auto-created on first invoke)
