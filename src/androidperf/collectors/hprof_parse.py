"""Minimal Android hprof binary parser — class histogram only.

Reads UTF8 strings + LOAD_CLASS records to build a class-name map, then walks
HEAP_DUMP / HEAP_DUMP_SEGMENT sub-records counting INSTANCE_DUMP entries.
Does not materialise objects; peak RAM is roughly the hprof file size.
"""

from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

# Value sizes keyed by hprof primitive type tag.
# Type 2 (object reference) is id-size-dependent; resolved at parse time.
_BASE_TYPE_SIZES: dict[int, int] = {4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}

# Heap sub-record fixed-skip table: tag -> (id_count, fixed_bytes).
# Skip size = id_count * id_size + fixed_bytes.
_HEAP_FIXED: dict[int, tuple[int, int]] = {
    0xFF: (1, 0),  # ROOT_UNKNOWN
    0x01: (2, 0),  # ROOT_JNI_GLOBAL
    0x02: (1, 8),  # ROOT_JNI_LOCAL
    0x03: (1, 8),  # ROOT_JAVA_FRAME
    0x04: (1, 4),  # ROOT_NATIVE_STACK
    0x05: (1, 0),  # ROOT_STICKY_CLASS
    0x06: (1, 4),  # ROOT_THREAD_BLOCK
    0x07: (1, 0),  # ROOT_MONITOR_USED
    0x08: (1, 8),  # ROOT_THREAD_OBJECT
    # Android extensions
    0xFE: (1, 4),  # HEAP_DUMP_INFO (4-byte heap type + id name)
    0x89: (1, 0),  # ROOT_INTERNED_STRING
    0x8A: (1, 0),  # ROOT_FINALIZING (deprecated)
    0x8B: (1, 0),  # ROOT_DEBUGGER
    0x8C: (1, 0),  # ROOT_REFERENCE_CLEANUP (deprecated)
    0x8D: (1, 0),  # ROOT_VM_INTERNAL
    0x8E: (1, 8),  # ROOT_JNI_MONITOR
}


def parse_histogram(path: str | Path) -> list[dict[str, Any]]:
    """Return [{class_name, instances, shallow_bytes}] sorted by instances desc."""
    data = Path(path).read_bytes()
    pos = 0

    # Header: null-terminated version string, then u4 id_size, then u8 timestamp.
    null = data.index(b"\x00", pos)
    pos = null + 1
    id_size = struct.unpack_from(">I", data, pos)[0]
    pos += 4 + 8

    id_struct = struct.Struct(">I" if id_size == 4 else ">Q")
    type_sizes = dict(_BASE_TYPE_SIZES)
    type_sizes[2] = id_size  # object reference width = identifier width

    strings: dict[int, str] = {}
    class_names: dict[int, int] = {}   # class_object_id -> name_string_id
    counts: dict[int, int] = defaultdict(int)
    shallow: dict[int, int] = defaultdict(int)

    total = len(data)
    while pos + 9 <= total:
        tag = data[pos]
        pos += 5  # tag(1) + time_offset(4)
        length = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        end = pos + length

        if tag == 0x01:  # UTF8
            sid = id_struct.unpack_from(data, pos)[0]
            strings[sid] = data[pos + id_size: end].decode("utf-8", errors="replace")

        elif tag == 0x02:  # LOAD_CLASS
            pos += 4  # class_serial
            cid = id_struct.unpack_from(data, pos)[0]
            pos += id_size + 4  # class_object_id + stack_serial
            nid = id_struct.unpack_from(data, pos)[0]
            class_names[cid] = nid

        elif tag in (0x0C, 0x1C):  # HEAP_DUMP / HEAP_DUMP_SEGMENT
            pos = _parse_heap(data, pos, end, id_size, id_struct, type_sizes, counts, shallow)

        pos = end

    result = []
    for cid, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        nid = class_names.get(cid)
        raw = strings.get(nid, f"<0x{cid:x}>") if nid is not None else f"<0x{cid:x}>"
        result.append({
            "class_name": raw.replace("/", "."),
            "instances": cnt,
            "shallow_bytes": shallow[cid],
        })
    return result


def _parse_heap(
    data: bytes, pos: int, end: int,
    id_size: int, id_struct: struct.Struct, type_sizes: dict[int, int],
    counts: dict[int, int], shallow: dict[int, int],
) -> int:
    while pos < end:
        sub = data[pos]
        pos += 1

        if sub in _HEAP_FIXED:
            ids, fixed = _HEAP_FIXED[sub]
            pos += ids * id_size + fixed

        elif sub == 0x20:  # CLASS_DUMP — variable length
            pos = _skip_class_dump(data, pos, id_size, type_sizes)

        elif sub == 0x21:  # INSTANCE_DUMP
            pos += id_size + 4  # obj_id + stack_serial
            cid = id_struct.unpack_from(data, pos)[0]
            pos += id_size
            nb = struct.unpack_from(">I", data, pos)[0]
            pos += 4 + nb
            counts[cid] += 1
            shallow[cid] += nb

        elif sub == 0x22:  # OBJECT_ARRAY_DUMP
            pos += id_size + 4  # obj_id + stack
            num = struct.unpack_from(">I", data, pos)[0]
            pos += 4 + id_size + num * id_size  # num + element_class_id + elements

        elif sub == 0x23:  # PRIMITIVE_ARRAY_DUMP
            pos += id_size + 4  # obj_id + stack
            num = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            elem_type = data[pos]
            pos += 1 + num * type_sizes.get(elem_type, 4)

        else:
            # Unknown sub-tag — can't advance safely; skip remainder of segment.
            return end

    return pos


def _skip_class_dump(data: bytes, pos: int, id_size: int, type_sizes: dict[int, int]) -> int:
    pos += id_size + 4 + id_size * 6 + 4  # class_id + stack + (super,loader,signers,domain,r1,r2) + instance_size

    cp_count = struct.unpack_from(">H", data, pos)[0]
    pos += 2
    for _ in range(cp_count):
        pos += 2  # constant pool index
        t = data[pos]
        pos += 1 + type_sizes.get(t, 4)

    sf_count = struct.unpack_from(">H", data, pos)[0]
    pos += 2
    for _ in range(sf_count):
        pos += id_size  # field name string id
        t = data[pos]
        pos += 1 + type_sizes.get(t, 4)  # type + value

    if_count = struct.unpack_from(">H", data, pos)[0]
    pos += 2
    pos += if_count * (id_size + 1)  # each: name_id + type tag (no value)

    return pos
