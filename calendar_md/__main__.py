import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .parser import parse_calendar_markdown, to_json_document


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calendar_md",
        description="Convert markdown economic calendar into JSON format.",
    )
    parser.add_argument("--input", required=True, help="Path to the markdown source file")
    parser.add_argument(
        "--output",
        help="Output JSON file path (omit to print to stdout)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now().year,
        help="Year to apply when normalising dates (default: current year)",
    )
    parser.add_argument(
        "--timezone",
        default="UTC-4",
        help="Timezone description stored under meta.assumptions.time_zone",
    )
    parser.add_argument(
        "--source",
        default="markdown_import",
        help="String stored as meta.source",
    )

    args = parser.parse_args(argv)
    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"Girdi dosyası bulunamadı: {input_path}")

    text = input_path.read_text(encoding="utf-8")
    days = parse_calendar_markdown(text, year=args.year)
    document = to_json_document(
        days,
        year=args.year,
        timezone=args.timezone,
        source=args.source,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
