from rc6_storage_policy import GIB, MIB, calculate_storage_decision


def test_measured_droplet_allows_deploy_pre_and_ingest_batch():
    d = calculate_storage_decision(
        total_bytes=24_883_167_232,
        free_bytes=10_698_919_936,
        image_bytes=1_610_000_000,
        mode="deploy-pre",
    )
    assert d.decision == "ALLOW"
    assert d.operational_reserve_bytes == 4_976_633_447
    assert d.build_reserve_bytes == 3 * GIB
    assert d.deploy_pre_required_bytes < 8 * GIB

    i = calculate_storage_decision(
        total_bytes=24_883_167_232,
        free_bytes=10_698_919_936,
        image_bytes=1_610_000_000,
        mode="ingest-start",
        projected_write_bytes=512 * MIB,
    )
    assert i.decision == "ALLOW"
    assert i.ingest_required_bytes == i.deploy_pre_required_bytes + 512 * MIB


def test_prebuild_blocks_while_postbuild_can_still_allow():
    total = 24_883_167_232
    image = 1_610_000_000
    free = 7 * GIB
    pre = calculate_storage_decision(
        total_bytes=total, free_bytes=free, image_bytes=image, mode="deploy-pre"
    )
    post = calculate_storage_decision(
        total_bytes=total, free_bytes=free, image_bytes=image, mode="deploy-post"
    )
    assert pre.decision == "BLOCK"
    assert post.decision == "ALLOW"


def test_ingest_backpressure_preserves_next_deploy():
    base = calculate_storage_decision(
        total_bytes=24_883_167_232,
        free_bytes=10 * GIB,
        image_bytes=1_610_000_000,
        mode="report",
        projected_write_bytes=512 * MIB,
    )
    just_short = base.ingest_required_bytes - 1
    d = calculate_storage_decision(
        total_bytes=base.total_bytes,
        free_bytes=just_short,
        image_bytes=base.image_bytes,
        mode="ingest-continue",
        projected_write_bytes=base.projected_write_bytes,
    )
    assert d.decision == "PAUSE"
    assert d.reason == "ingest_backpressure_preserve_deploy_headroom"


def test_policy_scales_with_filesystem_size():
    d = calculate_storage_decision(
        total_bytes=100 * GIB,
        free_bytes=80 * GIB,
        image_bytes=2 * GIB,
        mode="report",
    )
    assert d.operational_reserve_bytes == 20 * GIB
    assert d.build_reserve_bytes == 4 * GIB
    assert d.hard_stop_bytes == 10 * GIB


def test_large_image_scales_build_reserve():
    d = calculate_storage_decision(
        total_bytes=64 * GIB,
        free_bytes=40 * GIB,
        image_bytes=4 * GIB,
        mode="deploy-pre",
    )
    assert d.build_reserve_bytes == 8 * GIB


def test_hard_stop_fail_closed_by_mode():
    deploy = calculate_storage_decision(
        total_bytes=24 * GIB,
        free_bytes=1 * GIB,
        image_bytes=1 * GIB,
        mode="deploy-post",
    )
    ingest = calculate_storage_decision(
        total_bytes=24 * GIB,
        free_bytes=1 * GIB,
        image_bytes=1 * GIB,
        mode="ingest-continue",
    )
    assert deploy.decision == "BLOCK"
    assert ingest.decision == "PAUSE"


def test_report_is_observation_only_above_validation_layer():
    d = calculate_storage_decision(
        total_bytes=24 * GIB,
        free_bytes=6 * GIB,
        image_bytes=1 * GIB,
        mode="report",
    )
    assert d.decision == "REPORT"
