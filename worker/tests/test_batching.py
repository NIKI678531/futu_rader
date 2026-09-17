import json

from ai.batching import BatchPolicy, pack_items


def pack(items, **limits):
    return pack_items(items, key_of=lambda row: row["group"], payload_of=lambda row: row,
                      system="sys", render=json.dumps, schema={}, policy=BatchPolicy(**limits))


def test_batches_never_mix_groups_and_keep_each_item():
    items = [{"group": group, "id": index} for group in ("3033|25", "3033|24", "2802|25")
             for index in range(11)]
    batches, oversized = pack(items)
    assert not oversized
    assert sum(map(len, batches)) == len(items)
    assert all(len(batch) <= 5 for batch in batches)
    assert all(len({row["group"] for row in batch}) == 1 for batch in batches)


def test_oversized_text_is_not_truncated_or_sent():
    row = {"group": "day", "text": "text" * 1000}
    batches, oversized = pack([row], max_payload_bytes=200)
    assert batches == []
    assert oversized == [row]


def test_system_and_schema_are_included_in_token_limit():
    batches, oversized = pack_items([{"id": 1}], key_of=lambda row: 0, payload_of=lambda row: row,
                                    system="context" * 1000, render=json.dumps, schema={},
                                    policy=BatchPolicy(max_input_tokens=100))
    assert not batches
    assert len(oversized) == 1