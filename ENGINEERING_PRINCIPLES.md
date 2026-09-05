# AURA ENGINEERING PRINCIPLES

1. **Evidence Before Modification**: Never modify code based on guessing; reproduce bugs and collect empirical evidence first.
2. **Minimal Blast Radius**: Implement the smallest surgical change that satisfies requirements.
3. **AsyncIO Hygiene**: Always cancel background tasks gracefully and prevent coroutine leaks.
4. **Type Annotations**: Enforce strict Python type annotations and Pydantic validation.
5. **Regression Gate**: No task is complete until full `pytest` suite passes with 0 regressions.
