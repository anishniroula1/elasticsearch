# Check IVF-SQ16 storage and memory

Use this guide after copying the catalog into the IVF-SQ16 index.

The examples use these physical index names:

```text
Old IVF Flat index: sentence_semantic_catalog-ivf-v1
New IVF-SQ16 index: sentence_semantic_catalog-ivf-sq16-v1
```

Replace the names if your indexes are different.

## Compare both index sizes

Run:

```http
GET /_cat/indices/sentence_semantic_catalog*?v&bytes=gb&h=index,docs.count,docs.deleted,pri,rep,pri.store.size,store.size&s=index
```

Important columns:

- docs.count shows the number of catalog records. Both indexes should have the
  same number before comparing their sizes.
- pri.store.size shows storage used by primary shards only. Use this column for
  the fairest Flat versus SQ16 comparison.
- store.size includes primary and replica shards.
- docs.deleted shows records waiting for segment merges to reclaim their disk
  space.

For a small test index, show the result in megabytes:

```http
GET /_cat/indices/sentence_semantic_catalog*?v&bytes=mb&h=index,docs.count,pri,rep,pri.store.size,store.size&s=index
```

## Get the exact size of the SQ16 index

Run:

```http
GET /sentence_semantic_catalog-ivf-sq16-v1/_stats/store,docs?filter_path=_all.primaries.docs.count,_all.primaries.store.size_in_bytes,_all.total.store.size_in_bytes
```

The response contains:

```text
_all.primaries.docs.count
    Number of documents in the primary shards.

_all.primaries.store.size_in_bytes
    Exact primary-shard size without replicas.

_all.total.store.size_in_bytes
    Exact size of primary and replica shards together.
```

## Check every shard

Run:

```http
GET /_cat/shards/sentence_semantic_catalog-ivf-sq16-v1?v&bytes=gb&h=index,shard,prirep,docs,store,node&s=shard,prirep
```

Use this result to confirm that:

- Shards are distributed across the data nodes.
- Shards have similar document counts and sizes.
- One node is not holding much more catalog data than the other nodes.

The prirep value is p for a primary shard and r for a replica shard.

## Check native vector memory

Disk storage and k-NN native memory are different. Run:

```http
GET /_plugins/_knn/stats?pretty
```

Look for:

```text
graph_memory_usage
graph_memory_usage_percentage
indices_in_cache
cache_capacity_reached
circuit_breaker_triggered
```

The SQ16 index might not appear in indices_in_cache until it has been searched
or warmed.

## Warm only the new SQ16 index

Run:

```http
GET /_plugins/_knn/warmup/sentence_semantic_catalog-ivf-sq16-v1?pretty
```

The warmup request loads the native Faiss files for the index into memory. Run
the k-NN stats request again after warmup finishes.

Do not warm the old and new catalogs together when the cluster has limited
native memory. Loading both versions can cause cache eviction or trigger the
k-NN circuit breaker.

## Clear only the old index from native memory

If the old IVF Flat index is still loaded, remove only its native-memory cache:

```http
POST /_plugins/_knn/clear_cache/sentence_semantic_catalog-ivf-v1?pretty
```

This does not delete the old index or any documents. A later search against the
old index will load its native files again.

## Understand the result

SQ16 reduces the native vector representation from 32-bit values to 16-bit
values. The vector part can use about half the memory of IVF Flat.

The complete OpenSearch index will not always be 50 percent smaller because it
also contains sentence text, sentence keys, metadata, Lucene files, replicas,
and possibly vector data available through `_source`.

Use these values for the final comparison:

```text
Document count: must match between both indexes
Disk comparison: pri.store.size
Cluster disk usage: store.size
Loaded vector memory: graph_memory_usage after warmup
```

Official OpenSearch references:

- https://docs.opensearch.org/latest/api-reference/index-apis/stats/
- https://docs.opensearch.org/latest/vector-search/api/knn/
- https://docs.opensearch.org/latest/vector-search/optimizing-storage/faiss-16-bit-quantization/
