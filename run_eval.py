"""Main benchmark evaluation script.

Usage:
    uv run python run_eval.py                                     # defaults: playwright-cli + claude-sonnet-4-6
    uv run python run_eval.py --cli playwright-cli --model claude-sonnet-4-6
    uv run python run_eval.py --headed                            # run browser in headed mode
    uv run python run_eval.py --tasks 5                           # run only 5 tasks
    uv run python run_eval.py --benchmark stealth-bench           # run stealth benchmark

Available CLI backends: playwright-cli (default)
Available models: claude-haiku-4-5, claude-sonnet-4-6 (default), claude-opus-4-6
"""

# Fix for MacOS users using uv without SSL certificate setup
import certifi, os, sys

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

# Fix Windows console encoding: cp936/charmap can't print emoji → UnicodeEncodeError crash
if sys.platform == "win32":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

import argparse
import asyncio
import base64, hashlib, json, traceback
from datetime import datetime
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from agent import CliAgent
from agent.runner import PlaywrightCliRunner, PhantomwrightCliRunner
from judge import construct_judge_messages, JudgementResult
from judge_llm import invoke_judge

load_dotenv()

# Configuration
MAX_CONCURRENT = 3
TASK_TIMEOUT = 1800  # 30 minutes max per task
DEFAULT_MODEL = "claude-sonnet-4.6"

# Benchmark task files
BENCHMARKS = {
    "bu-bench": {
        "file": Path(__file__).parent / "BU_Bench_V1.enc",
        "key_seed": b"BU_Bench_V1",
    },
    "stealth-bench": {
        "file": Path(__file__).parent / "Stealth_Bench_V1.enc",
        "key_seed": b"Stealth_Bench_V1",
    },
}

# CLI backend registry
CLI_BACKENDS = {
    "playwright-cli": PlaywrightCliRunner,
    "phantomwright-cli": PhantomwrightCliRunner,
}


def encode_screenshots(paths: list[str]) -> list[str]:
    """Encode screenshot files to base64. Skips files that don't exist."""
    result = []
    for p in paths:
        path = Path(p)
        if path.exists():
            result.append(base64.b64encode(path.read_bytes()).decode())
    return result


def load_tasks(benchmark: str = "bu-bench") -> list[dict]:
    """Decrypt and load benchmark tasks."""
    bench = BENCHMARKS[benchmark]
    key = base64.urlsafe_b64encode(hashlib.sha256(bench["key_seed"]).digest())
    encrypted = base64.b64decode(bench["file"].read_text())
    return json.loads(Fernet(key).decrypt(encrypted))


async def run_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    model: str = DEFAULT_MODEL,
    cli_runner_class: type = PlaywrightCliRunner,
    headless: bool = True,
    run_data_dir: Path = None,
) -> dict:
    """Run a single task. Returns result dict with score (0 on failure).

    Args:
        task: Task dict with task_id, confirmed_task, category, answer.
        semaphore: Concurrency limiter.
        model: Claude model to use for the agent.
        cli_runner_class: CLI runner class (PlaywrightCliRunner, etc.).
        headless: Whether to run browser in headless mode.
        run_data_dir: Directory for trace output.
    """
    async with semaphore:
        try:
            task_id = str(task.get("task_id", "unknown"))
            print(f"Running task: {task_id}")

            # Create runner and agent
            task_output_dir = run_data_dir / task_id if run_data_dir else Path(f"run_data_tmp/{task_id}")
            runner = cli_runner_class(
                session_name=f"bench_{task_id}",
                headless=headless,
                output_dir=task_output_dir,
            )
            agent = CliAgent(
                task=task["confirmed_task"],
                model=model,
                runner=runner,
                output_dir=task_output_dir,
                task_id=task_id,
            )

            try:
                agent_history = await asyncio.wait_for(
                    agent.run(), timeout=TASK_TIMEOUT
                )
            except asyncio.TimeoutError:
                # Runner cleanup is handled by agent.run()'s finally block
                print(f"Task {task_id} timed out after {TASK_TIMEOUT}s")
                return {
                    "task_id": task_id,
                    "score": 0,
                    "steps": 0,
                    "duration": TASK_TIMEOUT,
                    "cost": 0,
                    "error": f"Task timed out after {TASK_TIMEOUT}s",
                }

            # Collect task metrics from agent history
            steps = agent_history.number_of_steps()
            duration = agent_history.total_duration_seconds()
            cost = agent_history.usage.total_cost if agent_history.usage else 0

            # Collect judge inputs from agent history
            agent_task = task["confirmed_task"]
            final_result = (
                agent_history.final_result() or "Agent did not return a result"
            )
            agent_steps = agent_history.agent_steps()
            ground_truth = task.get("answer")
            screenshots_b64 = encode_screenshots(agent_history.screenshot_paths())

            # Run judge (Claude-based)
            print(f"[{task_id}] Running judge...")
            system_prompt, user_content = construct_judge_messages(
                task=agent_task,
                final_result=final_result,
                agent_steps=agent_steps,
                ground_truth=ground_truth,
                screenshots_b64=screenshots_b64,
            )
            judgement = await invoke_judge(
                system_prompt=system_prompt,
                user_content=user_content,
            )

            score = 1 if judgement.verdict else 0
            print(
                f"Task {task_id} completed: score={score}, verdict={judgement.verdict}"
            )

            # Save trace to run_data/
            if run_data_dir:
                run_data_dir.mkdir(parents=True, exist_ok=True)
                trace = {
                    "agent_task": agent_task,
                    "final_result": final_result,
                    "agent_steps": agent_steps,
                    "ground_truth": ground_truth,
                    "screenshots_b64": screenshots_b64,
                }
                metrics = {"steps": steps, "duration": duration, "cost": cost}
                (run_data_dir / f"{task_id}.json").write_text(
                    json.dumps(
                        {
                            "agent_trace": trace,
                            "metrics": metrics,
                            "judgement": judgement.model_dump(),
                        },
                        indent=2,
                    )
                )

            return {
                "task_id": task_id,
                "score": score,
                "steps": steps,
                "duration": duration,
                "cost": cost,
                "judgement": judgement.model_dump(),
            }

        except Exception as e:
            error_type = type(e).__name__
            error_msg = f"{error_type}: {e}"
            print(f"Task {task.get('task_id', 'unknown')} failed: {error_msg}")
            return {
                "task_id": task.get("task_id"),
                "score": 0,
                "steps": 0,
                "duration": 0,
                "cost": 0,
                "error": error_msg,
                "traceback": traceback.format_exc(),
            }


