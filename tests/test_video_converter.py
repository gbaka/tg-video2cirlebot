import asyncio
import math
import os
import struct
import subprocess
import sys

import pytest

from video_converter import VideoConverter


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=15",
            "-t",
            "1.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


async def test_no_audio_fit_fragment_and_progress(source, tmp_path):
    converter = VideoConverter()
    progress = []
    path, meta = await converter.convert_to_circle(
        str(source),
        resolution=96,
        preset="ultrafast",
        output_path=str(tmp_path / "out.mp4"),
        crop_mode="fit",
        trim_start=0.25,
        trim_duration=0.5,
        progress_callback=progress.append,
    )
    assert meta["width"] == meta["height"] == 96
    assert 0.4 <= meta["duration"] <= 0.7
    assert progress[0] == 0 and progress[-1] == 100
    assert progress == sorted(progress)
    assert any(0 < value < 100 for value in progress)
    assert (await converter.probe(path))["sample_aspect_ratio"] == "1:1"


@pytest.mark.parametrize("cancel", [False, True])
async def test_process_timeout_or_cancel_reaps_sigterm_resistant_child(tmp_path, cancel):
    pidfile = tmp_path / "pid"
    script = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(1)"
    )
    task = asyncio.create_task(
        VideoConverter._process([sys.executable, "-c", script], timeout=0.1, terminate_timeout=0.02)
    )
    for _ in range(100):
        if pidfile.exists():
            break
        await asyncio.sleep(0.002)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize(
    "options",
    [
        {"trim_start": -1},
        {"trim_start": math.nan},
        {"trim_duration": 0},
        {"trim_duration": -1},
        {"trim_start": 2},
        {"crop_mode": "invalid"},
        {"resolution": 95},
        {"crf": 99},
    ],
)
async def test_invalid_encode_options_fail_before_output(source, tmp_path, options):
    output = tmp_path / "invalid.mp4"
    with pytest.raises(ValueError):
        await VideoConverter().convert_to_circle(str(source), output_path=str(output), **options)
    assert not output.exists()


async def test_rotation_sar_probe_and_conversion_are_display_correct(source, tmp_path):
    rotated = tmp_path / "rotated.mp4"
    anamorphic = tmp_path / "sar.mp4"
    # Set the MP4 tkhd rotation matrix directly: portable across old/new FFmpeg,
    # whose handling of the historical rotate metadata flag differs.
    data = bytearray(source.read_bytes())
    matrix_offset = data.index(b"tkhd") + 4 + 40
    data[matrix_offset : matrix_offset + 36] = struct.pack(
        ">9i", 0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824
    )
    rotated.write_bytes(data)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            "setsar=2",
            "-c:v",
            "libx264",
            str(anamorphic),
        ],
        check=True,
    )
    info = await VideoConverter.probe(str(rotated))
    assert abs(info["rotation"]) == 90
    assert info["display_width"] == 90 and info["display_height"] == 160
    for path in (rotated, anamorphic):
        _output, meta = await VideoConverter().convert_to_circle(
            str(path),
            resolution=96,
            preset="ultrafast",
            crop_mode="fit",
            output_path=str(tmp_path / (path.stem + "-out.mp4")),
        )
        assert meta["width"] == meta["height"] == 96
        assert meta["sample_aspect_ratio"] == "1:1"
        assert not meta["rotation"]


async def test_progress_callback_failure_reaps_child(tmp_path):
    pidfile = tmp_path / "callback-pid"

    def broken(_percent):
        raise ValueError("callback failed")

    script = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(pidfile)!r},'w').write(str(os.getpid())); "
        "print('out_time_us=500000',flush=True); time.sleep(2)"
    )
    with pytest.raises(ValueError, match="callback failed"):
        await VideoConverter._process(
            [sys.executable, "-c", script],
            timeout=0.5,
            duration=1,
            progress=broken,
            terminate_timeout=0.02,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


async def test_process_drains_large_unterminated_pipes_with_bounded_retention():
    script = "import os; os.write(1,b'x'*2000000); os.write(2,b'e'*2000000)"
    output = await VideoConverter._process([sys.executable, "-c", script], timeout=2)
    assert len(output) <= 256 * 1024
    assert output == b"x" * len(output)


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 20000),
        ("height", 0),
        ("sample_aspect_ratio", "nan:1"),
        ("sample_aspect_ratio", "100000:1"),
        ("sample_aspect_ratio", "-1:1"),
        ("sample_aspect_ratio", "-1:-1"),
        ("sample_aspect_ratio", "garbage"),
    ],
)
async def test_probe_rejects_resource_exhausting_metadata(monkeypatch, field, value):
    import json

    stream = {
        "codec_type": "video",
        "width": 160,
        "height": 90,
        "sample_aspect_ratio": "1:1",
        field: value,
    }

    async def process(*_args, **_kwargs):
        return json.dumps({"streams": [stream], "format": {"duration": 1}}).encode()

    monkeypatch.setattr(VideoConverter, "_process", staticmethod(process))
    with pytest.raises(ValueError):
        await VideoConverter.probe("local.mp4")


async def test_encode_and_probe_commands_are_resource_and_protocol_bounded(
    source, tmp_path, monkeypatch
):
    commands = []
    original = VideoConverter._process

    async def process(args, **kwargs):
        commands.append(args)
        return await original(args, **kwargs)

    monkeypatch.setattr(VideoConverter, "_process", staticmethod(process))
    await VideoConverter().convert_to_circle(
        str(source), resolution=96, output_path=str(tmp_path / "bounded.mp4")
    )
    for args in commands:
        assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
        assert args[args.index("-threads") + 1] == "1"
    encode = next(args for args in commands if args[0] == "ffmpeg")
    for option, value in {
        "-r": "30",
        "-maxrate": "4M",
        "-bufsize": "8M",
        "-filter_threads": "1",
        "-filter_complex_threads": "1",
    }.items():
        assert encode[encode.index(option) + 1] == value
    assert encode.count("-threads") >= 2


async def test_hanging_callback_does_not_block_timeout_cleanup(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    pidfile = tmp_path / "hanging-callback-pid"

    async def hanging(_percent):
        entered.set()
        await release.wait()

    script = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(pidfile)!r},'w').write(str(os.getpid())); "
        "print('out_time_us=500000',flush=True); "
        "os.write(1,b'x'*2000000); os.write(2,b'e'*2000000); time.sleep(5)"
    )
    task = asyncio.create_task(
        VideoConverter._process(
            [sys.executable, "-c", script],
            timeout=0.1,
            terminate_timeout=0.02,
            progress=hanging,
            duration=1,
        )
    )
    await entered.wait()
    done, _ = await asyncio.wait([task], timeout=0.5)
    completed = bool(done)
    release.set()
    with pytest.raises(TimeoutError):
        await task
    assert completed, "timeout waited for blocked callback instead of draining/reaping"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


async def test_probe_rejects_oversized_normalized_pixel_area(monkeypatch):
    import json

    async def process(*_args, **_kwargs):
        return json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 512,
                        "height": 8192,
                        "sample_aspect_ratio": "16:1",
                    }
                ],
                "format": {"duration": 1},
            }
        ).encode()

    monkeypatch.setattr(VideoConverter, "_process", staticmethod(process))
    with pytest.raises(ValueError):
        await VideoConverter.probe("local.mp4")


async def test_broken_input_never_leaves_output(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    output = tmp_path / "out.mp4"
    with pytest.raises((RuntimeError, ValueError)):
        await VideoConverter().convert_to_circle(str(broken), output_path=str(output))
    assert not output.exists()
