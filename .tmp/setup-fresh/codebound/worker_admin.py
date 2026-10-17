"""Trusted local administration, never a tool exposed to agents or workers."""

import sys
from dataclasses import replace

from codebound.datasets import DEFAULT_DATA_DIR
from codebound.docker_executor import LABEL, PROJECT_LABEL, LocalDockerExecutor
from codebound.sandbox import RuntimeUnavailable, SandboxSettings


def main():
    executor = LocalDockerExecutor(DEFAULT_DATA_DIR, replace(SandboxSettings.from_env(), mode="local_docker"))
    if sys.argv[1:] == ["check"]:
        executor.ready()
        print("Local Docker worker image and required engine controls AVAILABLE")
    elif sys.argv[1:] == ["cleanup"]:
        ids = executor.command("ps", "-aq", "--filter", "label=" + LABEL,
                               "--filter", "label=" + PROJECT_LABEL).decode().split()
        for container_id in ids:
            executor.command("rm", "--force", container_id)
        print(f"Removed {len(ids)} workers owned by this Codebound checkout")
    else:
        raise SystemExit("Usage: python -m codebound.worker_admin check|cleanup")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeUnavailable, ValueError, KeyError):
        print("Local Docker worker UNAVAILABLE; no mock fallback. Check Docker and the configured image.")
        raise SystemExit(1)
