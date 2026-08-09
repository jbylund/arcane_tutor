#!/usr/bin/env python3
"""Generate postgresql.conf from template with memory settings tuned to available memory.

Memory values are computed from the available memory with conservative ratios so that two
concurrent instances (blue/green) fit comfortably on the host.
"""

import argparse
import platform
import subprocess
from pathlib import Path
from string import Template

MB = 1024 * 1024
GB = 1024 * MB

# --- postgresql.conf ratios, as fractions of the memory available to the host ---
SHARED_BUFFERS_RATIO = 0.05
EFFECTIVE_CACHE_SIZE_RATIO = 0.40
MAINTENANCE_WORK_MEM_RATIO = 0.02
MAINTENANCE_WORK_MEM_MIN = 64 * MB
MAINTENANCE_WORK_MEM_MAX = 2 * GB
# Safe under 100 connections * 4 parallel workers.
WORK_MEM_RATIO = 0.001
WORK_MEM_MIN = 16 * MB

# --- Docker memory limit headroom, above shared_buffers + maintenance_work_mem ---
#
# This was a flat 256 MB, which is less than one backend's peak anon RSS. A whole-table
# UPDATE -- the prefer-score and CubeCobra backfills at the end of an import -- was measured
# on a 15 GB host at 276 MB anon plus 831 MB shmem in a single backend, against a limit of
# 1350 MB. The cgroup OOM killer took the backend and the import died with "server closed the
# connection unexpectedly". The same limit also has to cover the other API workers'
# connections, the autovacuum workers that the UPDATE's churn wakes up, and the dirty page
# cache from its heap and WAL writes, which counts against the cgroup. memswap_limit equals
# the memory limit, so there is no swap to absorb a spike -- overshooting is always a kill.
#
# The floor is what that measurement says one busy backend costs, doubled for the rest of the
# container. The ratio takes over on larger hosts, where work_mem and maintenance_work_mem are
# proportionally larger and so is every backend that touches them.
CONTAINER_OVERHEAD_FLOOR = 768 * MB
CONTAINER_OVERHEAD_SHARED_BUFFERS_RATIO = 0.5


def get_available_memory_bytes() -> int:
    """Return available memory in bytes.

    Prefers the Docker VM's allocation (via `docker info`) so that limits set
    in Docker Desktop / colima are respected.  Falls back to host physical
    memory if Docker is not reachable.
    """
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            mem = int(result.stdout.strip())
            if mem > 0:
                return mem
    except OSError:
        pass

    if platform.system() == "Darwin":
        result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
        return int(result.stdout.strip())

    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024

    msg = "Could not determine available memory"
    raise RuntimeError(msg)


def fmt_mb(n_bytes: int) -> str:
    """Format bytes as a PostgreSQL memory string in MB."""
    return f"{n_bytes // (1024 * 1024)}MB"


def _compute_raw(total_bytes: int) -> dict[str, int]:
    """Compute PostgreSQL memory settings in bytes, sized for two concurrent instances (blue/green)."""
    return {
        "shared_buffers": int(total_bytes * SHARED_BUFFERS_RATIO),
        "effective_cache_size": int(total_bytes * EFFECTIVE_CACHE_SIZE_RATIO),
        "maintenance_work_mem": max(
            MAINTENANCE_WORK_MEM_MIN,
            min(int(total_bytes * MAINTENANCE_WORK_MEM_RATIO), MAINTENANCE_WORK_MEM_MAX),
        ),
        "work_mem": max(WORK_MEM_MIN, int(total_bytes * WORK_MEM_RATIO)),
    }


def _memory_notes() -> str:
    """Render the comment block that documents these ratios inside the generated conf.

    Generated rather than written into the template: the hand-written version drifted to
    describing ratios the script had stopped using, which is actively misleading to read
    while debugging a memory problem.
    """
    return "\n".join(
        [
            f"# shared_buffers:       {SHARED_BUFFERS_RATIO:>5.1%} of available memory",
            f"# effective_cache_size: {EFFECTIVE_CACHE_SIZE_RATIO:>5.1%} of available memory",
            f"# maintenance_work_mem: {MAINTENANCE_WORK_MEM_RATIO:>5.1%} of available memory, clamped to "
            f"[{fmt_mb(MAINTENANCE_WORK_MEM_MIN)}, {fmt_mb(MAINTENANCE_WORK_MEM_MAX)}]",
            f"# work_mem:             {WORK_MEM_RATIO:>5.1%} of available memory, minimum {fmt_mb(WORK_MEM_MIN)} "
            f"(safe at 100 conns * 4 parallel workers)",
        ]
    )


