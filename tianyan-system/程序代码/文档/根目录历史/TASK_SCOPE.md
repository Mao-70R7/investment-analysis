# Task Scope

## Project Root
E:\AI工作区\投顾数据处理

## Default Rule
Keep search, file reads, and context gathering inside this project root by default.
Treat the current working directory as the active project root for the current thread.
Do not switch to another project just because a different path was mentioned.

## Allowed Within Project
- README.md
- TASK_SCOPE.md
- docs
- src
- scripts
- config
- data
- schemas
- tests
- project-local files that are directly relevant to the task

## External Access
Do not read, search, or scan paths outside this project root unless the user explicitly authorizes it.
A different local path mentioned in the request does not count as authorization by itself.
Before external access, ask for authorization and name the path and reason.
Only treat these as approval:
- the user explicitly says to switch project to a named path
- the user explicitly authorizes access to a named external path
- the user answers yes to a prior authorization question

## Noisy Paths To Avoid Unless Needed
- .git
- node_modules
- dist
- build
- .cache
- browser profile data
- temporary files
- generated artifacts not relevant to the task

## Notes
Program execution, tests, package managers, and network clients may run as needed, but they do not authorize broader filesystem scanning.
