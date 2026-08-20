"""WebSocket service — pushes real-time metrics to connected UI clients via API Gateway."""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from perfsage_executor.config import get_settings
from perfsage_executor.models.metrics import MetricSnapshot
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class WebSocketService:
    """Manages WebSocket connections and broadcasts metrics to connected clients."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._connections_table = boto3.resource(
            "dynamodb", region_name=self.settings.aws.region
        ).Table(self.settings.dynamodb.connections_table)
        self._api_client: Any = None

    @property
    def api_client(self) -> Any:
        """Lazy-initialized API Gateway Management API client."""
        if self._api_client is None:
            endpoint_url = self.settings.websocket.api_url
            if not endpoint_url:
                logger.warning("WebSocket API URL not configured — streaming disabled")
                return None
            self._api_client = boto3.client(
                "apigatewaymanagementapi",
                endpoint_url=endpoint_url,
                region_name=self.settings.aws.region,
            )
        return self._api_client

    def broadcast_metrics(self, test_id: str, snapshot: MetricSnapshot) -> int:
        """Broadcast a metric snapshot to all clients subscribed to a test.

        Args:
            test_id: Test run identifier.
            snapshot: Metric snapshot to broadcast.

        Returns:
            Number of clients successfully notified.
        """
        if not self.api_client:
            return 0

        connections = self._get_connections(test_id)
        message = json.dumps(snapshot.to_websocket_message())
        successful = 0

        for connection_id in connections:
            try:
                self.api_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=message.encode("utf-8"),
                )
                successful += 1
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "GoneException":
                    # Client disconnected — remove stale connection
                    self._remove_connection(connection_id)
                else:
                    logger.warning(f"Failed to send to {connection_id}: {e}")

        return successful

    def broadcast_anomaly(self, test_id: str, anomaly_data: dict[str, Any]) -> int:
        """Broadcast an anomaly alert to subscribed clients.

        Args:
            test_id: Test run identifier.
            anomaly_data: Serialized anomaly event.

        Returns:
            Number of clients successfully notified.
        """
        if not self.api_client:
            return 0

        connections = self._get_connections(test_id)
        message = json.dumps({"type": "anomaly", "test_id": test_id, "data": anomaly_data})
        successful = 0

        for connection_id in connections:
            try:
                self.api_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=message.encode("utf-8"),
                )
                successful += 1
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "GoneException":
                    self._remove_connection(connection_id)
                else:
                    logger.warning(f"Failed to send anomaly to {connection_id}: {e}")

        return successful

    def broadcast_status(self, test_id: str, status: str, message: str = "") -> int:
        """Broadcast a test status change to subscribed clients.

        Args:
            test_id: Test run identifier.
            status: New test status.
            message: Optional status message.

        Returns:
            Number of clients notified.
        """
        if not self.api_client:
            return 0

        connections = self._get_connections(test_id)
        payload = json.dumps({"type": "status", "test_id": test_id, "status": status, "message": message})
        successful = 0

        for connection_id in connections:
            try:
                self.api_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=payload.encode("utf-8"),
                )
                successful += 1
            except ClientError:
                pass

        return successful

    def _get_connections(self, test_id: str) -> list[str]:
        """Get all connection IDs subscribed to a test.

        Args:
            test_id: Test run identifier.

        Returns:
            List of WebSocket connection IDs.
        """
        try:
            response = self._connections_table.query(
                KeyConditionExpression="test_id = :test_id",
                ExpressionAttributeValues={":test_id": test_id},
            )
            return [item["connection_id"] for item in response.get("Items", [])]
        except ClientError as e:
            logger.error(f"Failed to query connections for test {test_id}: {e}")
            return []

    def _remove_connection(self, connection_id: str) -> None:
        """Remove a stale connection from DynamoDB."""
        try:
            self._connections_table.delete_item(
                Key={"connection_id": connection_id},
            )
        except ClientError:
            pass
