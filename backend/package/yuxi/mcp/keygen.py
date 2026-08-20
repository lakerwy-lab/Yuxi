"""为项目部署生成并复用持久化 Ed25519 MCP 签名密钥。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PRIVATE_KEY_RELATIVE_PATH = Path("private/private.pem")
PUBLIC_KEY_RELATIVE_PATH = Path("public/public.pem")


def _write_new_file(path: Path, content: bytes, mode: int) -> None:
    """以排他方式创建密钥文件，避免并发启动覆盖既有密钥。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as buffer:
            buffer.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def ensure_mcp_signing_key_pair(root: Path) -> tuple[Path, Path]:
    """创建或校验持久化密钥对，绝不覆盖已存在的私钥。"""

    private_path = root / PRIVATE_KEY_RELATIVE_PATH
    public_path = root / PUBLIC_KEY_RELATIVE_PATH

    if private_path.exists():
        private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise RuntimeError("MCP 私钥不是 Ed25519 PEM")
    else:
        private_key = Ed25519PrivateKey.generate()
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _write_new_file(private_path, private_pem, 0o600)

    expected_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if public_path.exists():
        public_key = serialization.load_pem_public_key(public_path.read_bytes())
        if not isinstance(public_key, Ed25519PublicKey) or public_path.read_bytes() != expected_public:
            raise RuntimeError("MCP 公钥与持久化私钥不匹配")
    else:
        _write_new_file(public_path, expected_public, 0o644)

    return private_path, public_path


def main() -> None:
    """初始化 Compose 共享的 MCP 签名密钥目录。"""

    parser = argparse.ArgumentParser(description="初始化 Yuxi Enterprise MCP Ed25519 密钥")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    private_path, public_path = ensure_mcp_signing_key_pair(args.directory)
    print(
        "MCP signing keys ready: "
        f"{private_path.parent.name}/{private_path.name}, "
        f"{public_path.parent.name}/{public_path.name}"
    )


if __name__ == "__main__":
    main()