def compute_settings(total_bytes: int) -> dict[str, str]:
    """Compute PostgreSQL memory settings as formatted strings for postgresql.conf."""
    raw = _compute_raw(total_bytes)
    return {
        "available_memory": fmt_mb(total_bytes),
        "shared_buffers": fmt_mb(raw["shared_buffers"]),
        "effective_cache_size": fmt_mb(raw["effective_cache_size"]),
        "maintenance_work_mem": fmt_mb(raw["maintenance_work_mem"]),
        "work_mem": fmt_mb(raw["work_mem"]),
        "memory_notes": _memory_notes(),
    }


def compute_pg_mem_limit_bytes(total_bytes: int) -> int:
    """Compute Docker memory limit for a single postgres container.

    shared_buffers + maintenance_work_mem + headroom for backend process memory, autovacuum
    workers and page cache. See CONTAINER_OVERHEAD_FLOOR for how the headroom was sized.
    """
    raw = _compute_raw(total_bytes)
    overhead = max(CONTAINER_OVERHEAD_FLOOR, int(raw["shared_buffers"] * CONTAINER_OVERHEAD_SHARED_BUFFERS_RATIO))
    return raw["shared_buffers"] + raw["maintenance_work_mem"] + overhead


def _is_gitignored(path: Path) -> bool:
    """Return True if git ignores path, False if tracked or if git cannot be consulted."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path)],
            check=False,
            capture_output=True,
        )
    except OSError:
        return True  # No git available; nothing useful to warn about.
    return result.returncode == 0


def write_env_file(env_path: Path, values: dict[str, str]) -> None:
    """Write an env file containing exactly these values, replacing whatever was there.

    This file has one writer -- this script -- so it is rewritten wholesale rather than
    merged into. That is the point of it being separate from .env: .env is rebuilt from
    env.json by a make rule that truncates, which silently dropped a key appended here.
    """
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    env_path.write_text(
        "# Generated by scripts/gen_postgres_conf.py -- do not edit.\n"
        "# Derived from this host's memory and paired with the postgresql.conf generated\n"
        "# alongside it. Re-generate both with: make postgres-config\n" + body
    )


def main() -> None:
    """Parse args, detect available memory, and render the postgresql.conf template."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, help="path to postgresql.conf.template")
    parser.add_argument("--output", required=True, help="path to write postgresql.conf")
    parser.add_argument(
        "--env-output",
        help=(
            "path of the per-host env file to write POSTGRES_MEM_LIMIT to (e.g. .env.generated). Owned "
            "outright by this script and rewritten wholesale, so pass a path nothing else writes. Must "
            "be gitignored: the limit is derived from THIS host's memory and has to travel with the "
            "postgresql.conf generated alongside it."
        ),
    )
    args = parser.parse_args()

    total_bytes = get_available_memory_bytes()
    settings = compute_settings(total_bytes)

    template_text = Path(args.template).read_text()
    result = Template(template_text).safe_substitute(settings)
    Path(args.output).write_text(result)

    print(f"Generated {args.output}")
    for key, val in settings.items():
        # memory_notes is the multi-line comment block rendered into the conf, not a setting.
        if key != "memory_notes":
            print(f"  {key}: {val}")

    if args.env_output:
        limit_mb = compute_pg_mem_limit_bytes(total_bytes) // (1024 * 1024)
        limit_str = f"{limit_mb}m"
        # A file of its own, and specifically not .env or the tracked envs/ directory.
        # POSTGRES_MEM_LIMIT has to agree with the shared_buffers in the postgresql.conf generated
        # just above, and both are derived from this host's memory, so it must survive everything
        # that rewrites those other files:
        #
        #   envs/<stack>  tracked, so the next git checkout reverts the limit while the untracked
        #                 conf survives — and make then considers the conf up to date and never
        #                 regenerates, so the compose default silently applies to a conf that needs
        #                 more than it.
        #   .env          rebuilt from env.json by a make rule that truncates, which drops any key
        #                 env.json does not have. Same silent ending, and it races this script on a
        #                 fresh host because make runs both recipes in parallel.
        #
        # Compose reads this file after .env and before envs/<stack>, and later files win, so a
        # POSTGRES_MEM_LIMIT left behind in a pre-existing .env is overridden rather than obeyed.
        env_file = Path(args.env_output)
        write_env_file(env_file, {"POSTGRES_MEM_LIMIT": limit_str})
        print(f"  POSTGRES_MEM_LIMIT: {limit_str} -> {env_file}")
        if not _is_gitignored(env_file):
            print(f"  WARNING: {env_file} is tracked by git; a checkout will revert this value.")


if __name__ == "__main__":
    main()
