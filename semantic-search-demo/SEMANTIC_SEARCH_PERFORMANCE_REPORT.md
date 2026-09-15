# Final OpenSearch Entity Search Performance Report

**Status:** Final 512-dimensional test completed. Recommended to proceed with
the OpenSearch semantic-search implementation.

## Executive summary

The semantic vector implementation produced the best complete result for the
tested application:

- The same application used in the earlier fuzzy test contained **1,530 unique
  entities**.
- The previous RapidFuzz summary required approximately **20–30 seconds**.
- The new semantic summary returned the result in **less than 5 seconds**.
- Exact matching remains **under 1 second**.
- Semantic search finds reordered or meaning-equivalent names that the current
  Levenshtein implementation misses.

Using the five-second upper bound, semantic search reduced elapsed time by
approximately **75–83%** compared with the 20–30 second fuzzy summary. It is
roughly **4–6 times faster** while providing a broader kind of matching.

This was the final test using 512-dimensional vectors. The result is strong
enough to approve moving forward with OpenSearch semantic search.

## What was compared

The comparison uses the same application/document from the previous entity
matching test.

| Test input | Value |
| --- | --- |
| Previous data set | About 7 million entity-occurrence records |
| Application used for comparison | `2822082` |
| Unique entities in the application | 1,530 |
| Match threshold | 90% |
| Exact key | SHA-256 `semanticKey` created from normalized entity text |
| Semantic metric | Cosine similarity |
| Vector model | Amazon Titan Text Embeddings V2 |
| Tested vector size | 512 dimensions |
| Vector engine | Faiss HNSW |

The reported semantic timing was measured with the final 512-dimensional
configuration.

## Results side by side

| Capability | Exact search | Previous RapidFuzz summary | Semantic vector summary |
| --- | --- | --- | --- |
| Observed time | Under 1 second | About 20–30 seconds | Under 5 seconds |
| Scope | Identical normalized text | All 1,530 source entities | All 1,530 source entities |
| Match rule | Same `semanticKey` | Character-edit similarity | Meaning and context in the embedding |
| Handles spelling edits | No | Yes | Often; very short text can remain ambiguous |
| Handles reordered words | No | Poorly with the current Levenshtein scorer | Yes in the tested example |
| Handles related wording | No | No | Yes |
| OpenSearch candidate work | Exact keyword lookup | Fuzzy candidate search for every entity | Faiss vector search in batches |
| Application-side scoring | None | Levenshtein score for every candidate | None; OpenSearch applies cosine threshold |
| Best use | Deterministic exact counts | Optional lexical fallback | Main similar-entity workflow |

Exact search is still the fastest operation, but it answers a smaller question.
The semantic summary answers both of these questions in one response:

1. How many exact occurrences exist outside the current application?
2. How many semantically similar occurrences exist outside it?

## Example that shows the quality improvement

Consider these two entity names:

```text
Government of United States
United States Government
```

| Method at a 90% threshold | Result | Reason |
| --- | --- | --- |
| Exact | No match | The normalized strings are different |
| Current RapidFuzz/Levenshtein | No match | Normalized Levenshtein similarity is approximately 14.81% because most characters moved |
| Titan semantic vector | Match | The two names express the same organization despite the different word order |

The previous application uses RapidFuzz's normalized Levenshtein similarity.
RapidFuzz also has token-aware scorers that would handle some word reordering,
but token scoring still does not understand meaning. Semantic embeddings cover
word order, related wording, and many variations through the same search path.

Semantic search does not guarantee that every typographical error will match,
especially for acronyms and very short names. Keeping the exact path and
measuring real false positives and false negatives remains important.

## Why the semantic design performs better

The previous bulk fuzzy summary did expensive work for every source entity:

```text
1,530 application entities
  -> OpenSearch fuzzy candidate searches
  -> candidate text transfer to the API
  -> local Levenshtein calculation
  -> threshold filtering and counting
```

The semantic design prepares the expensive representation once during seeding:

```text
unique entitySearchText
  -> Titan embedding once
  -> one vector in the semantic catalog
```

At query time it reuses stored vectors:

```text
applicationId
  -> unique entities from occurrence index
  -> stored vectors from semantic catalog
  -> Faiss cosine searches in batches of 100
  -> exact and similar occurrence counts
```

Titan is not called for `/semantic-summary`. That removes model-network latency
from the application-summary request. Titan is called during seeding only for a
new unique text, or for a one-off text query that does not already have a
catalog vector.

## Why two indexes remain the right design

| Index | Stored data | Performance purpose |
| --- | --- | --- |
| Occurrence index | Every occurrence, application ID, entity ID, offsets and source data | Fast filtering and exact counts |
| Semantic catalog | One normalized text and vector per unique semantic key | Prevents repeated vectors and keeps the Faiss graph smaller |

Storing a vector on every occurrence would duplicate the same embedding many
times. The catalog makes seeding, storage, graph memory, and semantic searches
depend on unique text count rather than occurrence count.

The link between indexes is deterministic: the same normalized text always
produces the same SHA-256 `semanticKey`.

## Understanding the 90% threshold

The two 90% values are not the same mathematical measurement:

- RapidFuzz 90% means normalized character-edit similarity.
- Semantic 90% means cosine similarity converted to a percentage.

The API correctly configures `cosinesimil` and converts a requested threshold
to OpenSearch's minimum score. Still, 90% must be treated as a business setting,
not a universal truth.

The final comparison used 90%. This is the selected starting threshold for the
application. It can be adjusted later through normal production tuning if user
feedback shows too many missed or incorrect matches.

## Effect of using 512 dimensions

The current mapping uses 512 values instead of 1,024. This approximately halves
the raw vector storage and the numeric work for each distance comparison. The
complete index will not be exactly half the size because HNSW graph links,
Lucene metadata, `_source`, and keyword fields still exist.

All four places must use 512:

1. Titan connector request: `"dimensions": 512`
2. OpenSearch model configuration: `"embedding_dimension": 512`
3. Pipeline output
4. Catalog `knn_vector` mapping: `"dimension": 512`

Existing 1,024-dimensional vectors cannot be mixed with or copied into the new
512-dimensional index. Create a new model configuration and catalog, generate
fresh embeddings, validate them, and then move the alias.

## Recommendation

Proceed with the semantic OpenSearch implementation and use this matching
policy:

| Need | Recommended path |
| --- | --- |
| Exact occurrence count | `semanticKey`/keyword lookup |
| Similar entity summary | Stored-vector Faiss search |
| Search for user-entered text | Titan `neural` query against the catalog |
| Bulk RapidFuzz summary | Retire from the main application page |
| Special typo/acronym fallback | Add only if production feedback shows it is needed |

This recommendation is supported by both measured speed and better result
coverage. Semantic search reduced the full-summary time from 20–30 seconds to
under 5 seconds and matched the tested reordered government name that the
current fuzzy scorer rejected.

## Final decision

The final 512-dimensional test supports moving forward with OpenSearch semantic
search. No additional performance test is required for the decision documented
in this report.

The result is materially faster than the previous full RapidFuzz summary,
retains sub-second exact matching, and adds meaning-based matching that the
character-edit approach cannot provide. The project can proceed with the
planned OpenSearch cluster resize and semantic-search rollout.
