import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sign as signing  # noqa: E402

KEY = signing.FALLBACK_HMAC_KEY
VECTORS = [
    # (prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt)
    ("a cat", "1:1", "anime-v1", None, None, None, None),
    ("猫 在 屋顶", "2:3", "oil-v2", "12345", "30", "7.5", "blurry, low quality"),
    ("multi word prompt", "3:2", "photo-v3", None, "20", None, "ugly&bad"),
    ("emoji 🎨 test", "1:1", "render3d-v1", "0", None, "9", None),
]


def _java_bin() -> str | None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        return None
    return javac


def test_canonical_message_shape():
    msg = signing.canonical_message("a cat", "1:1", "anime-v1")
    assert msg == "prompt=a cat&ratio=1:1&style=anime-v1"
    assert not msg.endswith("&")


def test_canonical_message_full():
    msg = signing.canonical_message("p", "1:1", "s", "9", "20", "7", "np")
    assert msg == "prompt=p&ratio=1:1&style=s&seed_id=9&steps=20&cfg_scale=7&negative_prompt=np"


def test_sign_is_deterministic_and_hex():
    a = signing.sign("a cat", "1:1", "anime-v1")
    b = signing.sign("a cat", "1:1", "anime-v1")
    assert a == b
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)
    assert a != signing.sign("a cat", "1:1", "oil-v2")


def test_sign_key_sensitivity():
    assert signing.sign("p", "1:1", "s", key="k1") != signing.sign("p", "1:1", "s", key="k2")


@pytest.mark.skipif(_java_bin() is None, reason="javac/java not available")
def test_matches_java_replica(tmp_path: Path):
    """与按 Kotlin 源码逐行复刻的 Java 实现交叉校验（含中文/emoji，走 UTF-8 文件避免控制台编码干扰）。"""
    test_dir = Path(__file__).resolve().parent
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(
        [shutil.which("javac"), "-encoding", "UTF-8", "-d", str(classes), str(test_dir / "Sign.java")],
        check=True,
        capture_output=True,
    )
    cases_file = tmp_path / "cases.tsv"
    lines = []
    for prompt, ratio, style, seed_id, steps, cfg, neg in VECTORS:
        lines.append(
            "\t".join(
                [
                    prompt,
                    ratio,
                    style,
                    "-" if seed_id is None else str(seed_id),
                    "-" if steps is None else str(steps),
                    "-" if cfg is None else str(cfg),
                    "-" if neg is None else neg,
                ]
            )
        )
    cases_file.write_text("\n".join(lines) + "\n", "utf-8")
    out = subprocess.run(
        [shutil.which("java"), "-Dfile.encoding=UTF-8", "-cp", str(classes), "Sign", KEY, "--file", str(cases_file)],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8", "replace").strip().splitlines()
    assert len(out) == len(VECTORS), out
    for (prompt, ratio, style, seed_id, steps, cfg, neg), line in zip(VECTORS, out):
        java_msg, java_sig = line.rsplit("\t", 1)
        py_msg = signing.canonical_message(prompt, ratio, style, seed_id, steps, cfg, neg)
        py_sig = signing.sign(prompt, ratio, style, seed_id, steps, cfg, neg)
        assert java_msg == py_msg, (java_msg, py_msg)
        assert java_sig == py_sig, (java_sig, py_sig)


def test_java_missing_signature_case():
    """原始代码里 prompt/ratio/style 为空时拼装仍成立（& 结尾）。"""
    msg = signing.canonical_message("", "1:1", "")
    assert msg == "prompt=&ratio=1:1&style="


def test_dump_vectors_for_docs():
    """把测试向量写进 data/，便于文档与复现。"""
    rows = []
    for prompt, ratio, style, seed_id, steps, cfg, neg in VECTORS:
        rows.append(
            {
                "prompt": prompt,
                "ratio": ratio,
                "style": style,
                "seed_id": seed_id,
                "steps": steps,
                "cfg_scale": cfg,
                "negative_prompt": neg,
                "canonical": signing.canonical_message(prompt, ratio, style, seed_id, steps, cfg, neg),
                "signature": signing.sign(prompt, ratio, style, seed_id, steps, cfg, neg),
            }
        )
    out = Path(__file__).resolve().parent.parent / "data" / "signature_vectors.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"key": KEY, "vectors": rows}, ensure_ascii=False, indent=2), "utf-8")
    assert out.exists()
