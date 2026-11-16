"""Entrypoint: python -m kubetraceback."""

from kubetraceback import config


def main() -> None:
    config.init()

    from kubetraceback.mcp_server import run

    run()


if __name__ == "__main__":
    main()
