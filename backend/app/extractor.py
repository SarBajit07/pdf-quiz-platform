"""One disposable extraction process; stdout carries only the result JSON."""

import json
import sys
from pathlib import Path

from app.core.config import settings
from app.documents import extract_pages


def main() -> None:
    settings.max_pages = int(sys.argv[2])
    try:
        pages = extract_pages(Path(sys.argv[1]).read_bytes())
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(2)
    except Exception:
        print(json.dumps({"error": "Unable to read PDF for extraction"}))
        sys.exit(1)
    print(json.dumps({"pages": pages}))


if __name__ == "__main__":
    main()
