# Final Semantic Search Performance Report

## Final result

The final test passed. We should move forward with OpenSearch semantic search.

The test used:

- The same application used for the old fuzzy-search test
- 1,530 unique entities
- A 90% match threshold
- Amazon Titan Text Embeddings V2
- 512-dimension vectors
- The Faiss vector engine

Here are the measured times:

| Search type | Time |
| --- | ---: |
| Exact search | Less than 1 second |
| Old RapidFuzz summary | About 20–30 seconds |
| New semantic summary | Less than 5 seconds |

The semantic summary is about 4–6 times faster than the old RapidFuzz
summary. It reduced the wait time by about 75–83%.

This is the final test. It used the final 512-dimension setup. No more
performance testing is required for this decision.

## What we tested

| Item | Test value |
| --- | --- |
| Data available during the test | About 7 million occurrence records |
| Application ID | 2822082 |
| Unique entities in the application | 1,530 |
| Match threshold | 90% |
| Embedding model | Amazon Titan Text Embeddings V2 |
| Vector size | 512 |
| Vector engine | Faiss HNSW |
| Vector comparison | Cosine similarity |

An occurrence record is one place where an entity was found. One entity can
have many occurrence records.

## Simple comparison

| Question | Exact search | RapidFuzz | Semantic search |
| --- | --- | --- | --- |
| How fast was it? | Less than 1 second | About 20–30 seconds | Less than 5 seconds |
| Does it find the same text? | Yes | Yes | Yes |
| Does it handle small spelling changes? | No | Yes | Often |
| Does it handle a different word order? | No | Not well with our current scorer | Yes |
| Does it understand similar meaning? | No | No | Yes |
| Where is matching done? | OpenSearch | OpenSearch and Python | OpenSearch |
| Best use | Exact counts | Optional backup | Main similar-entity search |

Exact search is still the fastest option, but it only finds the same normalized
text. Semantic search finds exact matches and meaning-based matches.

## Easy example

Input names:

Government of United States

United States Government

Output at a 90% threshold:

| Search type | Result | Why |
| --- | --- | --- |
| Exact search | No match | The words are not in the same order |
| Our current RapidFuzz search | No match | Moving the words creates a very low character score |
| Semantic search | Match | Both names have the same meaning |

Our RapidFuzz code uses character changes to calculate the score. Its score for
this example is about 14.81%, so it fails the 90% threshold.

Semantic search compares meaning. It understands that both names point to the
same type of organization even though the words moved.

Semantic search is not perfect. A very short name or acronym can still be hard
to understand. Exact matching should stay in the application so we always have
a reliable exact count.

## Why semantic search is faster

The old RapidFuzz flow repeated a lot of work for every entity.

Old flow:

Application with 1,530 entities
→ Search for possible text matches for every entity
→ Send those names back to Python
→ Calculate RapidFuzz scores in Python
→ Remove results below the threshold
→ Count the remaining records

The semantic flow creates the vector once during seeding.

Seeding flow:

New unique entity text
→ Titan creates one vector
→ OpenSearch saves it in the semantic catalog

Search flow:

Application ID
→ Get its unique entities
→ Get their saved vectors
→ Search the catalog with Faiss
→ Count exact and similar records

The semantic-summary endpoint does not call Titan while the user is waiting.
It reuses vectors already saved in OpenSearch. Titan is called during seeding
when a new unique text is added.

Titan may also be called when a user searches for text that is not already in
the catalog.

## Why we use two indexes

| Index | What it saves | Why we need it |
| --- | --- | --- |
| Occurrence index | Every entity record and its source information | Finds applications, records, locations, and exact counts |
| Semantic catalog | One text and one vector for each unique name | Avoids saving the same vector many times |

Example:

If Ethiopian Airlines appears in 10,000 records, the occurrence index keeps
all 10,000 records. The semantic catalog keeps only one Ethiopian Airlines
vector.

This saves storage and memory. It also keeps the Faiss search graph smaller.

Both indexes use semanticKey to connect their records. The same normalized text
always creates the same SHA-256 semanticKey.

## What the 90% threshold means

RapidFuzz 90% and semantic 90% do not mean the same thing.

- RapidFuzz 90% means the characters are very similar.
- Semantic 90% means the vectors have at least 90% cosine similarity.

The API changes the requested 90% semantic threshold into the OpenSearch score
needed by the cosinesimil setting.

We used 90% for the final test. It is a good starting value. It can be changed
later if real users see too many wrong matches or too many missing matches.

## Why we use 512 dimensions

Each vector has 512 numbers. The older setup used 1,024 numbers.

Using 512 numbers gives us:

- About half the raw vector storage
- Less vector data to move
- Less calculation during vector comparison
- Good results for our short entity names

The complete index will not become exactly half the size because OpenSearch
also saves text, index data, and Faiss graph links.

These four settings must all use 512:

1. Titan connector dimensions
2. OpenSearch model embedding dimension
3. Ingest pipeline output
4. Catalog vector mapping dimension

A 1,024-dimension vector cannot be placed in a 512-dimension field. Old vectors
must be created again with the 512-dimension model.

## Recommended search setup

| Need | Use this search |
| --- | --- |
| Count the exact same entity text | semanticKey exact search |
| Find similar entities for an application | Stored-vector Faiss search |
| Search using text entered by a user | Titan neural text search |
| Run the full RapidFuzz summary | Remove it from the main application page |
| Handle special typo or acronym cases | Add a backup only if real results show it is needed |

## Final decision

Move forward with OpenSearch semantic search.

The final 512-dimension test shows that:

- Exact search stays under 1 second.
- The full semantic summary stays under 5 seconds for 1,530 unique entities.
- The old RapidFuzz summary takes about 20–30 seconds.
- Semantic search finds meaning-based matches that RapidFuzz misses.
- The two-index design avoids saving the same vector many times.

The result is fast enough and gives better matches. The project can continue
with the planned OpenSearch cluster upgrade and semantic-search rollout.
