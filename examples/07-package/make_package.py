"""Build guide.mdq (and guide.mdq.age, encrypted to a fixed demo key) from src/."""
import os
import zipfile
from pathlib import Path

from mdq import _internal as mi

HERE = Path(__file__).resolve().parent


def build(out_dir: Path = HERE) -> bytes:
    out = out_dir / "guide.mdq"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((HERE / "src").rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(HERE / "src").as_posix())
    data = out.read_bytes()
    # A DEMO identity derived from fixed bytes: never do this for real secrets.
    identity = mi.age_identity(bytes(range(1, 33)))
    (out_dir / "key.txt").write_text("# demo key, do not reuse\n" + identity + "\n")
    sealed = mi.age_encrypt(data, os.urandom(mi.age_random_len(1)), [mi.age_recipient(identity)])
    (out_dir / "guide.mdq.age").write_bytes(sealed)
    return data


if __name__ == "__main__":
    build()
    print("wrote guide.mdq, guide.mdq.age and key.txt")
