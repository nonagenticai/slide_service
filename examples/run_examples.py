#!/usr/bin/env python3
"""
Run all PowerPoint design and animation examples

This script runs all example files and tests to demonstrate
the capabilities of the PowerPoint design and animation helpers.
"""

import shlex
import subprocess
import sys
from pathlib import Path


def run_command(command, description):
    """Run a command and print its output with a description."""
    print(f"\n\n{'=' * 80}")
    print(f"RUNNING: {description}")
    print(f"{'=' * 80}\n")

    # Run the command (split string into args list to avoid shell=True)
    args = shlex.split(command) if isinstance(command, str) else command
    process = subprocess.run(args, capture_output=True, text=True)  # noqa: S603 -- example runner with hardcoded commands only

    # Print stdout
    if process.stdout:
        print(process.stdout)

    # Print stderr if there was an error
    if process.returncode != 0:
        print("ERROR:")
        print(process.stderr)
        return False

    return True


def main():
    """Run all examples and tests."""
    # Create output directory if it doesn't exist
    output_dir = Path("output_slides")
    output_dir.mkdir(exist_ok=True)

    # Run examples
    examples = [
        ("python enhanced_pptx_example.py", "Basic Design Helpers Example"),
        ("python enhanced_animated_example.py", "Enhanced Animated Example"),
        ("python test_pptx_design_helpers.py", "Design Helpers Test Suite"),
    ]

    success = True
    for command, desc in examples:
        if not run_command(command, desc):
            success = False

    # Print summary of created files
    if success:
        print("\n\n" + "=" * 80)
        print("SUMMARY OF CREATED FILES:")
        print("=" * 80 + "\n")

        print(f"Files in {output_dir}:\n")
        files = list(output_dir.glob("*.pptx"))
        files.extend(output_dir.glob("*.md"))
        files.extend(output_dir.glob("*.json"))

        for file in sorted(files):
            size_kb = file.stat().st_size / 1024
            print(f"  {file.name} ({size_kb:.1f} KB)")

        print("\nAll examples completed successfully!")
        print("\nYou can now open these PowerPoint files to see the results.")
        print("For animated presentations, follow the instructions in the presentation notes")
        print("or in the generated markdown instruction files.")
    else:
        print("\n\nThere were errors running some examples. Please check the output above.")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
