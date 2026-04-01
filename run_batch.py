"""Run a batch of benchmark tasks. Used by GitHub Actions runners."""

import os
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()

import argparse
import asyncio
import json
from dotenv import load_dotenv
from run_eval import load_tasks, run_task
from agent.runner import PlaywrightCliRunner, PhantomwrightCliRunner

load_dotenv()

CLI_BACKENDS = {
    "playwright-cli": PlaywrightCliRunner,
    "phantomwright-cli": PhantomwrightCliRunner,
}

MODELS = {
    "claude-haiku-4.5": "claude-haiku-4.5",
    "claude-sonnet-4.6": "claude-sonnet-4.6",
    "claude-opus-4.6": "claude-opus-4.6",
}


def interleave(tasks: list) -> list:
    """Reorder 100 tasks, 20 per section to balance difficulty."""
    reordered = []
    for i in range(20):
        for d in range(5):
            reordered.append(tasks[d * 20 + i])
    return reordered


async def run_batch(
    model_name: str,
    start: int,
    end: int,
    parallel: int = 3,
    tracking_id: str = None,
    run_start: str = None,
    cli_backend: str = "playwright-cli",
) -> dict:
    """Run tasks[start:end] with given model. Returns results summary."""
    tasks = interleave(load_tasks())[start:end]
    model = MODELS[model_name]
    cli_runner_class = CLI_BACKENDS[cli_backend]
    sem = asyncio.Semaphore(parallel)

    results = await asyncio.gather(
        *[
            run_task(
                t,
                sem,
                model=model,
                cli_runner_class=cli_runner_class,
                run_data_dir=None,
            )
            for t in tasks
        ]
    )

    # Aggregate
    return {
        "tracking_id": tracking_id,
        "model": model_name,
        "start": start,
        "end": end,
        "run_start": run_start,
        "tasks_completed": len(results),
        "tasks_successful": sum(1 for r in results if r.get("score") == 1),
        "total_steps": sum(r.get("steps", 0) for r in results),
        "total_duration": sum(r.get("duration", 0) for r in results),
        "total_cost": sum(r.get("cost", 0) for r in results),
        "task_results": [
            {
                "task_id": r["task_id"],
                "score": r["score"],
                "steps": r.get("steps", 0),
                "duration": r.get("duration", 0),
                "cost": r.get("cost", 0),
            }
            for r in results
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Run a batch of benchmark tasks")
    parser.add_argument(
        "--model",
        required=True,
        choices=list(MODELS.keys()),
        help="Model to use",
    )
    parser.add_argument(
        "--start", type=int, required=True, help="Start task index (inclusive)"
    )
    parser.add_argument(
        "--end", type=int, required=True, help="End task index (exclusive)"
    )
    parser.add_argument(
        "--parallel", type=int, default=3, help="Max concurrent tasks (default: 3)"
    )
    parser.add_argument(
        "--tracking-id", required=True, help="UUID for orchestrator matching"
    )
    parser.add_argument(
        "--run-start", required=True, help="Run start timestamp for aggregation"
    )
    parser.add_argument(
        "--output", required=True, help="Output file path for results JSON"
    )
    parser.add_argument(
        "--cli",
        default="playwright-cli",
        choices=list(CLI_BACKENDS.keys()),
        help="CLI browser backend (default: playwright-cli)",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_batch(
            args.model,
            args.start,
            args.end,
            args.parallel,
            args.tracking_id,
            args.run_start,
            args.cli,
        )
    )
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
