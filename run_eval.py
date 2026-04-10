"""Main benchmark evaluation script.

Usage:
    uv run python run_eval.py                                       # defaults: playwright-cli + sonnet
    uv run python run_eval.py --cli playwright-cli --model sonnet
    uv run python run_eval.py --cli agent-browser --headed
    uv run python run_eval.py --tasks 5
    uv run python run_eval.py --benchmark stealth-bench
    uv run python run_eval.py --anthropic-base-url http://localhost:5005

Available CLI backends: browser-use, agent-browser, playwright-cli, playwriter-cli
Available models: haiku, sonnet (default), opus
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
import base64
import hashlib
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

from agent import AgentSDKExecutor, ExecutorConfig
from agent.cli_registry import list_cli_tools
from judge import JudgementResult, construct_judge_messages
from judge_llm import invoke_judge

load_dotenv()

# Configuration
MAX_CONCURRENT = 3
TASK_TIMEOUT = 1800  # 30 minutes max per task
DEFAULT_MODEL = "sonnet"

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
    config: ExecutorConfig,
    run_data_dir: Path | None = None,
) -> dict:
    """Run a single task. Returns result dict with score (0 on failure).

    Args:
        task: Task dict with task_id, confirmed_task, category, answer.
        semaphore: Concurrency limiter.
        config: Executor configuration (CLI tool, model, limits, etc.).
        run_data_dir: Directory for trace output.
    """
    async with semaphore:
        try:
            task_id = str(task.get("task_id", "unknown"))
            print(f"Running task: {task_id}")

            # Create per-task screenshot directory
            task_output_dir = (
                run_data_dir / task_id if run_data_dir else Path(f"run_data_tmp/{task_id}")
            )
            task_screenshot_dir = task_output_dir / "screenshots"

            # Create executor with per-task screenshot dir
            task_config = ExecutorConfig(
                cli_tool_name=config.cli_tool_name,
                max_turns=config.max_turns,
                max_budget_usd=config.max_budget_usd,
                timeout_seconds=config.timeout_seconds,
                model=config.model,
                screenshot_dir=task_screenshot_dir,
                anthropic_base_url=config.anthropic_base_url,
                cli_path=config.cli_path,
                headless=config.headless,
            )

            executor = AgentSDKExecutor(task_config)
            executor._task_id = task_id

            try:
                agent_result = await asyncio.wait_for(
                    executor.execute(task["confirmed_task"]),
                    timeout=TASK_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"Task {task_id} timed out after {TASK_TIMEOUT}s")
                # The executor may have collected steps/screenshots before
                # the timeout.  Salvage what we can from its tracker.
                tracker = executor._tracker
                if tracker.steps:
                    print(
                        f"  Salvaging {len(tracker.steps)} steps, "
                        f"{len(tracker.screenshots)} screenshots from timeout"
                    )
                    agent_result = executor._build_timeout_result(
                        time.monotonic() - TASK_TIMEOUT  # approximate start
                    )
                else:
                    return {
                        "task_id": task_id,
                        "score": 0,
                        "steps": 0,
                        "duration": TASK_TIMEOUT,
                        "cost": 0,
                        "error": f"Task timed out after {TASK_TIMEOUT}s",
                    }

            # Collect task metrics
            steps = agent_result.number_of_steps()
            duration = agent_result.total_duration_seconds()
            cost = agent_result.cost_usd

            # Collect judge inputs
            agent_task = task["confirmed_task"]
            final_result = agent_result.final_result()
            agent_steps = agent_result.agent_steps_for_judge()
            ground_truth = task.get("answer")
            screenshots_b64 = encode_screenshots(agent_result.screenshot_paths())

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
                f"Task {task_id} completed: score={score}, "
                f"verdict={judgement.verdict}, "
                f"captcha={agent_result.captcha_encountered}, "
                f"impossible={agent_result.task_impossible}"
            )

            # Save detailed trace to run_data/
            if run_data_dir:
                task_output_dir.mkdir(parents=True, exist_ok=True)
                trace = {
                    "agent_trace": {
                        "agent_task": agent_task,
                        "final_result": final_result,
                        "agent_steps": agent_steps,
                        "ground_truth": ground_truth,
                        "screenshots_b64": screenshots_b64,
                    },
                    "metrics": {
                        "steps": steps,
                        "duration": duration,
                        "cost": cost,
                        "num_turns": agent_result.num_turns,
                        "token_usage": agent_result.token_usage,
                        "captcha_encountered": agent_result.captcha_encountered,
                        "task_impossible": agent_result.task_impossible,
                    },
                    "judgement": judgement.model_dump(),
                }
                (task_output_dir / f"{task_id}.json").write_text(
                    json.dumps(trace, indent=2, default=str)
                )

            return {
                "task_id": task_id,
                "score": score,
                "steps": steps,
                "duration": duration,
                "cost": cost,
                "num_turns": agent_result.num_turns,
                "captcha_encountered": agent_result.captcha_encountered,
                "task_impossible": agent_result.task_impossible,
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
        choices=list_cli_tools(),
        help="CLI browser backend (default: playwright-cli)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
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
    parser.add_argument(
        "--task-id",
        default=None,
        help="Run a specific task by ID (prefix match supported)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        help="Maximum agentic turns per task (default: 50)",
    )
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=5.0,
        help="Maximum API cost per task in USD (default: 5.0)",
    )
    parser.add_argument(
        "--anthropic-base-url",
        default=os.getenv("ANTHROPIC_BASE_URL"),
        help="Proxy URL for agent maestro desktop (default: ANTHROPIC_BASE_URL env var)",
    )
    parser.add_argument(
        "--cli-path",
        default=None,
        help="Path to Claude Code CLI binary (default: auto-detect)",
    )
    args = parser.parse_args()

    # Build run key and paths
    run_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_key = f"{args.cli}_model_{args.model}"
    bench_folder = args.benchmark.replace("-", "_")  # bu-bench -> bu_bench
    run_data_dir = (
        Path(__file__).parent
        / "run_data"
        / bench_folder
        / f"{run_key}_start_at_{run_start}"
    )
    results_file = (
        Path(__file__).parent / "results" / bench_folder / f"{run_key}.json"
    )

    tasks = load_tasks(args.benchmark)

    # Filter by specific task ID (prefix match)
    if args.task_id:
        tid = args.task_id
        tasks = [t for t in tasks if str(t.get("task_id", "")).startswith(tid)]
        if not tasks:
            print(f"No task found matching ID prefix: {tid}")
            return

    if args.tasks:
        tasks = tasks[: args.tasks]

    # Build executor config
    config = ExecutorConfig(
        cli_tool_name=args.cli,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        timeout_seconds=TASK_TIMEOUT,
        model=args.model,
        anthropic_base_url=args.anthropic_base_url,
        cli_path=args.cli_path,
        headless=not args.headed,
    )

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
                config=config,
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
    total_captcha = sum(1 for r in results if r.get("captcha_encountered"))
    total_impossible = sum(1 for r in results if r.get("task_impossible"))

    # Save results (append to existing runs)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(results_file.read_text()) if results_file.exists() else []
    runs.append(
        {
            "run_start": run_start,
            "cli_tool": args.cli,
            "model": args.model,
            "tasks_completed": len(results),
            "tasks_successful": successful,
            "tasks_captcha": total_captcha,
            "tasks_impossible": total_impossible,
            "total_steps": total_steps,
            "total_duration": total_duration,
            "total_cost": total_cost,
        }
    )
    results_file.write_text(json.dumps(runs, indent=2))

    print(
        f"Run complete: {successful}/{len(results)} tasks successful, "
        f"{total_steps} steps, {total_duration:.1f}s, ${total_cost:.2f}, "
        f"captcha={total_captcha}, impossible={total_impossible}"
    )


if __name__ == "__main__":
    asyncio.run(main())
