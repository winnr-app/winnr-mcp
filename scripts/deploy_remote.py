#!/usr/bin/env python3
"""Deploy the remote Winnr MCP server (mcp.winnr.app) to AWS Lambda.

What it manages (idempotent, all in the `winnr` account, us-east-1):
  - DynamoDB table  winnr-mcp-oauth          (pk, TTL on `ttl`)
  - SSM parameter   /winnr/mcp/CONFIRM_SECRET (created once, random)
  - Lambda layer    winnr-mcp-remote-deps    (arm64 / python3.12 wheels)
  - Lambda          winnr-mcp-remote         (code = src/winnr_mcp)
  - HTTP API        winnr-mcp-remote → $default → Lambda
  - ACM cert + API Gateway custom domain + Route53 alias for mcp.winnr.app

Run:  python scripts/deploy_remote.py [--skip-layer] [--skip-tests]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "remote"
REGION = "us-east-1"
PROFILE = "winnr"
RUNTIME = "python3.12"
ARCH = "arm64"
FUNCTION = "winnr-mcp-remote"
LAYER = "winnr-mcp-remote-deps"
TABLE = "winnr-mcp-oauth"
API_NAME = "winnr-mcp-remote"
DOMAIN = "mcp.winnr.app"
HOSTED_ZONE = "winnr.app."
ROLE = "winnr-mcp-remote-role"
SOURCE_LAMBDA_FOR_FIREBASE = "winnr-public-api"  # reuse its FIREBASE_SERVICE_ACCOUNT env var
MEMORY_MB = 1024
TIMEOUT_S = 29  # API Gateway HTTP API hard limit is 30s
DEPS = ["mcp>=1.26,<2", "httpx>=0.24", "mangum>=0.17", "firebase-admin>=6.0"]  # boto3 is in the runtime

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
lam = session.client("lambda")
iam = session.client("iam")
ddb = session.client("dynamodb")
ssm = session.client("ssm")
apigw = session.client("apigatewayv2")
acm = session.client("acm")
r53 = session.client("route53")
sts = session.client("sts")


def log(msg: str) -> None:
    print(msg, flush=True)


def run_tests() -> None:
    log("🧪 pytest")
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)


def build_layer() -> str:
    log("📦 building layer (arm64 / python3.12 wheels)")
    target = BUILD / "layer" / "python"
    shutil.rmtree(BUILD / "layer", ignore_errors=True)
    target.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--platform", "manylinux2014_aarch64",
         "--python-version", "3.12", "--only-binary=:all:", "--target", str(target), *DEPS],
        check=True,
    )
    for junk in target.rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    zip_path = BUILD / "layer.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(target.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(BUILD / "layer"))
    size_mb = zip_path.stat().st_size / 1e6
    log(f"   layer.zip {size_mb:.1f} MB")
    # Direct upload caps at 50 MB; go via S3 above ~45 MB.
    if size_mb > 45:
        s3 = session.client("s3")
        account = sts.get_caller_identity()["Account"]
        bucket = f"winnr-lambda-layers-{account}"
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:  # noqa: BLE001
            s3.create_bucket(Bucket=bucket)
        key = f"layers/{LAYER}/{int(time.time())}.zip"
        s3.upload_file(str(zip_path), bucket, key)
        content = {"S3Bucket": bucket, "S3Key": key}
    else:
        content = {"ZipFile": zip_path.read_bytes()}
    resp = lam.publish_layer_version(
        LayerName=LAYER, Content=content,
        CompatibleRuntimes=[RUNTIME], CompatibleArchitectures=[ARCH],
    )
    log(f"   ✅ {resp['LayerVersionArn']}")
    return resp["LayerVersionArn"]


def latest_layer_arn() -> str:
    versions = lam.list_layer_versions(LayerName=LAYER)["LayerVersions"]
    if not versions:
        raise SystemExit("no layer published yet — run without --skip-layer")
    return versions[0]["LayerVersionArn"]


def build_code() -> Path:
    log("📦 packaging code")
    zip_path = BUILD / "code.zip"
    BUILD.mkdir(parents=True, exist_ok=True)
    src = ROOT / "src" / "winnr_mcp"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*.py")):
            z.write(f, f.relative_to(ROOT / "src"))
        # importlib.metadata needs a dist-info for __version__
        version = _version()
        z.writestr(f"winnr_mcp-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: winnr-mcp\nVersion: {version}\n")
        z.writestr(f"winnr_mcp-{version}.dist-info/RECORD", "")
    log(f"   code.zip {zip_path.stat().st_size / 1e3:.0f} KB (winnr-mcp {version})")
    return zip_path


def _version() -> str:
    for line in (ROOT / "pyproject.toml").read_text().splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("version not found in pyproject.toml")


def ensure_table() -> None:
    try:
        ddb.describe_table(TableName=TABLE)
        log(f"🗄  table {TABLE} exists")
    except ddb.exceptions.ResourceNotFoundException:
        log(f"🗄  creating table {TABLE}")
        ddb.create_table(
            TableName=TABLE,
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
            SSESpecification={"Enabled": True},
        )
        ddb.get_waiter("table_exists").wait(TableName=TABLE)
    ttl = ddb.describe_time_to_live(TableName=TABLE)["TimeToLiveDescription"]
    if ttl.get("TimeToLiveStatus") not in ("ENABLED", "ENABLING"):
        ddb.update_time_to_live(TableName=TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"})
        log("   TTL enabled on `ttl`")


def ensure_secret() -> None:
    try:
        ssm.get_parameter(Name="/winnr/mcp/CONFIRM_SECRET", WithDecryption=True)
        log("🔑 /winnr/mcp/CONFIRM_SECRET exists")
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name="/winnr/mcp/CONFIRM_SECRET", Type="SecureString", Value=secrets.token_urlsafe(48),
                          Description="HMAC secret for winnr-mcp purchase confirmation tokens")
        log("🔑 created /winnr/mcp/CONFIRM_SECRET")


def ensure_role() -> str:
    account = sts.get_caller_identity()["Account"]
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        log(f"👤 role {ROLE} exists")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Description="winnr-mcp-remote Lambda")["Role"]["Arn"]
        iam.attach_role_policy(RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        log(f"👤 created role {ROLE}")
        time.sleep(10)  # IAM propagation
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
             "Resource": f"arn:aws:dynamodb:{REGION}:{account}:table/{TABLE}"},
            {"Effect": "Allow", "Action": ["ssm:GetParameter"],
             "Resource": [f"arn:aws:ssm:{REGION}:{account}:parameter/winnr/mcp/*",
                          f"arn:aws:ssm:{REGION}:{account}:parameter/winnr/frontend/FIREBASE_SERVICE_ACCOUNT_KEY"]},
        ],
    }
    iam.put_role_policy(RoleName=ROLE, PolicyName="winnr-mcp-remote-access", PolicyDocument=json.dumps(policy))
    return arn


def firebase_env() -> str:
    cfg = lam.get_function_configuration(FunctionName=SOURCE_LAMBDA_FOR_FIREBASE)
    value = cfg.get("Environment", {}).get("Variables", {}).get("FIREBASE_SERVICE_ACCOUNT")
    if not value:
        raise SystemExit(f"{SOURCE_LAMBDA_FOR_FIREBASE} has no FIREBASE_SERVICE_ACCOUNT env var to reuse")
    return value


def deploy_lambda(code_zip: Path, layer_arn: str, role_arn: str) -> str:
    env = {
        "WINNR_MCP_TABLE": TABLE,
        "WINNR_MCP_ISSUER": f"https://{DOMAIN}",
        "WINNR_DASHBOARD_URL": "https://app.winnr.app",
        "WINNR_API_URL": "https://api.winnr.app",
        "FIREBASE_SERVICE_ACCOUNT": firebase_env(),
    }
    try:
        lam.get_function_configuration(FunctionName=FUNCTION)
        log(f"λ  updating {FUNCTION}")
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code_zip.read_bytes(), Architectures=[ARCH])
        lam.get_waiter("function_updated").wait(FunctionName=FUNCTION)
        lam.update_function_configuration(
            FunctionName=FUNCTION, Runtime=RUNTIME, Handler="winnr_mcp.remote.lambda_handler.handler",
            Role=role_arn, Timeout=TIMEOUT_S, MemorySize=MEMORY_MB, Layers=[layer_arn],
            Environment={"Variables": env},
        )
        lam.get_waiter("function_updated").wait(FunctionName=FUNCTION)
    except lam.exceptions.ResourceNotFoundException:
        log(f"λ  creating {FUNCTION}")
        lam.create_function(
            FunctionName=FUNCTION, Runtime=RUNTIME, Role=role_arn, Handler="winnr_mcp.remote.lambda_handler.handler",
            Code={"ZipFile": code_zip.read_bytes()}, Timeout=TIMEOUT_S, MemorySize=MEMORY_MB,
            Architectures=[ARCH], Layers=[layer_arn], Environment={"Variables": env},
            Description="Winnr remote MCP server (Streamable HTTP + OAuth 2.1)",
        )
        lam.get_waiter("function_active").wait(FunctionName=FUNCTION)
    arn = lam.get_function_configuration(FunctionName=FUNCTION)["FunctionArn"]
    log(f"   ✅ {arn}")
    return arn


def ensure_api(lambda_arn: str) -> str:
    apis = [a for a in apigw.get_apis()["Items"] if a["Name"] == API_NAME]
    if apis:
        api = apis[0]
        log(f"🌐 API {API_NAME} exists ({api['ApiId']})")
    else:
        api = apigw.create_api(Name=API_NAME, ProtocolType="HTTP", Target=lambda_arn, Description="Winnr remote MCP")
        log(f"🌐 created API {api['ApiId']}")
    api_id = api["ApiId"]
    # integration + $default route
    integrations = apigw.get_integrations(ApiId=api_id)["Items"]
    integ = next((i for i in integrations if i.get("IntegrationUri") == lambda_arn), None)
    if not integ:
        integ = apigw.create_integration(ApiId=api_id, IntegrationType="AWS_PROXY", IntegrationUri=lambda_arn,
                                         PayloadFormatVersion="2.0", TimeoutInMillis=29000)
    routes = apigw.get_routes(ApiId=api_id)["Items"]
    if not any(r["RouteKey"] == "$default" for r in routes):
        apigw.create_route(ApiId=api_id, RouteKey="$default", Target=f"integrations/{integ['IntegrationId']}")
    stages = apigw.get_stages(ApiId=api_id)["Items"]
    if not any(s["StageName"] == "$default" for s in stages):
        apigw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)
    account = sts.get_caller_identity()["Account"]
    try:
        lam.add_permission(FunctionName=FUNCTION, StatementId="apigw-invoke", Action="lambda:InvokeFunction",
                           Principal="apigateway.amazonaws.com", SourceArn=f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*")
    except lam.exceptions.ResourceConflictException:
        pass
    return api_id


def ensure_certificate() -> str:
    certs = acm.list_certificates(CertificateStatuses=["ISSUED", "PENDING_VALIDATION"])["CertificateSummaryList"]
    match = next((c for c in certs if c["DomainName"] == DOMAIN), None)
    zone_id = _zone_id()
    if not match:
        log(f"🔒 requesting ACM certificate for {DOMAIN}")
        arn = acm.request_certificate(DomainName=DOMAIN, ValidationMethod="DNS")["CertificateArn"]
        time.sleep(5)
    else:
        arn = match["CertificateArn"]
    desc = acm.describe_certificate(CertificateArn=arn)["Certificate"]
    for opt in desc.get("DomainValidationOptions", []):
        rr = opt.get("ResourceRecord")
        if rr:
            r53.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": [{
                "Action": "UPSERT", "ResourceRecordSet": {"Name": rr["Name"], "Type": rr["Type"], "TTL": 300,
                                                         "ResourceRecords": [{"Value": rr["Value"]}]}}]})
    if desc["Status"] != "ISSUED":
        log("   waiting for DNS validation…")
        acm.get_waiter("certificate_validated").wait(CertificateArn=arn, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    log(f"   ✅ certificate {arn}")
    return arn


def _zone_id() -> str:
    zones = r53.list_hosted_zones_by_name(DNSName=HOSTED_ZONE)["HostedZones"]
    zone = next((z for z in zones if z["Name"] == HOSTED_ZONE), None)
    if not zone:
        raise SystemExit(f"hosted zone {HOSTED_ZONE} not found")
    return zone["Id"].split("/")[-1]


def ensure_domain(api_id: str, cert_arn: str) -> None:
    try:
        dn = apigw.get_domain_name(DomainName=DOMAIN)
        log(f"🌍 custom domain {DOMAIN} exists")
    except apigw.exceptions.NotFoundException:
        log(f"🌍 creating custom domain {DOMAIN}")
        dn = apigw.create_domain_name(DomainName=DOMAIN, DomainNameConfigurations=[{
            "CertificateArn": cert_arn, "EndpointType": "REGIONAL", "SecurityPolicy": "TLS_1_2"}])
    mappings = apigw.get_api_mappings(DomainName=DOMAIN)["Items"]
    if not any(m["ApiId"] == api_id for m in mappings):
        apigw.create_api_mapping(DomainName=DOMAIN, ApiId=api_id, Stage="$default")
    cfg = dn["DomainNameConfigurations"][0]
    target, zone = cfg["ApiGatewayDomainName"], cfg["HostedZoneId"]
    r53.change_resource_record_sets(HostedZoneId=_zone_id(), ChangeBatch={"Changes": [{
        "Action": "UPSERT", "ResourceRecordSet": {"Name": f"{DOMAIN}.", "Type": "A",
                                                 "AliasTarget": {"HostedZoneId": zone, "DNSName": target, "EvaluateTargetHealth": False}}}]})
    log(f"   ✅ {DOMAIN} → {target}")


def verify() -> None:
    import urllib.request

    log("🔎 verifying")
    for path, expect in (("/healthz", '"ok"'), ("/.well-known/oauth-authorization-server", "authorization_endpoint")):
        url = f"https://{DOMAIN}{path}"
        for attempt in range(12):
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    body = resp.read().decode()
                if expect in body:
                    log(f"   ✅ {path}")
                    break
                raise RuntimeError(body[:200])
            except Exception as exc:  # noqa: BLE001
                if attempt == 11:
                    raise SystemExit(f"   ❌ {url}: {exc}")
                time.sleep(10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-layer", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-domain", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("AWS_PROFILE", PROFILE)
    if not args.skip_tests:
        run_tests()
    ensure_table()
    ensure_secret()
    role = ensure_role()
    layer = latest_layer_arn() if args.skip_layer else build_layer()
    code = build_code()
    fn_arn = deploy_lambda(code, layer, role)
    api_id = ensure_api(fn_arn)
    if not args.skip_domain:
        cert = ensure_certificate()
        ensure_domain(api_id, cert)
        verify()
    log("✅ done")


if __name__ == "__main__":
    main()
