"""CLI: ``python -m litdown article.xml [output.md]``."""

from __future__ import annotations

import sys

import litdown


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    if len(args) < 2:
        print(f'Usage: {args[0]} <article.xml> [output.md]', file=sys.stderr)
        return 1

    md = litdown.convert(args[1])

    if len(args) >= 3:
        with open(args[2], 'w') as f:
            f.write(md)
    else:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
