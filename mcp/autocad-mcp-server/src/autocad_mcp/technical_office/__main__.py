"""CLI entrypoint for technical office plate jobs."""

from __future__ import annotations

import argparse
import json

from autocad_mcp.technical_office.partlist import create_partlist
from autocad_mcp.technical_office.pipeline import run_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a PDF to DXF/NC1 plate production job.")
    parser.add_argument("job_dir", help="Directory containing input.pdf and optional positions.csv/json")
    parser.add_argument("--output-root", default=None, help="Output directory, defaults to outputs/jobs/<job_id>")
    parser.add_argument(
        "--input-pdf",
        default=None,
        help="Optional PDF filename/path to process. If omitted, input.pdf is preferred, else all PDFs in job_dir are processed.",
    )
    parser.add_argument(
        "--autocad-live-policy",
        default="auto_start_if_needed",
        choices=["auto_start_if_needed", "off"],
        help="Whether to auto-start AutoCAD for live open/zoom/plot validation.",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help="Optional manager-approved project name. When provided, job.json is created/updated.",
    )
    parser.add_argument(
        "--create-partlist",
        action="store_true",
        help="Create an ERT Part_List_holes Excel file after DXF/NC1 production.",
    )
    args = parser.parse_args()
    result = run_job(
        args.job_dir,
        args.output_root,
        autocad_live_policy=args.autocad_live_policy,
        input_pdf=args.input_pdf,
        project_name=args.project_name,
    )
    output = result.to_dict()
    if args.create_partlist:
        partlist = create_partlist(args.job_dir, result.output_dir)
        output["partlist"] = partlist.to_dict()
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
