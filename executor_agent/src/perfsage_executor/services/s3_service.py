"""S3 service — uploads raw metrics and results to Amazon S3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from perfsage_executor.config import get_settings
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class S3Service:
    """Manages S3 operations for storing test results and metrics."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = boto3.client("s3", region_name=self.settings.aws.region)

    def _build_key(self, test_id: str, filename: str) -> str:
        """Build S3 object key for a test run file."""
        return f"{self.settings.s3.prefix}/{test_id}/{filename}"

    def upload_file(self, test_id: str, local_path: str, filename: str | None = None) -> str:
        """Upload a local file to S3.

        Args:
            test_id: Test run identifier.
            local_path: Path to the local file.
            filename: Override filename in S3 (defaults to local filename).

        Returns:
            S3 URI of the uploaded file.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        key = self._build_key(test_id, filename or path.name)
        bucket = self.settings.s3.bucket

        try:
            # Use multipart upload for files > 50MB
            file_size = path.stat().st_size
            if file_size > 50 * 1024 * 1024:
                self._multipart_upload(bucket, key, local_path)
            else:
                self._client.upload_file(local_path, bucket, key)

            s3_uri = f"s3://{bucket}/{key}"
            logger.info(f"Uploaded {path.name} ({file_size} bytes) to {s3_uri}")
            return s3_uri
        except ClientError as e:
            raise RuntimeError(f"Failed to upload to S3: {e}") from e

    def upload_json(self, test_id: str, data: dict[str, Any] | list, filename: str) -> str:
        """Upload a JSON object directly to S3.

        Args:
            test_id: Test run identifier.
            data: JSON-serializable data.
            filename: Filename in S3.

        Returns:
            S3 URI of the uploaded object.
        """
        key = self._build_key(test_id, filename)
        bucket = self.settings.s3.bucket

        try:
            body = json.dumps(data, indent=2, default=str)
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
            s3_uri = f"s3://{bucket}/{key}"
            logger.info(f"Uploaded JSON ({len(body)} bytes) to {s3_uri}")
            return s3_uri
        except ClientError as e:
            raise RuntimeError(f"Failed to upload JSON to S3: {e}") from e

    def upload_test_results(self, test_id: str, output_dir: str) -> dict[str, str]:
        """Upload all result files from a test run output directory.

        Args:
            test_id: Test run identifier.
            output_dir: Directory containing k6 output files.

        Returns:
            Dict mapping filename to S3 URI.
        """
        output_path = Path(output_dir)
        uploaded: dict[str, str] = {}

        if not output_path.exists():
            logger.warning(f"Output directory not found: {output_dir}")
            return uploaded

        for file in output_path.iterdir():
            if file.is_file():
                uri = self.upload_file(test_id, str(file))
                uploaded[file.name] = uri

        logger.info(f"Uploaded {len(uploaded)} files for test {test_id}")
        return uploaded

    def get_results_uri(self, test_id: str) -> str:
        """Get the base S3 URI for a test run's results."""
        return f"s3://{self.settings.s3.bucket}/{self.settings.s3.prefix}/{test_id}/"

    def _multipart_upload(self, bucket: str, key: str, file_path: str) -> None:
        """Perform multipart upload for large files."""
        from boto3.s3.transfer import TransferConfig

        config = TransferConfig(
            multipart_threshold=50 * 1024 * 1024,
            multipart_chunksize=50 * 1024 * 1024,
            max_concurrency=4,
        )
        self._client.upload_file(file_path, bucket, key, Config=config)
