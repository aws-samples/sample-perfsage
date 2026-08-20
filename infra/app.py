#!/usr/bin/env python3
"""PerfSage — Unified CDK Application Entry Point.

Deploys all agent infrastructure in a single CDK app:
  - AgentsStack: TestGen, Executor, Analysis Lambdas + API Gateway
  - StorageStack: S3 results bucket + DynamoDB tables
  - NetworkingStack: VPC + WebSocket API Gateway
  - ExecutionStack: ECS/Fargate cluster + ECR + IAM roles
"""

import os

import aws_cdk as cdk

from stacks.agents_stack import AgentsStack
from stacks.storage_stack import StorageStack
from stacks.networking_stack import NetworkingStack
from stacks.execution_stack import ExecutionStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT", os.environ.get("AWS_ACCOUNT_ID")),
    region=os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1")),
)

env_name = app.node.try_get_context("env") or "dev"

# ── Storage stack (S3 + DynamoDB) ─────────────────────────────────────────────
storage = StorageStack(app, "PerfSageStorage", env=env)

# ── Networking stack (VPC + WebSocket API) ────────────────────────────────────
networking = NetworkingStack(app, "PerfSageNetworking", env=env)

# ── Execution stack (ECS + Fargate) ──────────────────────────────────────────
execution = ExecutionStack(
    app,
    "PerfSageExecution",
    env=env,
    results_bucket=storage.results_bucket,
    test_runs_table=storage.test_runs_table,
    vpc=networking.vpc,
)
execution.add_dependency(storage)
execution.add_dependency(networking)

# ── Agents stack (all 3 Lambda agents + API Gateway) ─────────────────────────
# TestGen runs on Fargate — pass private subnets (Networking) + task SG (Execution)
# as cross-stack references so they stay in sync and survive redeploys.
agents = AgentsStack(
    app,
    f"PerfSage-Agents-{env_name}",
    env=env,
    env_name=env_name,
    fargate_subnets=",".join([s.subnet_id for s in networking.vpc.private_subnets]),
    fargate_security_groups=execution.task_security_group.security_group_id,
)
agents.add_dependency(storage)
agents.add_dependency(networking)
agents.add_dependency(execution)

# ── Tags ──────────────────────────────────────────────────────────────────────
cdk.Tags.of(app).add("Project", "PerfSage")
cdk.Tags.of(app).add("Environment", env_name)
cdk.Tags.of(app).add("ManagedBy", "CDK")

app.synth()
