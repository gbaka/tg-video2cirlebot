import os
import time

import pytest


async def test_job_directories_protect_active_and_cleanup_only_owned(tmp_path):
    from tempfiles import TempFiles

    manager = TempFiles(tmp_path / "jobs")
    foreign = manager.root / "foreign"
    foreign.mkdir()
    async with manager.job() as directory:
        (directory / "input.mp4").write_bytes(b"video")
        old = time.time() - 1000
        os.utime(directory, (old, old))
        assert manager.cleanup(max_age_sec=0) == 0
        assert directory.exists()
    assert not directory.exists()
    abandoned = manager.root / "job-abandoned"
    abandoned.mkdir()
    (abandoned / ".circlebot-owner").write_text("1073741824")
    os.utime(abandoned, (old, old))
    outside = tmp_path / "outside"
    outside.mkdir()
    (manager.root / "job-link").symlink_to(outside, target_is_directory=True)
    assert manager.cleanup(max_age_sec=0) == 1
    assert outside.exists() and foreign.exists()


async def test_cleanup_keeps_unowned_job_prefix(tmp_path):
    from tempfiles import TempFiles
    manager = TempFiles(tmp_path)
    foreign = tmp_path / "job-unrelated"
    foreign.mkdir()
    (foreign / "important.txt").write_text("retain")
    old = time.time() - 1000
    os.utime(foreign, (old, old))
    assert manager.cleanup(max_age_sec=0) == 0
    assert (foreign / "important.txt").read_text() == "retain"


async def test_cleanup_protects_jobs_of_other_manager(tmp_path):
    from tempfiles import TempFiles
    first, second = TempFiles(tmp_path), TempFiles(tmp_path)
    async with first.job() as directory:
        old = time.time() - 1000
        os.utime(directory, (old, old))
        assert second.cleanup(max_age_sec=0) == 0
        assert directory.exists()


async def test_job_directory_removed_on_cancel(tmp_path):
    from tempfiles import TempFiles

    manager = TempFiles(tmp_path / "jobs")
    with pytest.raises(RuntimeError):
        async with manager.job() as directory:
            raise RuntimeError("failure")
    assert not directory.exists()