async def main():
    parser = argparse.ArgumentParser(description="Run benchmark evaluation")
    parser.add_argument(
        "--cli",
        default="playwright-cli",
        choices=list(CLI_BACKENDS.keys()),
        help="CLI browser backend (default: playwright-cli)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        choices=["claude-haiku-4.5", "claude-sonnet-4.6", "claude-opus-4.6"],
        help=f"Claude model for the agent (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--benchmark",
        default="bu-bench",
        choices=list(BENCHMARKS.keys()),
        help="Benchmark to run (default: bu-bench)",
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=None,
        help="Number of tasks to run (default: all)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window)",
    )
    args = parser.parse_args()

    # Resolve CLI backend
    cli_runner_class = CLI_BACKENDS[args.cli]

    # Get framework name/version from the runner class
    framework_name = cli_runner_class.__name__.replace("CliRunner", "CLI")
    framework_version = "unknown"

    # Try to resolve version without starting a browser session
    import shutil
    cli_path = shutil.which(cli_runner_class.BINARY_NAME)
    if cli_path:
        import subprocess
        try:
            ver_result = subprocess.run(
                [cli_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if ver_result.returncode == 0:
                framework_version = ver_result.stdout.strip()
        except Exception:
            pass

    # Build run key and paths
    run_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_key = f"{framework_name}_{framework_version}_model_{args.model}"
    bench_folder = args.benchmark.replace("-", "_")  # bu-bench -> bu_bench
    run_data_dir = (
        Path(__file__).parent / "run_data" / bench_folder / f"{run_key}_start_at_{run_start}"
    )
    results_file = Path(__file__).parent / "results" / bench_folder / f"{run_key}.json"

    tasks = load_tasks(args.benchmark)
    if args.tasks:
        tasks = tasks[: args.tasks]

    print(
        f"Starting evaluation: {len(tasks)} tasks, "
        f"cli={args.cli}, model={args.model}, "
        f"benchmark={args.benchmark}, headless={not args.headed}"
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results = await asyncio.gather(
        *[
            run_task(
                t,
                sem,
                model=args.model,
                cli_runner_class=cli_runner_class,
                headless=not args.headed,
                run_data_dir=run_data_dir,
            )
            for t in tasks
        ]
    )

    # Aggregate metrics
    successful = sum(1 for r in results if r.get("score") == 1)
    total_steps = sum(r.get("steps", 0) for r in results)
    total_duration = sum(r.get("duration", 0) for r in results)
    total_cost = sum(r.get("cost", 0) for r in results)

    # Save results (append to existing runs)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(results_file.read_text()) if results_file.exists() else []
    runs.append(
        {
            "run_start": run_start,
            "tasks_completed": len(results),
            "tasks_successful": successful,
            "total_steps": total_steps,
            "total_duration": total_duration,
            "total_cost": total_cost,
        }
    )
    results_file.write_text(json.dumps(runs, indent=2))

    print(
        f"Run complete: {successful}/{len(results)} tasks successful, "
        f"{total_steps} steps, {total_duration:.1f}s, ${total_cost:.2f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
