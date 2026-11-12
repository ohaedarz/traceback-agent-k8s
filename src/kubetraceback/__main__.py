"""Entrypoint: python -m kubetraceback"""

from kubetraceback import config
from kubetraceback.mcp_server import run


def main() -> None:
    # App init -> env vars loaded -> app ready to process requests.
    config.init()
    run()


if __name__ == "__main__":
    main()