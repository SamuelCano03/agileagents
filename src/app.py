"""Entry point for running Agile Agents demos locally.

For now this just forwards to the CLI-based daily stand-up once implemented.
"""

from src.interfaces.cli import main as cli_main


def main() -> None:
    """Main entrypoint used by `python -m src.app`.

    In early versions this simply dispatches to the CLI interface.
    """

    cli_main()


if __name__ == "__main__":  # pragma: no cover
    main()
