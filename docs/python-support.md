# Python version support

MAS Lab runs on Python 3.11, 3.12, 3.13 and 3.14. 3.14 is the default. The free-threaded build of 3.14 (`3.14t`) has partial
support: most components run on it, a few do not yet, and the list below says which and why.

The versions are declared once, in `.github/python-support.json`. The workflows, `requires-python` in
every package, the README and this page are checked against that file by a test, so they cannot drift.

## Support levels

| Python | Support | Notes |
| ------ | ------- | ----- |
| 3.11 | full | The oldest version. `requires-python = ">=3.11"` in every package. |
| 3.12 | full | |
| 3.13 | full | |
| 3.14 | full | The default: the version pull requests run on. |
| 3.14t | partial | Free-threaded build. See [Free-threaded build](#free-threaded-build-314t). |

## How and when each version is tested

| When | What runs | Python |
| ---- | --------- | ------ |
| Pull request | Only the jobs whose paths changed, with a coverage gate on the changed lines | 3.14 |
| Push to `main` | The same jobs, and the unit tests again on the oldest version | 3.14, and 3.11 for the unit tests |
| Weekly (Monday) | Unit tests, library tests, end-to-end suites, free-threaded job; and, in a separate run, the MCP and A2A conformance suites | every supported version (the conformance suites run on 3.13) |
| Before a release (a `v*` tag, or publishing from the release workflow) | The same as weekly. A failure blocks the upload to PyPI. | every supported version |

The slow end-to-end suites (controller, lab smoke, golden runs, tutorials, dry-run, human-in-the-loop) run on
the oldest and the newest version only. The unit and library tests run on all of them. The weekly run exists
because a new release of a dependency can break an older Python without any commit here.

What keeps the cost of a pull request flat as versions are added:

- Pull requests run one Python version. Adding a supported version adds jobs to the weekly and release runs only.
- Jobs are selected by changed paths, and a newer push to the same branch cancels the run in progress.
- The dependency cache has one entry per Python version. Only `main` writes it, so a branch cannot evict the
  entries other runs share.
- Every job has a timeout, and the setup step fails if the interpreter is not the one the job asked for
  (`uv venv` would otherwise pick whichever Python it finds first).

## Free-threaded build (3.14t)

The free-threaded build runs threads in parallel, without the global interpreter lock (GIL). We support it
for compatibility. We do not expect it to make MAS Lab faster: the work is mostly waiting for model and tool
calls, and a free-threaded build is slightly slower on single-threaded code.

Two properties make the check meaningful:

- The test job sets `PYTHON_GIL=0` and runs `python -X gil=0`. A C extension that does not declare
  free-threaded support would otherwise switch the GIL back on at import, and the run would pass without
  being free-threaded. With the flag, that import fails.
- Dependencies are installed from wheels only (`--only-binary :all:`). A dependency without a free-threaded
  wheel fails the install instead of compiling from source.

### Components

| Component | 3.14t | Notes |
| --------- | ----- | ----- |
| `runtime` | yes | Passes with the GIL forced off. |
| `ctl`, `library-standard`, `library-ioa` (with `slim`), `library-skills`, `library-telemetry`, `library-kg`, `library-samples`, `lab/components/core` | yes | Pass with the GIL forced off. A few test files need a component that does not install, and are left out (see below). |
| `library-eval` | no | Needs `deepeval`, which needs `grpcio`. `grpcio` has no free-threaded wheel yet. |
| `library-lab`, `lab/components/bench`, `lab/components/controller` | no | They depend on `library-eval`. |
| `library-telemetry[observe]` | no | The extra does not resolve: the SDK's OpenTelemetry pins conflict with the protobuf versions that have free-threaded wheels. |

The measured run (runtime 1444 tests, ctl 1169, library-standard 701, library-ioa 360, library-skills 215,
library-samples 14, lab core 42, plus library-telemetry and library-kg) passed. The tests the job leaves out are
the `not_covered` list in `.github/python-support.json`, each with its reason. They are the test files that import
`lab/components/bench` and the tests of the `[observe]` extra. A test in that list is a gap in coverage, not a pass.

### Threads and context variables

On the free-threaded build, a new thread starts with a copy of the context variables of the thread that created
it; on the other builds it starts empty. The runtime keeps per-call state in context variables, so a thread it
starts from inside a call must not see that call's. The runtime and its libraries start threads with
`mas.runtime.aio.new_thread`, which runs the target in an empty context on every build, so no interpreter setting
is needed. A test fails on any direct `threading.Thread` in the sources. Code that does want the caller's context in
a thread passes it explicitly with `contextvars.copy_context().run`.

### What would lift the remaining limits

- `grpcio` publishing a free-threaded wheel. Until then, run the lab on the regular 3.14 build.
- A first green weekly run of the free-threaded job, after which it can become a required check. Today it
  reports without blocking a release.
- `lab/components/bench` installing on 3.14t, which brings back the tests in the `not_covered` list.

## Changing the supported versions

Edit `.github/python-support.json`, then `requires-python` in the packages, the table above and the README.
`tests/test_ci_python.py` fails until all of them agree. Adding a version needs no workflow edit.
