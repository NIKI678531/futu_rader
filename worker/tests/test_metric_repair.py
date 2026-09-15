from jobs.repair_feed_metrics import recover_share


def test_only_completed_unambiguous_counter_objects_are_recovered():
    assert recover_share('{"common":{"share_count":0},"comments":[')["value"] == 0
    assert recover_share('{"share_count":3}') ["value"] == 3
    assert recover_share('{"common":{"share_count":3') is None
    assert recover_share('{"share_count":3,"comments":[') is None
    assert recover_share('{"common":{"share_count":3},"share_count":9}') is None
    assert recover_share('{"common":{"share_count":true}}') is None
    assert recover_share('{"common":{"share_count":-1}}') is None
    assert recover_share('{"common":{"share_count":3,"share_count":3}}') is None
    assert recover_share('{"content":"share_count: 100"}') is None