from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class EcommerceApiStack(Stack):
    """
    Product -> Order -> OrderItem CRUD API.

    API Gateway (REST) -> single Lambda (proxy) -> single DynamoDB table.
    Rate limit: 500 rps / 1000 burst (10x higher than sample_api).
    """

    def __init__(self, scope, construct_id, *, env_name="dev", **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        is_prod = env_name == "prod"
        removal_policy = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY

        # DynamoDB: single-table design
        table = dynamodb.Table(
            self, "EcommerceTable",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
        )

        # Lambda: CRUD handler
        api_function = lambda_.Function(
            self, "EcommerceApiFunction",
            function_name=f"ecommerce-api-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "TABLE_NAME": table.table_name,
                "LOG_LEVEL": "DEBUG",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        table.grant_read_write_data(api_function)

        # API Gateway: 500 rps / 1000 burst
        api = apigw.RestApi(
            self, "EcommerceApi",
            rest_api_name=f"ecommerce-api-{env_name}",
            description="E-commerce Product/Order/OrderItem CRUD API (500 rps)",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                throttling_rate_limit=500,
                throttling_burst_limit=1000,
                tracing_enabled=True,
            ),
        )

        # Proxy integration — all routes handled by Lambda
        proxy = api.root.add_resource("{proxy+}")
        proxy.add_method(
            "ANY",
            apigw.LambdaIntegration(api_function, proxy=True),
        )

        # Also handle root path
        api.root.add_method(
            "ANY",
            apigw.LambdaIntegration(api_function, proxy=True),
        )

        # Outputs
        CfnOutput(self, "ApiEndpoint", value=api.url, description="E-commerce API endpoint")
        CfnOutput(self, "TableName", value=table.table_name, description="DynamoDB table name")
        CfnOutput(self, "FunctionName", value=api_function.function_name, description="Lambda function name")
