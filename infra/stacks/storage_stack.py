"""Storage Stack — S3 bucket for metrics + DynamoDB table for test run metadata."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class StorageStack(Stack):
    """Provisions S3 bucket and DynamoDB tables for test result persistence."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for raw metrics and test results
        self.results_bucket = s3.Bucket(
            self,
            "ResultsBucket",
            bucket_name=f"perfsage-results-{self.account}-{self.region}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="transition-to-ia",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="expire-old-results",
                    expiration=Duration.days(90),
                    enabled=True,
                ),
            ],
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

        # DynamoDB Table for test run metadata and summaries
        self.test_runs_table = dynamodb.Table(
            self,
            "TestRunsTable",
            table_name="perfsage-test-runs",
            partition_key=dynamodb.Attribute(
                name="test_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="expires_at",
            point_in_time_recovery=True,
        )

        # GSI for querying by status
        self.test_runs_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.NUMBER,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # DynamoDB Table for WebSocket connections
        self.connections_table = dynamodb.Table(
            self,
            "ConnectionsTable",
            table_name="perfsage-ws-connections",
            partition_key=dynamodb.Attribute(
                name="connection_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )

        # GSI for querying connections by test_id
        self.connections_table.add_global_secondary_index(
            index_name="test-id-index",
            partition_key=dynamodb.Attribute(
                name="test_id",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Outputs
        CfnOutput(self, "ResultsBucketName", value=self.results_bucket.bucket_name)
        CfnOutput(self, "ResultsBucketArn", value=self.results_bucket.bucket_arn)
        CfnOutput(self, "TestRunsTableName", value=self.test_runs_table.table_name)
        CfnOutput(self, "TestRunsTableArn", value=self.test_runs_table.table_arn)
        CfnOutput(self, "ConnectionsTableName", value=self.connections_table.table_name)
