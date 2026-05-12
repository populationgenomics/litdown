"""CLI: ``python -m jatsdown article.xml [output.md]``."""

import sys

from jatsdown.jats import convert


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    if len(args) < 2:
        print(f'Usage: {args[0]} <article.xml> [output.md]', file=sys.stderr)
        return 1

    md = convert(args[1])

    if len(args) >= 3:
        with open(args[2], 'w') as f:
            f.write(md)
    else:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
