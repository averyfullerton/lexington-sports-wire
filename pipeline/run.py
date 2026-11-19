"""Weekly refresh entry point (Tuesday morning, after Monday Night Football).

uv run python -m pipeline.run
"""

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    raise NotImplementedError("Pipeline steps not implemented yet")


if __name__ == "__main__":
    main()
