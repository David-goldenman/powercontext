# Evidence index

`foundation-*/summary.json` is an actual completed foundation reproduction result;
pair it with its `commands.json`, `versions.json`, test outputs and source snapshot.
The newest completed run is preferred. A directory without summary.json is an
incomplete attempt, not successful evidence.

The initial run at foundation-20261102T072831875067Z did not capture the SDK
commit because its Windows worktree metadata was not understood by Linux git.
The version reader was corrected without editing that shared metadata; subsequent
runs include the actual SDK commit, and supersede that version field.

`target-red.txt` inside a completed foundation run is the business feature RED:
five assertion failures caused by the explicitly unsupported new min_priority
behavior, with no dependency/startup errors. It is intentionally still RED.

`tdd-context-initial-red.txt` is earlier developer test-first scaffolding evidence
(the CLI had not yet been created), not the competition's target-feature RED.

`minimax-prepared-*` contains input preparation only, marked api_called=false.
There is no real MiniMax code-generation evidence in this foundation delivery.
Only a future `minimax-real-*` run can supply actual model participation, and it
must be reviewed with the actual public code/diff and unchanged tests.

The first independent pip installation failed because an inherited WSL proxy
timed out. Its raw output remains in independent-venv-install.txt. The next
attempt removed proxy variables for that one process and used official PyPI;
its output is independent-venv-install-direct.txt. Neither attempt changed the
shared Python environment or machine proxy settings. Installation command and
exit results are recorded separately when the attempt completes.

Windows then downloaded Linux CPython 3.12 wheels from official PyPI. Its direct
attempt timed out on a package body; the next attempt used a process-local,
explicitly probed 127.0.0.1:7890 proxy and succeeded. WSL installed the resulting
wheels into this module's independent venv using --no-index. These are actual
package downloads and installations, not copies of shared site-packages.
