"""Agents Stack — All three PerfSage agent Lambdas + unified API Gateway.

Deploys:
  - TestGen Agent Lambda
  - Executor Agent Lambda
  - Analysis Agent Lambda
  - Shared API Gateway with per-agent routes
"""

from aws_cdk import (
    BundlingOptions,
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_ecr as ecr,
    aws_ecs as ecs,
)
from constructs import Construct


class AgentsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str = "dev",
        fargate_subnets: str = "",
        fargate_security_groups: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        is_prod = env_name == "prod"
        removal_policy = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY

        # ── DynamoDB: Job State ────────────────────────────────────────────────
        job_table = dynamodb.Table(
            self, "JobTable",
            partition_key=dynamodb.Attribute(
                name="job_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=removal_policy,
        )

        # ── S3: Spec Storage ──────────────────────────────────────────────────
        spec_bucket = s3.Bucket(
            self, "SpecBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=removal_policy,
            auto_delete_objects=not is_prod,
        )

        # ══════════════════════════════════════════════════════════════════════
        # ── TestGen Agent Lambda ──────────────────────────────────────────────
        # ══════════════════════════════════════════════════════════════════════

        agent_function = lambda_.Function(
            self, "TestGenFunction",
            function_name=f"perfsage-testgen-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../testgen_agent/lambda_package"),
            memory_size=3008,
            timeout=Duration.minutes(15),
            architecture=lambda_.Architecture.X86_64,
            environment={
                "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
                "BEDROCK_REGION": self.region,
                "JOB_TABLE_NAME": job_table.table_name,
                "BUCKET_NAME": spec_bucket.bucket_name,
                "WORKER_FUNCTION_NAME": f"perfsage-testgen-{env_name}",
                "LOG_LEVEL": "INFO" if is_prod else "DEBUG",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        # IAM: Bedrock + Self-invoke
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="BedrockInvoke",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        )

        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="SelfInvoke",
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:perfsage-testgen-{env_name}"],
            )
        )

        job_table.grant_read_write_data(agent_function)
        spec_bucket.grant_read_write(agent_function)

        # ══════════════════════════════════════════════════════════════════════
        # ── API Gateway: Shared across all agents ─────────────────────────────
        # ══════════════════════════════════════════════════════════════════════

        api = apigw.RestApi(
            self, "TestGenApi",
            rest_api_name=f"perfsage-testgen-{env_name}",
            description="PerfSage Agents API",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["POST", "GET", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                throttling_rate_limit=10,
                throttling_burst_limit=20,
            ),
        )

        integration = apigw.LambdaIntegration(agent_function, proxy=True)

        # POST /jobs — submit async job (IAM auth required)
        jobs = api.root.add_resource("jobs")
        jobs.add_method("POST", integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # GET /jobs/{id} — check status (IAM auth required)
        job_by_id = jobs.add_resource("{id}")
        job_by_id.add_method("GET", integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # GET /health (IAM auth required)
        health = api.root.add_resource("health")
        health.add_method("GET", integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # ══════════════════════════════════════════════════════════════════════
        # ── Executor Agent Lambda ─────────────────────────────────────────────
        # ══════════════════════════════════════════════════════════════════════

        executor_function = lambda_.Function(
            self, "ExecutorFunction",
            function_name=f"perfsage-executor-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="perfsage_executor.lambda_handler.handler",
            code=lambda_.Code.from_asset("../executor_lambda_package"),
            memory_size=3008,
            timeout=Duration.minutes(15),
            architecture=lambda_.Architecture.X86_64,
            environment={
                "PERFSAGE_MODEL_ID": "us.anthropic.claude-opus-4-7",
                "PERFSAGE_MODEL_REGION": "us-east-1",
                "PERFSAGE_S3_BUCKET": f"perfsage-results-{self.account}-{self.region}",
                "PERFSAGE_S3_PREFIX": "runs",
                "PERFSAGE_DYNAMODB_TABLE": "perfsage-test-runs",
                "PERFSAGE_ECS_CLUSTER": "perfsage-executor",
                "PERFSAGE_ECS_TASK_FAMILY": "perfsage-k6-runner",
                "PERFSAGE_ECR_REPOSITORY": "perfsage/k6-runner",
                "PERFSAGE_EXECUTION_MODE": "fargate",
                "AWS_ACCOUNT_ID": self.account,
                "LOG_LEVEL": "INFO",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        # Executor IAM: Bedrock + ECS + S3 + DynamoDB + CloudWatch + Self-invoke
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="BedrockFullAccess",
                effect=iam.Effect.ALLOW,
                actions=["bedrock:*"],
                resources=["*"],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="ECSAccess",
                effect=iam.Effect.ALLOW,
                actions=["ecs:*"],
                resources=["*"],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="IAMPassRole",
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/perfsage-ecs-execution-role",
                    f"arn:aws:iam::{self.account}:role/perfsage-ecs-task-role",
                ],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="S3Access",
                effect=iam.Effect.ALLOW,
                actions=["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::perfsage-results-{self.account}-{self.region}",
                    f"arn:aws:s3:::perfsage-results-{self.account}-{self.region}/*",
                ],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="DynamoDBAccess",
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/perfsage-test-runs",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/perfsage-test-runs/*",
                ],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogs",
                effect=iam.Effect.ALLOW,
                actions=["logs:*"],
                resources=["*"],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="SelfInvoke",
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:perfsage-executor-{env_name}"],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="CloudFormationDescribe",
                effect=iam.Effect.ALLOW,
                actions=["cloudformation:DescribeStacks"],
                resources=["*"],
            )
        )
        executor_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="CloudWatchMetrics",
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )

        executor_integration = apigw.LambdaIntegration(executor_function, proxy=True)

        # POST /executor/run — start a performance test
        executor_resource = api.root.add_resource("executor")
        executor_run = executor_resource.add_resource("run")
        executor_run.add_method("POST", executor_integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # GET /executor/status/{id} — poll test status
        executor_status = executor_resource.add_resource("status")
        executor_status_by_id = executor_status.add_resource("{id}")
        executor_status_by_id.add_method("GET", executor_integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # ══════════════════════════════════════════════════════════════════════
        # ── Analysis Agent Lambda ─────────────────────────────────────────────
        # ══════════════════════════════════════════════════════════════════════

        analysis_function = lambda_.Function(
            self, "AnalysisFunction",
            function_name=f"perfsage-analysis-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="analysis_agent.lambda_handler.handler",
            code=lambda_.Code.from_asset("../analysis_lambda_package"),
            memory_size=3008,
            timeout=Duration.minutes(10),
            architecture=lambda_.Architecture.X86_64,
            environment={
                "PERFSAGE_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
                "PERFSAGE_MODEL_REGION": self.region,
                "PERFSAGE_S3_BUCKET": f"perfsage-results-{self.account}-{self.region}",
                "PERFSAGE_DYNAMODB_TABLE": "perfsage-test-runs",
                "LOG_LEVEL": "INFO",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        # Analysis IAM: Bedrock + S3 read + DynamoDB read
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="BedrockInvoke",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        )
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="S3Read",
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::perfsage-results-{self.account}-{self.region}",
                    f"arn:aws:s3:::perfsage-results-{self.account}-{self.region}/*",
                ],
            )
        )
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="DynamoDBRead",
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:GetItem", "dynamodb:Query"],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/perfsage-test-runs",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/perfsage-test-runs/*",
                ],
            )
        )
        # X-Ray read — lets the analysis agent pull server-side trace evidence
        # (cold starts, per-segment latency, faults) from the target app. X-Ray
        # read APIs only support Resource "*".
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="XRayRead",
                effect=iam.Effect.ALLOW,
                actions=[
                    "xray:GetTraceSummaries",
                    "xray:BatchGetTraces",
                    "xray:GetServiceGraph",
                ],
                resources=["*"],
            )
        )
        # CloudWatch read — lets the analysis agent attribute throttling/errors
        # to the target's Lambda / API Gateway / DynamoDB. GetMetricData only
        # supports Resource "*".
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="CloudWatchMetricsRead",
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:GetMetricData",
                    "cloudwatch:ListMetrics",
                ],
                resources=["*"],
            )
        )
        # API Gateway read — lets the analysis agent derive the target's API
        # name + backing Lambda from the target URL (so CloudWatch grounding
        # works from the UI, which only passes the URL). GET-only.
        analysis_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="ApiGatewayRead",
                effect=iam.Effect.ALLOW,
                actions=["apigateway:GET"],
                resources=[f"arn:aws:apigateway:{self.region}::/restapis*"],
            )
        )

        analysis_integration = apigw.LambdaIntegration(analysis_function, proxy=True)

        # POST /analysis/run — run analysis on a completed test
        analysis_resource = api.root.add_resource("analysis")
        analysis_run = analysis_resource.add_resource("run")
        analysis_run.add_method("POST", analysis_integration,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # ══════════════════════════════════════════════════════════════════════
        # ── TestGen Fargate Resources ─────────────────────────────────────────
        # ══════════════════════════════════════════════════════════════════════

        # ECR repository for TestGen container image
        testgen_ecr = ecr.Repository(
            self, "TestGenECR",
            repository_name="perfsage/testgen-runner",
            removal_policy=removal_policy,
            empty_on_delete=not is_prod,
            image_scan_on_push=True,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=5)],
        ) if not is_prod else ecr.Repository(
            self, "TestGenECR",
            repository_name="perfsage/testgen-runner",
            removal_policy=removal_policy,
            image_scan_on_push=True,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
        )

        # Task execution role (ECS uses this to pull image + write logs)
        testgen_execution_role = iam.Role(
            self, "TestGenExecutionRole",
            role_name="perfsage-ecs-testgen-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy"),
            ],
        )

        # Task role (the container uses this for Bedrock, S3, DynamoDB)
        testgen_task_role = iam.Role(
            self, "TestGenTaskRole",
            role_name="perfsage-ecs-testgen-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Bedrock — scoped to specific actions and model ARNs
        testgen_task_role.add_to_policy(iam.PolicyStatement(
            sid="BedrockInvoke",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
            ],
            resources=[
                "arn:aws:bedrock:*::foundation-model/anthropic.*",
                f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
            ],
        ))

        # S3 — scoped to the spec bucket only
        spec_bucket.grant_read_write(testgen_task_role)

        # DynamoDB — scoped to job table only
        job_table.grant_read_write_data(testgen_task_role)

        # CloudWatch Logs — scoped to testgen log group
        testgen_task_role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/perfsage/testgen:*",
            ],
        ))

        # TestGen Fargate task definition (static — per-run config via overrides)
        testgen_log_group = logs.LogGroup(
            self, "TestGenFargateLogGroup",
            log_group_name="/perfsage/testgen",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=removal_policy,
        )

        testgen_task_def = ecs.FargateTaskDefinition(
            self, "TestGenTaskDef",
            family="perfsage-testgen-runner",
            cpu=512,               # TestGen is I/O-bound (waits on Bedrock); 0.5 vCPU is sufficient
            memory_limit_mib=1024, # ~500 MB peak for spec parsing + LLM orchestration
            execution_role=testgen_execution_role,
            task_role=testgen_task_role,
        )

        testgen_task_def.add_container(
            "testgen-runner",
            image=ecs.ContainerImage.from_ecr_repository(testgen_ecr, "latest"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="testgen",
                log_group=testgen_log_group,
            ),
            stop_timeout=Duration.seconds(120),
            environment={
                "LOG_LEVEL": "INFO" if is_prod else "DEBUG",
            },
        )

        # Update TestGen Lambda to launch Fargate tasks instead of self-invoke
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="ECSRunTestGenTask",
                effect=iam.Effect.ALLOW,
                actions=["ecs:RunTask", "ecs:DescribeTasks"],
                resources=[
                    testgen_task_def.task_definition_arn,
                    f"arn:aws:ecs:{self.region}:{self.account}:task/perfsage-executor/*",
                ],
            )
        )
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassTestGenRoles",
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[
                    testgen_execution_role.role_arn,
                    testgen_task_role.role_arn,
                ],
            )
        )

        # Add Fargate-related env vars to the TestGen Lambda
        agent_function.add_environment("TESTGEN_EXECUTION_MODE", "fargate")
        agent_function.add_environment("TESTGEN_ECS_CLUSTER", "perfsage-executor")
        agent_function.add_environment("TESTGEN_TASK_DEF", testgen_task_def.task_definition_arn)
        # Fargate networking — resolved from PerfSageNetworking/PerfSageExecution via
        # cross-stack references so the values stay in sync and survive redeploys.
        agent_function.add_environment("TESTGEN_FARGATE_SUBNETS", fargate_subnets)
        agent_function.add_environment("TESTGEN_FARGATE_SECURITY_GROUPS", fargate_security_groups)

        # ── Outputs ───────────────────────────────────────────────────────────
        CfnOutput(self, "ApiEndpoint",
            value=api.url,
            description="API Gateway endpoint",
        )
        CfnOutput(self, "JobTableName",
            value=job_table.table_name,
            description="DynamoDB job table name",
        )
        CfnOutput(self, "SpecBucketName",
            value=spec_bucket.bucket_name,
            description="S3 bucket for spec storage",
        )
        CfnOutput(self, "TestGenFunctionArn",
            value=agent_function.function_arn,
            description="TestGen Lambda function ARN",
        )
        CfnOutput(self, "ExecutorFunctionArn",
            value=executor_function.function_arn,
            description="Executor Lambda function ARN",
        )
        CfnOutput(self, "AnalysisFunctionArn",
            value=analysis_function.function_arn,
            description="Analysis Lambda function ARN",
        )
        CfnOutput(self, "TestGenECRRepo",
            value=testgen_ecr.repository_uri,
            description="TestGen ECR repository URI",
        )
        CfnOutput(self, "TestGenTaskDefArn",
            value=testgen_task_def.task_definition_arn,
            description="TestGen Fargate task definition ARN",
        )
