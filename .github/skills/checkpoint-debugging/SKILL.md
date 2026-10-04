---
name: checkpoint-debugging
description: Debug OneDrive and Teams checkpoint, resume, completion, retry, and duplicate-migration issues.
---

# Checkpoint Debugging

## Purpose

Use this skill when migration state does not match the actual migration
result.

Primary focus:

- OneDrive completion problems.
- OneDrive resume/retry problems.

---

# Core Principle

A checkpoint is a record of migration state.

It is not automatically proof that the target data is correct.

Always understand:

```text
What happened
    ↓
What state was recorded
    ↓
Why that state was recorded
    ↓
What the report says
    ↓
What validation says
```
