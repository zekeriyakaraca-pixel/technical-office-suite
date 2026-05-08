from __future__ import annotations

import argparse
import sys

from .codex_bridge import inspect_codex, inspect_codex_mcp
from .config import get_paths
from .orchestrator import AgentOrchestrator
from .tools import ToolRegistry


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print(build_doctor_report())
        return 0
    if args.command == "ask":
        return _ask(args)
    if args.command == "chat":
        return _chat(args)
    if args.command == "job" and args.job_command == "run":
        return _job_run(args)
    if args.command == "agent" and args.agent_command == "draft":
        return _agent_draft(args)
    if args.command == "agent" and args.agent_command == "approve":
        return _agent_approve(args)
    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toffice", description="Technical Office Codex CLI runtime")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="Check Codex CLI readiness")

    ask = sub.add_parser("ask", help="Ask the manager agent one question")
    ask.add_argument("message", nargs="+")

    sub.add_parser("chat", help="Start an interactive manager chat")

    job = sub.add_parser("job", help="Job operations")
    job_sub = job.add_subparsers(dest="job_command")
    job_run = job_sub.add_parser("run", help="Run a job through the deterministic DXF/NC1/QC pipeline")
    job_run.add_argument("job_id")
    job_run.add_argument("--autocad", default="off", choices=["off", "auto_start_if_needed"])

    agent = sub.add_parser("agent", help="Agent draft and approval operations")
    agent_sub = agent.add_subparsers(dest="agent_command")
    draft = agent_sub.add_parser("draft", help="Create an inactive draft agent")
    draft.add_argument("title", nargs="+")
    approve = agent_sub.add_parser("approve", help="Activate an existing draft agent")
    approve.add_argument("draft_id")
    return parser


def build_doctor_report() -> str:
    status = inspect_codex()
    mcp_status = inspect_codex_mcp(get_paths())
    lines = [
        "Technical Office Codex doctor",
        f"Codex executable: {status.executable or 'missing'}",
        f"Codex login: {'ready' if status.login_ok else 'not ready'}",
        f"Codex exec help: {'ready' if status.exec_help_ok else 'not ready'}",
        f"Codex autocad-mcp: {'ready' if mcp_status.points_to_suite else 'not ready'}",
        f"Expected autocad-mcp path: {mcp_status.expected_server_path}",
    ]
    if status.login_output:
        login_lines = [line for line in status.login_output.splitlines() if line.strip()]
        logged_line = next((line for line in login_lines if "logged in" in line.lower()), login_lines[0] if login_lines else "")
        if "could not update path" in logged_line.lower() and status.login_ok:
            logged_line = "ready; Codex reported a PATH update warning"
        lines.append(f"Login status: {logged_line}")
    if status.error:
        lines.append(f"Codex error: {status.error}")
    if mcp_status.error:
        lines.append(f"Codex MCP error: {mcp_status.error}")
    elif not mcp_status.configured:
        lines.append("Codex MCP status: autocad-mcp is not configured in Codex CLI global config.")
    elif mcp_status.stale_path_detected:
        lines.append("Codex MCP status: stale autocad-mcp path detected.")
    elif not mcp_status.points_to_suite:
        lines.append("Codex MCP status: autocad-mcp does not point to this repo.")
    if not mcp_status.points_to_suite and mcp_status.fix_commands:
        lines.append("Codex MCP fix commands (run manually after approval):")
        lines.extend(f"  {command}" for command in mcp_status.fix_commands)
    lines.append("Runtime model: technical-office/codex-cli")
    return "\n".join(lines)


def _ask(args: argparse.Namespace) -> int:
    text = " ".join(args.message)
    result = AgentOrchestrator().run(text)
    print(result.content)
    return 0


def _chat(_args: argparse.Namespace) -> int:
    orchestrator = AgentOrchestrator()
    history: list[dict[str, str]] = []
    print("Technical Office Manager Codex chat. Cikmak icin Ctrl+C veya bos satir.")
    try:
        while True:
            user_text = input("> ").strip()
            if not user_text:
                return 0
            result = orchestrator.run(user_text, history)
            print(result.content)
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": result.content})
    except KeyboardInterrupt:
        print()
        return 0


def _job_run(args: argparse.Namespace) -> int:
    tool = ToolRegistry(get_paths())
    result = tool.run("run_autocad_job", {"job_id": args.job_id, "autocad_live_policy": args.autocad})
    if result.get("ok"):
        print(result["message"])
        return 0
    print(f"Job failed: {result.get('error')}", file=sys.stderr)
    return 2


def _agent_draft(args: argparse.Namespace) -> int:
    title = " ".join(args.title)
    tool = ToolRegistry(get_paths())
    result = tool.run("draft_agent", {"title": title})
    if result.get("ok"):
        print(f"Draft created: {result['draft_id']}")
        print(f"Path: {result['path']}")
        print(f"Approve with: toffice agent approve {result['draft_id']}")
        return 0
    print(f"Draft failed: {result.get('error')}", file=sys.stderr)
    return 2


def _agent_approve(args: argparse.Namespace) -> int:
    tool = ToolRegistry(get_paths(), expose_approval_tool=True)
    result = tool.run("approve_agent", {"draft_id": args.draft_id})
    if result.get("ok"):
        print(f"Agent activated: {result['agent_id']}")
        return 0
    print(f"Approval failed: {result.get('error')}", file=sys.stderr)
    return 2


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
