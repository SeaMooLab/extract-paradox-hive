"""Command-line entry point for the extract_paradox_hive package.

Enables ``python -m extract_paradox_hive`` by delegating to the package's
``main`` function. This module holds no logic of its own, so the CLI's
behavior is defined in exactly one place.
"""

from extract_paradox_hive.cli import main

if __name__ == "__main__":
    main()