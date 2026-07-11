"""
Idempotent boto3-based deployer for FocalAI.

Works when the AWS CLI isn't installed. Reads creds from environment
or from a .env file next to this script (same schema as .env.example).

Usage: python deploy.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_dotenv() -> None:
    for candidate in (HERE / ".env", HERE.parent / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
        print(f"→ loaded {candidate}")
        return
    print("→ no .env found; using environment only")


load_dotenv()

if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
    sys.exit("ERROR: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not set.")

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
FN_NAME = os.environ.get("LAMBDA_FUNCTION_NAME", "focalai-task-prioritizer")
ROLE_NAME = f"{FN_NAME}-role"
POLICY_NAME = f"{FN_NAME}-bedrock-invoke"

import boto3
from botocore.exceptions import ClientError

session = boto3.Session(region_name=REGION)
sts = session.client("sts")
iam = session.client("iam")
lam = session.client("lambda")

account_id = sts.get_caller_identity()["Account"]
model_arn = f"arn:aws:bedrock:{REGION}::foundation-model/{MODEL_ID}"

print(f"→ account:  {account_id}")
print(f"→ region:   {REGION}")
print(f"→ function: {FN_NAME}")
print(f"→ model:    {MODEL_ID}")


# ---------- IAM role ---------------------------------------------------------
TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

INLINE = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "bedrock:InvokeModel",
            "Resource": model_arn,
        }
    ],
}

try:
    iam.get_role(RoleName=ROLE_NAME)
    print("✓ role exists")
    role_created = False
except ClientError as e:
    if e.response["Error"]["Code"] != "NoSuchEntity":
        raise
    iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(TRUST),
        Description="FocalAI Lambda execution role",
    )
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("→ role created, waiting 12s for propagation")
    time.sleep(12)
    role_created = True

iam.put_role_policy(
    RoleName=ROLE_NAME,
    PolicyName=POLICY_NAME,
    PolicyDocument=json.dumps(INLINE),
)
print("✓ bedrock invoke policy attached")

role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]


# ---------- Zip package ------------------------------------------------------
def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(HERE / "lambda_function.py", "lambda_function.py")
    return buf.getvalue()


zip_bytes = build_zip()
print(f"✓ package built ({len(zip_bytes)} bytes)")


# ---------- Lambda function --------------------------------------------------
env_vars = {"BEDROCK_MODEL_ID": MODEL_ID}

def wait_updated():
    lam.get_waiter("function_updated").wait(FunctionName=FN_NAME)


try:
    lam.get_function(FunctionName=FN_NAME)
    exists = True
except ClientError as e:
    if e.response["Error"]["Code"] != "ResourceNotFoundException":
        raise
    exists = False

if exists:
    print("→ updating existing function code")
    lam.update_function_code(FunctionName=FN_NAME, ZipFile=zip_bytes)
    wait_updated()
    lam.update_function_configuration(
        FunctionName=FN_NAME,
        Runtime="python3.12",
        Handler="lambda_function.lambda_handler",
        Timeout=25,
        MemorySize=512,
        Environment={"Variables": env_vars},
    )
    wait_updated()
else:
    print("→ creating function")
    # Fresh role may need extra time to be assumable.
    for attempt in range(6):
        try:
            lam.create_function(
                FunctionName=FN_NAME,
                Runtime="python3.12",
                Handler="lambda_function.lambda_handler",
                Role=role_arn,
                Code={"ZipFile": zip_bytes},
                Timeout=25,
                MemorySize=512,
                Environment={"Variables": env_vars},
            )
            break
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidParameterValueException" and "role" in str(e).lower():
                print(f"  role not ready yet, retrying ({attempt + 1}/6)…")
                time.sleep(5)
                continue
            raise
    else:
        sys.exit("ERROR: role never became assumable")
    lam.get_waiter("function_active").wait(FunctionName=FN_NAME)

print("✓ function deployed")


# ---------- Function URL -----------------------------------------------------
try:
    url_cfg = lam.get_function_url_config(FunctionName=FN_NAME)
    url = url_cfg["FunctionUrl"]
    print("✓ function URL exists")
except ClientError as e:
    if e.response["Error"]["Code"] != "ResourceNotFoundException":
        raise
    url_cfg = lam.create_function_url_config(
        FunctionName=FN_NAME,
        AuthType="NONE",
        Cors={
            "AllowOrigins": ["*"],
            "AllowMethods": ["GET", "POST"],
            "AllowHeaders": ["content-type"],
        },
    )
    url = url_cfg["FunctionUrl"]
    print("→ function URL created")

try:
    lam.add_permission(
        FunctionName=FN_NAME,
        StatementId="FunctionURLAllowPublic",
        Action="lambda:InvokeFunctionUrl",
        Principal="*",
        FunctionUrlAuthType="NONE",
    )
    print("✓ public invoke permission added")
except ClientError as e:
    if e.response["Error"]["Code"] == "ResourceConflictException":
        print("✓ public invoke permission already present")
    else:
        raise

print()
print("=" * 60)
print(f"  Function URL: {url}")
print("=" * 60)
