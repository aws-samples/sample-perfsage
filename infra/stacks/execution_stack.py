"""Execution Stack — ECS Cluster, Fargate Task Definition, ECR Repository, IAM Roles.

The ECR repo is created here. The task definition uses a placeholder image initially.
The deploy script pushes the real image after this stack deploys, then updates the task def.
"""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class ExecutionStack(Stack):
    """Provisions ECS cluster, ECR repository, Fargate task definitions, and IAM roles."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        results_bucket: s3.IBucket,
        test_runs_table: dynamodb.ITable,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECR Repository for k6 runner image
        self.ecr_repository = ecr.Repository(
            self,
            "K6RunnerRepo",
            repository_name="perfsage/k6-runner",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 5 images",
                    max_image_count=5,
                )
            ],
        )

        # ECS Cluster
        self.cluster = ecs.Cluster(
            self,
            "ExecutorCluster",
            cluster_name="perfsage-executor",
            vpc=vpc,
            enable_fargate_capacity_providers=True,
        )

        # Task Execution Role (for pulling images, writing logs)
        self.execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name="perfsage-ecs-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # Task Role (for k6 container — S3, DynamoDB, CloudWatch access)
        self.task_role = iam.Role(
            self,
            "TaskRole",
            role_name="perfsage-ecs-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Grant S3 write access
        results_bucket.grant_read_write(self.task_role)

        # Grant DynamoDB write access
        test_runs_table.grant_read_write_data(self.task_role)

        # Grant CloudWatch metrics access
        self.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudwatch:PutMetricData",
                    "cloudwatch:GetMetricData",
                ],
                resources=["*"],
            )
        )

        # Grant CloudWatch Logs access
        self.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=["*"],
            )
        )

        # Log Group for k6 tasks
        self.log_group = logs.LogGroup(
            self,
            "K6LogGroup",
            log_group_name="/perfsage/k6",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Security Group for Fargate tasks
        self.task_security_group = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="Security group for PerfSage k6 Fargate tasks",
            allow_all_outbound=True,
        )

        # Outputs
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn)
        CfnOutput(self, "EcrRepositoryUri", value=self.ecr_repository.repository_uri)
        CfnOutput(self, "ExecutionRoleArn", value=self.execution_role.role_arn)
        CfnOutput(self, "TaskRoleArn", value=self.task_role.role_arn)
        CfnOutput(self, "SecurityGroupId", value=self.task_security_group.security_group_id)
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name)
        CfnOutput(
            self,
            "PrivateSubnetIds",
            value=",".join([s.subnet_id for s in vpc.private_subnets]),
        )
