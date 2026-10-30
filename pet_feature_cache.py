"""Bounded exact-pixel memoization of fixed pet feature inference only."""
from collections import OrderedDict
import hashlib

MAX_ENTRIES = 64
MAX_PIXEL_BYTES = 8 * 1024 * 1024


def pet_vector(owner, crop, compute):
    pixels = crop.tobytes()
    key = (owner.pet_version, id(owner.pet_model), id(owner.processor),
           tuple(crop.shape), str(crop.dtype), hashlib.sha256(pixels).digest())
    cache = owner.__dict__.setdefault('_exact_pet_vectors', OrderedDict())
    stats = owner.__dict__.setdefault('_exact_pet_stats', {'hits': 0, 'misses': 0})
    entry = cache.get(key)
    if entry is not None and entry[0] == pixels:
        cache.move_to_end(key)
        stats['hits'] += 1
        return list(entry[1])
    stats['misses'] += 1
    vector = list(compute())
    if len(pixels) <= MAX_PIXEL_BYTES:
        cache[key] = (pixels, tuple(vector))
        cache.move_to_end(key)
        while len(cache) > MAX_ENTRIES or sum(len(v[0]) for v in cache.values()) > MAX_PIXEL_BYTES:
            cache.popitem(last=False)
    return list(vector)
