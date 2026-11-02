# Query-directed literal evidence

Evidence selection remains opt-in. Every output fragment carries its complete
scope/artifact/revision/entry/version address and original character offsets.
Candidates retain their own source body, preventing same-ID cross-scope text
mixing. Scientific sentence boundaries retain decimals and common abbreviations;
adjacent selections merge with their original whitespace. Oversized sentences
offer overlapping literal windows instead of silently losing fitting evidence.

Offline lexical ranking filters function words, folds common inflections, and
weights within-request query coverage and rarity. Marginal selection includes
the exact escaped UTF-8 JSON content, offsets, separators and citation bytes.
Cached per-source costs avoid repeatedly serializing the whole proposal.
The ordinary prepare path is unchanged.

## Validation

```sh
python -m pytest -q tests/builtin/runtime/test_evidence_selection.py \
  tests/builtin/runtime/test_prepared_context.py
```

On 2026-11-02 the 13 new contracts and 71 existing tests passed (84 total), as
did all 3 published evidence tests. Independent review checked 200 randomized
identity, Unicode/control-character, literal-offset and byte-budget cases.
At 8000 bytes, 16 candidates x100 short sentences took 0.359 seconds in the
review environment after removing a demonstrated >40-second packing regression.

The full published suite initially had 94 passes and one pre-product fixture
checksum failure caused by Windows CRLF checkout conversion. That test passed
with the canonical Git fixture bytes; expected outputs were not changed.
The root development suite was not executed successfully because the isolated
competition environment lacks its development extras. The official 48-request
quality thresholds remain unverified and require remote reevaluation. No
evaluation labels were used to implement or tune this change.
