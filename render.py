#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from pipeline.build import build_project


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a JSON storyboard into a stickman explainer video."
    )
    parser.add_argument("project", help="Directory containing project.json and storyboard.json")
    quality = parser.add_mutually_exclusive_group()
    quality.add_argument("--preview", action="store_true", help="Fast low-resolution render")
    quality.add_argument("--final", action="store_true", help="Full-resolution final render")
    parser.add_argument("--mute", action="store_true", help="Skip speech generation and use silence")
    parser.add_argument("--force", action="store_true", help="Ignore render caches")
    args = parser.parse_args()

    selected_quality = "final" if args.final else "preview"
    try:
        report = build_project(
            args.project,
            quality=selected_quality,
            muted=args.mute,
            force=args.force,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
