# Contributing

Thank you for helping. The project values three things above all: numbers that are right, explanations that are clear, and code that a careful reader can check.

## Before you start

1. Install with `pip install -e ".[dev]"` and run `pytest`. All tests should pass before you change anything.
2. For anything larger than a small fix, open an issue first and describe the problem you want to solve. A short conversation saves wasted work.

## When you change the maths

- Keep calculations in `openportfolio/analytics` as pure functions: numbers in, numbers out.
- Add a test that checks the result against a value you can work out by hand or cite from a textbook, and show the working in a comment.
- Update `docs/methodology.md` with the method, its assumptions and its limits.

## When you add a number to the screen

Add its label, plain meaning and formula to `openportfolio/glossary.py` and return it with `metric(...)`. No number should reach a user without its meaning.

## Writing style

Code comments, messages and documentation use plain British English. Write short sentences in the active voice, use everyday words, and say exactly what happens. Error messages should state what went wrong and how to fix it.

## Pull requests

- Keep each pull request to one change.
- Explain *why* as well as *what*.
- Make sure `pytest` passes.
