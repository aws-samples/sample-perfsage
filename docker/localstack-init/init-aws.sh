#!/bin/bash
# LocalStack initialization script — creates required AWS resources for local development

echo "Initializing LocalStack resources for PerfSage..."

# Create S3 bucket
awslocal s3 mb s3://perfsage-results

# Create DynamoDB tables
awslocal dynamodb create-table \
    --table-name perfsage-test-runs \
    --attribute-definitions \
        AttributeName=test_id,AttributeType=S \
        AttributeName=status,AttributeType=S \
        AttributeName=created_at,AttributeType=N \
    --key-schema AttributeName=test_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --global-secondary-indexes \
        'IndexName=status-index,KeySchema=[{AttributeName=status,KeyType=HASH},{AttributeName=created_at,KeyType=RANGE}],Projection={ProjectionType=ALL}'

awslocal dynamodb create-table \
    --table-name perfsage-ws-connections \
    --attribute-definitions \
        AttributeName=connection_id,AttributeType=S \
        AttributeName=test_id,AttributeType=S \
    --key-schema AttributeName=connection_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --global-secondary-indexes \
        'IndexName=test-id-index,KeySchema=[{AttributeName=test_id,KeyType=HASH}],Projection={ProjectionType=ALL}'

echo "LocalStack initialization complete!"
