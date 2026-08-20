"""Docker service — manages local k6 container lifecycle for development and testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from perfsage_executor.config import get_settings
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class DockerService:
    """Manages Docker containers for local k6 execution."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy-initialized Docker client."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
            except DockerException as e:
                raise RuntimeError(
                    "Docker daemon is not running. Please start Docker Desktop or the Docker service.\n"
                    f"Error: {e}"
                ) from e
        return self._client

    def ensure_network(self) -> str:
        """Create or get the Docker network for test isolation.

        Returns:
            Network ID.
        """
        network_name = self.settings.docker.network_name
        try:
            network = self.client.networks.get(network_name)
            logger.info(f"Using existing Docker network: {network_name}")
            return network.id
        except NotFound:
            network = self.client.networks.create(network_name, driver="bridge")
            logger.info(f"Created Docker network: {network_name}")
            return network.id

    def pull_k6_image(self) -> str:
        """Pull the k6 Docker image if not present.

        Returns:
            Image ID.
        """
        image_name = self.settings.docker.k6_image
        try:
            image = self.client.images.get(image_name)
            logger.info(f"k6 image already available: {image_name}")
        except ImageNotFound:
            logger.info(f"Pulling k6 image: {image_name}...")
            image = self.client.images.pull(image_name)
            logger.info(f"Successfully pulled: {image_name}")
        return image.id

    def start_k6_container(
        self,
        script_path: str,
        test_id: str,
        environment: dict[str, str] | None = None,
        output_dir: str | None = None,
    ) -> Container:
        """Start a k6 container for test execution.

        Args:
            script_path: Path to the k6 script on the host.
            test_id: Unique test run identifier.
            environment: Environment variables to pass to k6.
            output_dir: Host directory for output files.

        Returns:
            Docker Container object.
        """
        self.ensure_network()
        self.pull_k6_image()

        script_abs = str(Path(script_path).resolve())
        output_abs = str(Path(output_dir or f"/tmp/perfsage/{test_id}").resolve())
        Path(output_abs).mkdir(parents=True, exist_ok=True)

        volumes: dict[str, dict[str, str]] = {
            script_abs: {"bind": "/scripts/test.js", "mode": "ro"},
            output_abs: {"bind": "/results", "mode": "rw"},
        }

        env = environment or {}
        env["PERFSAGE_TEST_ID"] = test_id

        # Build k6 command with JSON output
        k6_command = [
            "run",
            "--out", "json=/results/metrics.json",
            "--summary-export=/results/summary.json",
            f"--vus", env.get("K6_VUS", "10"),
            f"--duration", env.get("K6_DURATION", "30s"),
            "/scripts/test.js",
        ]

        container = self.client.containers.run(
            image=self.settings.docker.k6_image,
            command=k6_command,
            name=f"perfsage-k6-{test_id}",
            network=self.settings.docker.network_name,
            volumes=volumes,
            environment=env,
            detach=True,
            auto_remove=False,
            labels={"perfsage.test_id": test_id, "perfsage.component": "k6-runner"},
        )

        logger.info(f"Started k6 container: {container.short_id} for test {test_id}")
        return container

    def get_container_status(self, container_id: str) -> str:
        """Get the current status of a container.

        Returns:
            Container status string (created, running, paused, restarting, exited, dead).
        """
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except NotFound:
            return "not_found"

    def stream_container_logs(self, container_id: str) -> Any:
        """Get a log stream from a running container.

        Returns:
            Generator yielding log lines.
        """
        try:
            container = self.client.containers.get(container_id)
            return container.logs(stream=True, follow=True)
        except NotFound:
            raise RuntimeError(f"Container {container_id} not found")

    def stop_container(self, container_id: str, timeout: int = 30) -> int:
        """Stop a container gracefully (SIGTERM, then SIGKILL after timeout).

        Args:
            container_id: Docker container ID.
            timeout: Seconds to wait before SIGKILL.

        Returns:
            Container exit code.
        """
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=timeout)
            result = container.wait()
            exit_code = result.get("StatusCode", -1)
            logger.info(f"Container {container_id[:12]} stopped with exit code {exit_code}")
            return exit_code
        except NotFound:
            logger.warning(f"Container {container_id} already removed")
            return -1

    def remove_container(self, container_id: str) -> None:
        """Remove a stopped container."""
        try:
            container = self.client.containers.get(container_id)
            container.remove(force=True)
            logger.info(f"Removed container: {container_id[:12]}")
        except NotFound:
            pass

    def cleanup_network(self) -> None:
        """Remove the perfsage Docker network if no containers are attached."""
        try:
            network = self.client.networks.get(self.settings.docker.network_name)
            network.remove()
            logger.info(f"Removed Docker network: {self.settings.docker.network_name}")
        except (NotFound, Exception) as e:
            logger.debug(f"Network cleanup skipped: {e}")

    def wait_for_container(self, container_id: str, timeout: int = 600) -> dict[str, Any]:
        """Wait for a container to finish execution.

        Args:
            container_id: Docker container ID.
            timeout: Maximum seconds to wait.

        Returns:
            Dict with 'StatusCode' and 'Error'.
        """
        try:
            container = self.client.containers.get(container_id)
            result = container.wait(timeout=timeout)
            return result
        except Exception as e:
            logger.error(f"Error waiting for container {container_id}: {e}")
            return {"StatusCode": -1, "Error": str(e)}

    def get_container_output_files(self, container_id: str, output_dir: str) -> dict[str, str]:
        """List output files generated by the k6 container.

        Returns:
            Dict mapping filename to full path.
        """
        output_path = Path(output_dir)
        files: dict[str, str] = {}
        if output_path.exists():
            for f in output_path.iterdir():
                if f.is_file():
                    files[f.name] = str(f)
        return files
