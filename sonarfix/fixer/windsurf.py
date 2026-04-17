"""Headless IDE fixer driver — launches Docker container, polls for completion."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

from sonarfix.config import get_settings
from sonarfix.fixer.planner import FixPlan

console = Console()

POLL_INTERVAL = 5  # seconds
COMPLETION_MARKER = "WORK-COMPLETED"


class WindsurfResult:
    """Result from a single headless IDE fixer run."""

    def __init__(
        self,
        success: bool,
        output_log: str,
        container_log: str = "",
        timed_out: bool = False,
        batch_index: int = 0,
    ):
        self.success = success
        self.output_log = output_log
        self.container_log = container_log
        self.timed_out = timed_out
        self.batch_index = batch_index

    @property
    def fixed_count(self) -> int:
        return self.output_log.count("FIXED")

    @property
    def skipped_count(self) -> int:
        return self.output_log.count("SKIPPED")


class WindsurfDriver:
    """Manages headless IDE fixer Docker containers."""

    def __init__(
        self,
        token: Optional[str] = None,
        image: Optional[str] = None,
        timeout: Optional[int] = None,
        config_dir: Optional[str] = None,
    ):
        cfg = get_settings()
        self.token = token or cfg.windsurf_token
        self.image = image or cfg.windsurf_image
        self.timeout = timeout or cfg.windsurf_timeout
        self.config_dir = config_dir or cfg.windsurf_config_dir

        if not self.token:
            raise ValueError(
                "IDE fixer token is required. "
                "Set IDE_FIXER_TOKEN in your .env or pass it as a parameter."
            )

    def _check_docker(self) -> None:
        """Verify Docker is available."""
        if not shutil.which("docker"):
            raise RuntimeError("Docker is not installed or not on PATH.")
        try:
            subprocess.run(
                ["docker", "info"], capture_output=True, check=True, timeout=10
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"Docker daemon not running: {e}") from e

    def _check_image(self) -> None:
        """Verify the Docker image exists; auto-build from bundled Dockerfile if missing."""
        result = subprocess.run(
            ["docker", "images", "-q", self.image],
            capture_output=True, text=True, timeout=10,
        )
        if not result.stdout.strip():
            self._build_image()

    def _build_image(self) -> None:
        """Build the IDE fixer Docker image from the bundled Dockerfile."""
        # Resolve docker/ide-fixer dir relative to this package
        pkg_root = Path(__file__).resolve().parent.parent.parent
        docker_dir = pkg_root / "docker" / "ide-fixer"

        if not (docker_dir / "Dockerfile").exists():
            raise RuntimeError(
                f"Docker image '{self.image}' not found and bundled Dockerfile "
                f"missing at {docker_dir}/Dockerfile. "
                f"Run: docker build docker/ide-fixer -t {self.image}"
            )

        console.print(
            f"[bold yellow]Docker image '{self.image}' not found. "
            f"Building from {docker_dir} (this may take a few minutes)...[/]"
        )

        proc = subprocess.run(
            ["docker", "build", str(docker_dir), "-t", self.image],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to build Docker image '{self.image}':\n{proc.stderr[-1000:]}"
            )
        console.print(f"[bold green]Docker image '{self.image}' built successfully![/]")

    def run_batch(self, plan: FixPlan, workspace_dir: Path) -> WindsurfResult:
        """Execute a single fix batch via the headless IDE fixer.

        Args:
            plan: The FixPlan with instruction text.
            workspace_dir: The repo workspace directory to mount.

        Returns:
            WindsurfResult with outcome details.
        """
        self._check_docker()
        self._check_image()

        # Write instructions file
        instructions_file = workspace_dir / "windsurf-instructions.txt"
        instructions_file.write_text(plan.instructions_text, encoding="utf-8")

        # Clean any previous output
        output_file = workspace_dir / "windsurf-output.txt"
        if output_file.exists():
            output_file.unlink()

        # Remove any leftover container with the same name
        container_name = f"sonarfix-batch-{plan.batch_index}"
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=10,
        )

        # Build docker command
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--shm-size=512m",
            "-e", f"IDE_FIXER_TOKEN={self.token}",
            "-e", "DISPLAY=:1",
            "-v", f"{workspace_dir}:/home/ubuntu/workspace",
        ]

        # Mount IDE fixer config if provided
        if self.config_dir:
            cmd.extend(["-v", f"{self.config_dir}:/home/ubuntu/.config/ide-fixer"])

        cmd.append(self.image)

        console.print(
            f"[bold blue]Launching fix batch {plan.batch_index} "
            f"({plan.issue_count} issues, {len(plan.file_paths)} files)...[/]"
        )

        # Launch container
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            return WindsurfResult(
                success=False,
                output_log="",
                container_log=f"Failed to start container: {e}",
                batch_index=plan.batch_index,
            )

        # Poll for completion
        start_time = time.time()
        timed_out = False

        while proc.poll() is None:
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                console.print(f"[red]Batch {plan.batch_index} timed out after {self.timeout}s[/]")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                timed_out = True
                break

            # Check for completion marker
            if output_file.exists():
                content = output_file.read_text(errors="replace")
                if COMPLETION_MARKER in content:
                    console.print(
                        f"[green]Batch {plan.batch_index} completed![/]"
                    )
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break

            time.sleep(POLL_INTERVAL)

        # Gather output
        output_log = ""
        if output_file.exists():
            output_log = output_file.read_text(errors="replace")

        container_stdout = ""
        container_stderr = ""
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
            container_stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
            container_stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        except Exception:
            pass

        container_log = container_stdout
        if container_stderr:
            container_log += f"\n--- STDERR ---\n{container_stderr}"

        success = COMPLETION_MARKER in output_log and not timed_out

        return WindsurfResult(
            success=success,
            output_log=output_log,
            container_log=container_log,
            timed_out=timed_out,
            batch_index=plan.batch_index,
        )

    def run_all(
        self, plans: list[FixPlan], workspace_dir: Path
    ) -> list[WindsurfResult]:
        """Run all fix plan batches sequentially."""
        results: list[WindsurfResult] = []
        for plan in plans:
            result = self.run_batch(plan, workspace_dir)
            results.append(result)
            if not result.success:
                console.print(
                    f"[yellow]Batch {plan.batch_index} did not fully succeed. "
                    f"Continuing with next batch...[/]"
                )
        return results
