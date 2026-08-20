"""DynamoDB service — stores test run metadata and summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from perfsage_executor.config import get_settings
from perfsage_executor.models.test_run import TestRun
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class DynamoDBService:
    """Manages DynamoDB operations for test run persistence."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._resource = boto3.resource("dynamodb", region_name=self.settings.aws.region)
        self._table = self._resource.Table(self.settings.dynamodb.table_name)

    def save_test_run(self, test_run: TestRun) -> None:
        """Save or update a test run record.

        Args:
            test_run: TestRun model to persist.
        """
        try:
            item = test_run.to_dynamodb_item()
            # Add TTL (90 days from now)
            item["expires_at"] = int((datetime.now(timezone.utc).timestamp()) + (90 * 24 * 3600))
            self._table.put_item(Item=self._serialize_item(item))
            logger.info(f"Saved test run {test_run.test_id} (status: {test_run.status.value})")
        except ClientError as e:
            raise RuntimeError(f"Failed to save test run to DynamoDB: {e}") from e

    def get_test_run(self, test_id: str) -> dict[str, Any] | None:
        """Retrieve a test run record by ID.

        Args:
            test_id: Unique test run identifier.

        Returns:
            Raw DynamoDB item dict, or None if not found.
        """
        try:
            response = self._table.get_item(Key={"test_id": test_id})
            return response.get("Item")
        except ClientError as e:
            logger.error(f"Failed to get test run {test_id}: {e}")
            return None

    def update_status(self, test_id: str, status: str, **kwargs: Any) -> None:
        """Update the status of a test run.

        Args:
            test_id: Test run identifier.
            status: New status value.
            **kwargs: Additional attributes to update.
        """
        try:
            update_expr = "SET #status = :status"
            expr_names = {"#status": "status"}
            expr_values: dict[str, Any] = {":status": status}

            if "ended_at" in kwargs:
                update_expr += ", ended_at = :ended_at"
                expr_values[":ended_at"] = kwargs["ended_at"]

            if "error_message" in kwargs:
                update_expr += ", error_message = :error_message"
                expr_values[":error_message"] = kwargs["error_message"]

            if "metrics_location" in kwargs:
                update_expr += ", metrics_location = :metrics_location"
                expr_values[":metrics_location"] = kwargs["metrics_location"]

            if "summary" in kwargs:
                update_expr += ", summary = :summary"
                expr_values[":summary"] = self._serialize_item(kwargs["summary"])

            self._table.update_item(
                Key={"test_id": test_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
            logger.info(f"Updated test run {test_id} status to {status}")
        except ClientError as e:
            logger.error(f"Failed to update test run {test_id}: {e}")

    def list_test_runs(self, limit: int = 10, status_filter: str | None = None) -> list[dict[str, Any]]:
        """List recent test runs.

        Args:
            limit: Maximum number of results.
            status_filter: Optional status to filter by (uses GSI).

        Returns:
            List of test run items.
        """
        try:
            if status_filter:
                response = self._table.query(
                    IndexName="status-index",
                    KeyConditionExpression="status = :status",
                    ExpressionAttributeValues={":status": status_filter},
                    Limit=limit,
                    ScanIndexForward=False,
                )
            else:
                response = self._table.scan(Limit=limit)

            return response.get("Items", [])
        except ClientError as e:
            logger.error(f"Failed to list test runs: {e}")
            return []

    def _serialize_item(self, item: Any) -> Any:
        """Recursively convert Python types to DynamoDB-compatible types."""
        if isinstance(item, dict):
            return {k: self._serialize_item(v) for k, v in item.items()}
        elif isinstance(item, list):
            return [self._serialize_item(i) for i in item]
        elif isinstance(item, float):
            from decimal import Decimal
            return Decimal(str(item))
        elif isinstance(item, datetime):
            return int(item.timestamp() * 1000)
        elif isinstance(item, tuple):
            return [self._serialize_item(i) for i in item]
        return item
