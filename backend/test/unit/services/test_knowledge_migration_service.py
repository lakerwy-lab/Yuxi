from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services import knowledge_migration_service as migration


pytestmark = pytest.mark.unit


def test_build_manifest_is_read_only_and_hashes_supported_files(tmp_path, monkeypatch):
    source = tmp_path / "rag-agent"
    source.mkdir()
    document = source / "IT 手册.md"
    document.write_text("# VPN\n", encoding="utf-8")
    (source / "ignored.bin").write_bytes(b"ignored")
    monkeypatch.setenv("RAG_AGENT_MIGRATION_ROOT", str(tmp_path))

    manifest = migration.build_manifest(str(source))

    assert manifest["read_only"] is True
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["sha256"]
    assert document.read_text(encoding="utf-8") == "# VPN\n"


@pytest.mark.asyncio
async def test_dry_run_blocks_missing_target_without_writing(tmp_path, monkeypatch):
    source = tmp_path / "rag-agent"
    source.mkdir()
    (source / "faq.txt").write_text("VPN", encoding="utf-8")
    monkeypatch.setenv("RAG_AGENT_MIGRATION_ROOT", str(tmp_path))

    result = await migration.dry_run_migration(str(source), None)

    assert result["status"] == "blocked"
    assert result["can_import"] is False
    assert any("target_kb_id" in reason for reason in result["reasons"])


@pytest.mark.asyncio
async def test_dry_run_checks_embedding_and_json_source_paths(tmp_path, monkeypatch):
    source = tmp_path / "snapshot.json"
    source.write_text(
        '{"embedding_model_spec": "source-model", "files": [{"filename": "faq.md", "size": 1}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("RAG_AGENT_MIGRATION_ROOT", str(tmp_path))

    async def get_kb_config(_kb_id):
        return SimpleNamespace(embedding_model_spec="target-model")

    monkeypatch.setattr(migration.knowledge_base, "get_kb_config", get_kb_config)

    result = await migration.dry_run_migration(str(source), "kb-1")

    assert result["status"] == "blocked"
    assert any("embedding_model_spec" in reason for reason in result["reasons"])
    assert any("source_path" in reason for reason in result["reasons"])
