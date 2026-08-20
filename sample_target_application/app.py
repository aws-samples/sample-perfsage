#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.ecommerce_api_stack import EcommerceApiStack

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

app = cdk.App()

account = os.environ.get("AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"

if not account:
    raise SystemExit("No AWS account. Set AWS_ACCOUNT_ID or ensure credentials are active.")

env_name = app.node.try_get_context("env") or os.environ.get("ENV_NAME", "dev")
env = cdk.Environment(account=account, region=region)

EcommerceApiStack(app, f"PerfSage-EcommerceApi-{env_name}", env=env, env_name=env_name)

cdk.Tags.of(app).add("Project", "PerfSage")
cdk.Tags.of(app).add("Component", "EcommerceApi")
cdk.Tags.of(app).add("Environment", env_name)

app.synth()
