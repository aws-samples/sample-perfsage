"""Networking Stack — VPC + WebSocket API Gateway for real-time metric streaming."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_logs as logs,
)
from constructs import Construct


WEBSOCKET_CONNECT_HANDLER = """
import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])

def handler(event, context):
    connection_id = event['requestContext']['connectionId']
    # Client can pass test_id as query param: ?test_id=run-abc123
    query_params = event.get('queryStringParameters') or {}
    test_id = query_params.get('test_id', 'all')
    
    table.put_item(Item={
        'connection_id': connection_id,
        'test_id': test_id,
        'connected_at': int(datetime.utcnow().timestamp()),
        'ttl': int(datetime.utcnow().timestamp()) + 86400,  # 24h TTL
    })
    
    return {'statusCode': 200, 'body': 'Connected'}
"""

WEBSOCKET_DISCONNECT_HANDLER = """
import json
import os
import boto3

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])

def handler(event, context):
    connection_id = event['requestContext']['connectionId']
    
    table.delete_item(Key={'connection_id': connection_id})
    
    return {'statusCode': 200, 'body': 'Disconnected'}
"""

WEBSOCKET_DEFAULT_HANDLER = """
import json
import os

def handler(event, context):
    # Default handler for unrecognized routes
    return {'statusCode': 200, 'body': json.dumps({'message': 'PerfSage WebSocket API'})}
"""


class NetworkingStack(Stack):
    """Provisions VPC and WebSocket API Gateway for PerfSage."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC with public and private subnets across 2 AZs
        self.vpc = ec2.Vpc(
            self,
            "PerfSageVpc",
            vpc_name="perfsage-vpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # WebSocket API Gateway
        self.websocket_api = apigwv2.WebSocketApi(
            self,
            "MetricsWebSocketApi",
            api_name="perfsage-metrics-ws",
            description="PerfSage real-time metrics WebSocket API",
        )

        # Lambda execution role
        lambda_role = iam.Role(
            self,
            "WebSocketLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Grant DynamoDB access for connections table
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/perfsage-ws-connections*"],
            )
        )

        # Connect handler Lambda
        connect_fn = lambda_.Function(
            self,
            "ConnectHandler",
            function_name="perfsage-ws-connect",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline(WEBSOCKET_CONNECT_HANDLER),
            role=lambda_role,
            timeout=Duration.seconds(10),
            environment={
                "CONNECTIONS_TABLE": "perfsage-ws-connections",
            },
        )

        # Disconnect handler Lambda
        disconnect_fn = lambda_.Function(
            self,
            "DisconnectHandler",
            function_name="perfsage-ws-disconnect",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline(WEBSOCKET_DISCONNECT_HANDLER),
            role=lambda_role,
            timeout=Duration.seconds(10),
            environment={
                "CONNECTIONS_TABLE": "perfsage-ws-connections",
            },
        )

        # Default handler Lambda
        default_fn = lambda_.Function(
            self,
            "DefaultHandler",
            function_name="perfsage-ws-default",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline(WEBSOCKET_DEFAULT_HANDLER),
            role=lambda_role,
            timeout=Duration.seconds(10),
        )

        # Add routes to WebSocket API
        self.websocket_api.add_route(
            "$connect",
            integration=integrations.WebSocketLambdaIntegration(
                "ConnectIntegration", connect_fn
            ),
        )

        self.websocket_api.add_route(
            "$disconnect",
            integration=integrations.WebSocketLambdaIntegration(
                "DisconnectIntegration", disconnect_fn
            ),
        )

        self.websocket_api.add_route(
            "$default",
            integration=integrations.WebSocketLambdaIntegration(
                "DefaultIntegration", default_fn
            ),
        )

        # WebSocket Stage
        self.websocket_stage = apigwv2.WebSocketStage(
            self,
            "ProdStage",
            web_socket_api=self.websocket_api,
            stage_name="prod",
            auto_deploy=True,
        )

        # Outputs
        ws_url = f"wss://{self.websocket_api.api_id}.execute-api.{self.region}.amazonaws.com/prod"
        management_url = f"https://{self.websocket_api.api_id}.execute-api.{self.region}.amazonaws.com/prod"

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(self, "WebSocketApiId", value=self.websocket_api.api_id)
        CfnOutput(self, "WebSocketUrl", value=ws_url)
        CfnOutput(self, "WebSocketManagementUrl", value=management_url)
        CfnOutput(
            self,
            "PrivateSubnets",
            value=",".join([s.subnet_id for s in self.vpc.private_subnets]),
        )
